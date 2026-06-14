"""Primitive interface and registry loader for Dwarf scenarios.

A scenario references primitives by name; this module loads the registry,
validates names and compatibility, and instantiates primitive classes.

Primitive families: setup | load | probe | assertion | fault | teardown.
Runtimes:           library | single-node | devnet.
Targets:            cardano-node | amaru (per scenario target.implementation).

Concrete primitives are implemented in profile_manager.primitives_impl and its
submodules, and registered in dwarf/primitives/registry.json.
"""
import importlib
import json
import os
import subprocess
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from profile_manager import plugin_loader

FAMILIES = frozenset({"setup", "load", "probe", "assertion", "fault", "teardown"})
RUNTIMES = frozenset({"library", "single-node", "devnet"})
DWARF_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RegistryEntry:
    name: str
    module: str
    class_name: str
    version: str
    family: str
    supports: List[str]
    runtimes: List[str]
    params_schema: Optional[str]


def _entries_from_map(primitives_map):
    registry = {}
    for name, entry in primitives_map.items():
        family = entry.get("family")
        if family not in FAMILIES:
            raise ValueError(f"primitive {name}: unknown family {family!r}; expected one of {sorted(FAMILIES)}")
        runtimes = entry.get("runtimes") or []
        unknown = [r for r in runtimes if r not in RUNTIMES]
        if unknown:
            raise ValueError(f"primitive {name}: unknown runtime(s) {unknown}")
        registry[name] = RegistryEntry(
            name=name,
            module=entry["module"],
            class_name=entry["class"],
            version=entry.get("version", "0.0.0"),
            family=family,
            supports=list(entry.get("supports", [])),
            runtimes=list(runtimes),
            params_schema=entry.get("params_schema"),
        )
    return registry


def load_registry(path, *, plugin_roots=None):
    """Load and validate a registry JSON file, returning {name: RegistryEntry}."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    primitives_map = dict(data.get("primitives") or {})
    plugin_manifests = plugin_loader.discover_plugin_manifests(plugin_roots=plugin_roots)
    plugin_entries = plugin_loader.load_plugin_entries(plugin_manifests)
    collisions = sorted(set(primitives_map).intersection(plugin_entries))
    if collisions:
        raise ValueError(f"plugin primitive name collision(s): {collisions}")
    primitives_map.update(plugin_entries)
    return _entries_from_map(primitives_map)


def instantiate(registry, *, name, params, runtime=None, target_implementation=None):
    """Look up a primitive by name, validate compatibility, import, instantiate.

    Raises KeyError for unknown names, ValueError for runtime or target mismatches.
    """
    if name not in registry:
        raise KeyError(f"unknown primitive: {name!r}")
    entry = registry[name]
    if runtime is not None and runtime not in entry.runtimes:
        raise ValueError(
            f"primitive {name!r} does not support runtime {runtime!r}; "
            f"declared runtimes: {entry.runtimes}"
        )
    if target_implementation is not None and target_implementation not in entry.supports:
        raise ValueError(
            f"primitive {name!r} does not support target {target_implementation!r}; "
            f"declared supports: {entry.supports}"
        )
    module = importlib.import_module(entry.module)
    cls = getattr(module, entry.class_name)
    return cls(params=params, entry=entry)


class Primitive:
    """Base for all primitives. Subclasses override the family-specific hook."""

    def __init__(self, *, params=None, entry=None):
        self.params = dict(params or {})
        self.entry = entry


class LoadPrimitive(Primitive):
    """Produces load against the system under test. Runner calls run()."""

    def run(self, handle, rng):
        raise NotImplementedError


class ProbePrimitive(Primitive):
    """Samples some metric during a run. Runner calls sample() periodically, or
    sample_for_input(...) for per-input probes driven by the load primitive."""

    def sample(self, handle):
        raise NotImplementedError

    def sample_for_input(self, handle, input_id, outcome):
        """Called once per load input when the primitive is declared `on: every_input`."""
        raise NotImplementedError


class AssertionPrimitive(Primitive):
    """Evaluates one assertion after load completes. Runner calls evaluate()."""

    def evaluate(self, handle):
        raise NotImplementedError


def _events_from_handle(handle, *, phase=None, event=None, primitive=None):
    if hasattr(handle, "events"):
        events = list(getattr(handle, "events"))
    else:
        events = []
        log_path = Path(handle.run_dir) / "log.ndjson"
        if log_path.exists():
            for line in log_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    filtered = []
    for entry in events:
        if phase is not None and entry.get("phase") != phase:
            continue
        if event is not None and entry.get("event") != event:
            continue
        if primitive is not None and entry.get("primitive") != primitive:
            continue
        filtered.append(entry)
    return filtered


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _append_target_hook_event(handle, *, primitive: str, event: str, payload: dict, level: str = "info") -> None:
    run_dir = getattr(handle, "run_dir", None)
    if run_dir is None:
        return
    path = Path(run_dir) / "events" / "target-hooks.ndjson"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _utc_now_iso(),
        "phase": "runtime",
        "primitive": primitive,
        "level": level,
        "event": event,
        "payload": payload,
    }
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Target manifests and the cbor_fuzz_target primitive
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetManifest:
    id: str
    binary: str
    input_format: str
    implementation: str
    language: str
    upstream_commit: str
    invariants: List[str]


def _resolve_runtime_path(path):
    """Resolve paths authored from either the ada2 root or the Dwarf root."""
    candidate = Path(path)
    if candidate.is_absolute():
        if candidate.exists():
            return candidate
        dwarf_marker = ("dwarf",)
        if dwarf_marker[0] in candidate.parts:
            marker_index = candidate.parts.index(dwarf_marker[0])
            suffix = candidate.parts[marker_index + 1:]
            return DWARF_ROOT.joinpath(*suffix)
        return candidate
    if candidate.exists():
        return candidate
    if candidate.parts and candidate.parts[0] == "dwarf":
        return DWARF_ROOT.joinpath(*candidate.parts[1:])
    return candidate


def _resolve_aflnet_target_binary_path(path) -> Path:
    candidate = _resolve_runtime_path(path)
    if candidate.exists():
        return candidate

    path_obj = Path(path)
    parts = path_obj.parts
    if "targets" not in parts:
        return candidate

    suffix = Path(*parts[parts.index("targets"):])
    candidate_roots = [
        DWARF_ROOT,
        Path.home() / "ada2-docker-proof" / "dwarf",
        Path.home() / "dwarf-framework-stack" / "dwarf",
        Path.home() / "dwarf-fw-041",
    ]
    for root in candidate_roots:
        alt = root / suffix
        if alt.exists():
            return alt
    return candidate


def _build_dwarf_telemetry_env(handle):
    env = os.environ.copy()
    run_dir = getattr(handle, "run_dir", None)
    if run_dir is None:
        return env
    run_dir = Path(run_dir)
    events_dir = run_dir / "events"
    metrics_dir = run_dir / "metrics"
    runtime_metrics_dir = metrics_dir / "runtime"
    target_event_log = events_dir / "target-hooks.ndjson"
    env.update({
        "ADA2_DWARF_RUN_DIR": str(run_dir),
        "ADA2_DWARF_EVENTS_DIR": str(events_dir),
        "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
        "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
        "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
    })
    return env


def _ensure_cargo_path(env: dict[str, str]) -> dict[str, str]:
    cargo_bin = Path.home() / ".cargo" / "bin"
    cargo = cargo_bin / "cargo"
    if cargo.exists():
        path_value = env.get("PATH", "")
        entries = [entry for entry in path_value.split(os.pathsep) if entry]
        cargo_bin_str = str(cargo_bin)
        if cargo_bin_str not in entries:
            env = dict(env)
            env["PATH"] = os.pathsep.join([cargo_bin_str, *entries]) if entries else cargo_bin_str
    return env


def _ensure_user_local_bin_path(env: dict[str, str]) -> dict[str, str]:
    local_bin = Path.home() / ".local" / "bin"
    if local_bin.exists():
        path_value = env.get("PATH", "")
        entries = [entry for entry in path_value.split(os.pathsep) if entry]
        local_bin_str = str(local_bin)
        if local_bin_str not in entries:
            env = dict(env)
            env["PATH"] = os.pathsep.join([local_bin_str, *entries]) if entries else local_bin_str
    return env


@contextmanager
def _temporary_workspace_root_manifest_move_aside(working_dir: Path):
    repo_root = DWARF_ROOT
    root_manifest = repo_root / "Cargo.toml"
    if not root_manifest.exists():
        yield False
        return
    try:
        working_dir.relative_to(repo_root)
    except ValueError:
        yield False
        return
    backup_manifest = repo_root / "Cargo.toml.dwarf-aflpp.bak"
    if backup_manifest.exists():
        raise FileExistsError(f"temporary backup already exists: {backup_manifest}")
    root_manifest.rename(backup_manifest)
    try:
        yield True
    finally:
        if backup_manifest.exists():
            backup_manifest.rename(root_manifest)


def _count_files(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for candidate in path.rglob("*") if candidate.is_file())


def _parse_aflpp_fuzzer_stats(path: Path) -> dict:
    if not path.is_file():
        return {}
    stats = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if not key or not value:
            continue
        try:
            if "." in value:
                stats[key] = float(value)
            else:
                stats[key] = int(value)
        except ValueError:
            stats[key] = value
    return stats


def _decode_process_output(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _summarize_aflpp_artifacts(default_dir: Path) -> dict:
    queue_dir = default_dir / "queue"
    crashes_dir = default_dir / "crashes"
    hangs_dir = default_dir / "hangs"
    return {
        "default_dir": str(default_dir),
        "queue_count": _count_files(queue_dir),
        "crash_count": _count_files(crashes_dir),
        "hang_count": _count_files(hangs_dir),
        "has_queue_dir": queue_dir.is_dir(),
        "has_crashes_dir": crashes_dir.is_dir(),
        "has_hangs_dir": hangs_dir.is_dir(),
        "has_fuzzer_stats": (default_dir / "fuzzer_stats").is_file(),
        "has_plot_data": (default_dir / "plot_data").is_file(),
    }


def _read_aflpp_result_artifacts(handle, *, output_dir: Path) -> tuple[dict, dict]:
    bundle_default_dir = None
    run_dir = getattr(handle, "run_dir", None)
    if run_dir is not None:
        bundle_default_dir = Path(run_dir) / "outputs" / "aflpp" / "default"
    default_dir = bundle_default_dir if bundle_default_dir and bundle_default_dir.exists() else output_dir / "default"
    artifact_summary = _summarize_aflpp_artifacts(default_dir)
    stats = _parse_aflpp_fuzzer_stats(default_dir / "fuzzer_stats")
    return artifact_summary, stats


def _resolve_output_path(handle, path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    run_dir = getattr(handle, "run_dir", None)
    if run_dir is not None:
        return Path(run_dir) / candidate
    return candidate


def build_runtime_aflpp_campaign_command(
    *,
    working_dir: Path,
    bin_name: str,
    seed_dirs: list[Path],
    output_dir: Path,
    seconds: int,
    target_implementation: str,
    replay_harness: str,
    replay_target_id: str,
    replay_targets: list[str],
    dict_path: Path | None = None,
    sanitizer: str | None = None,
    target_triple: str | None = None,
    afl_mode: str | None = None,
    target_binary_path: Path | None = None,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "aflpp_campaign.py"),
        "--working-dir", str(working_dir),
        "--bin", bin_name,
    ]
    for seed_dir in seed_dirs:
        command.extend(["--seed-dir", str(seed_dir)])
    command.extend([
        "--output-dir", str(output_dir),
        "--seconds", str(seconds),
    ])
    if dict_path is not None:
        command.extend(["--dict-path", str(dict_path)])
    if sanitizer is not None:
        command.extend(["--sanitizer", sanitizer])
    if target_triple is not None:
        command.extend(["--target-triple", target_triple])
    if afl_mode is not None:
        command.extend(["--afl-mode", afl_mode])
    if target_binary_path is not None:
        command.extend(["--target-binary-path", str(target_binary_path)])
    command.extend([
        "--target-implementation", target_implementation,
        "--replay-harness", replay_harness,
        "--replay-target-id", replay_target_id,
    ])
    for replay_target in replay_targets:
        command.extend(["--replay-target", replay_target])
    return command


def build_runtime_custom_mutator_template_command(
    *,
    working_dir: Path,
    fuzz_dir: Path,
    target_name: str,
    seed_dirs: list[Path],
    output_dir: Path,
    seconds: int,
    target_implementation: str,
    replay_harness: str,
    replay_target_id: str,
    replay_targets: list[str],
    toolchain: str | None = None,
    dict_path: Path | None = None,
    extra_libfuzzer_args: list[str] | None = None,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_custom_mutator_template.py"),
        "--working-dir", str(working_dir),
        "--fuzz-dir", str(fuzz_dir),
        "--target-name", target_name,
    ]
    for seed_dir in seed_dirs:
        command.extend(["--seed-dir", str(seed_dir)])
    command.extend([
        "--output-dir", str(output_dir),
        "--seconds", str(seconds),
    ])
    if toolchain is not None:
        command.extend(["--toolchain", toolchain])
    if dict_path is not None:
        command.extend(["--dict-path", str(dict_path)])
    for arg in extra_libfuzzer_args or []:
        command.append(f"--libfuzzer-arg={arg}")
    command.extend([
        "--target-implementation", target_implementation,
        "--replay-harness", replay_harness,
        "--replay-target-id", replay_target_id,
    ])
    for replay_target in replay_targets:
        command.extend(["--replay-target", replay_target])
    return command


def build_runtime_differential_rule_harness_command(
    *,
    binary_path: Path,
    input_path: Path,
    output_dir: Path,
    target_implementation: str,
    reference_implementation: str,
) -> list[str]:
    return [
        str(binary_path),
        "--input-path",
        str(input_path),
        "--output-dir",
        str(output_dir),
        "--target-implementation",
        target_implementation,
        "--reference-implementation",
        reference_implementation,
    ]


def build_runtime_fuzz_campaign_command(*, config_path: Path) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "fuzz_campaign_orchestrator.py"),
        "--config",
        str(config_path),
    ]


def build_runtime_long_campaign_command(*, config_path: Path) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "long_campaign_orchestrator.py"),
        "--config",
        str(config_path),
    ]


def build_runtime_persistent_campaign_command(*, config_path: Path) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_persistent_campaign.py"),
        "--config",
        str(config_path),
    ]


def build_runtime_afl_stability_command(*, config_path: Path) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_afl_stability.py"),
        "--config",
        str(config_path),
    ]


def build_runtime_fuzz_env_setup_command(*, output_dir: Path, nightly_toolchain: str) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_fuzz_env_setup.py"),
        "--output-dir",
        str(output_dir),
        "--nightly-toolchain",
        nightly_toolchain,
    ]


def build_runtime_miri_campaign_command(
    *,
    repo_dir: Path,
    packages: list[str],
    output_dir: Path,
    toolchain: str,
    miriflags: list[str],
    test_filter: str | None,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_miri_campaign.py"),
        "--repo-dir",
        str(repo_dir),
        "--output-dir",
        str(output_dir),
        "--toolchain",
        toolchain,
    ]
    for package in packages:
        command.extend(["--package", package])
    for flag in miriflags:
        command.append(f"--miri-flag={flag}")
    if test_filter:
        command.extend(["--test-filter", test_filter])
    return command


def build_runtime_proptest_campaign_command(
    *,
    repo_dir: Path,
    checks: list[dict[str, Any]],
    output_dir: Path,
    cases: int,
    toolchain: str | None,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_proptest_campaign.py"),
        "--repo-dir",
        str(repo_dir),
        "--output-dir",
        str(output_dir),
        "--cases",
        str(cases),
    ]
    if toolchain:
        command.extend(["--toolchain", toolchain])
    for check in checks:
        command.extend(["--check-json", json.dumps(check, sort_keys=True)])
    return command


def build_runtime_credential_ceremony_command(
    *,
    output_dir: Path,
    pool_count: int,
    testnet_magic: int,
    kes_period_window: int,
    deterministic_seed: str | None,
    cardano_testnet_bin: str | None,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_credential_ceremony.py"),
        "--output-dir",
        str(output_dir),
        "--pool-count",
        str(pool_count),
        "--testnet-magic",
        str(testnet_magic),
        "--kes-period-window",
        str(kes_period_window),
    ]
    if deterministic_seed:
        command.extend(["--deterministic-seed", deterministic_seed])
    if cardano_testnet_bin:
        command.extend(["--cardano-testnet-bin", cardano_testnet_bin])
    return command


def build_runtime_amaru_proptest_oracle_command(
    *,
    repo_dir: Path,
    target_subcrate: str,
    fixture_filter: str | None,
    corpus_size: int,
    output_dir: Path,
    toolchain: str | None,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_amaru_proptest_oracle.py"),
        "--repo-dir",
        str(repo_dir),
        "--target-subcrate",
        target_subcrate,
        "--corpus-size",
        str(corpus_size),
        "--output-dir",
        str(output_dir),
    ]
    if fixture_filter:
        command.extend(["--fixture-filter", fixture_filter])
    if toolchain:
        command.extend(["--toolchain", toolchain])
    return command


def build_runtime_aflnet_campaign_command(
    *,
    aflnet_dir: Path,
    target_binary_path: Path,
    state_corpus: Path,
    output_dir: Path,
    seconds: int,
    port: int,
    protocol: str,
    startup_wait_usec: int,
    server_script_path: Path | None = None,
    server_binary_path: Path | None = None,
    use_dumb_mode: bool = True,
    timeout_seconds: float | None = None,
    min_execs_done: int | None = None,
    min_sessions: int | None = None,
    min_plot_data_rows: int | None = None,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_aflnet_campaign.py"),
        "--aflnet-dir",
        str(aflnet_dir),
        "--target-binary-path",
        str(target_binary_path),
        "--state-corpus",
        str(state_corpus),
        "--output-dir",
        str(output_dir),
        "--seconds",
        str(seconds),
        "--port",
        str(port),
        "--protocol",
        protocol,
        "--startup-wait-usec",
        str(startup_wait_usec),
    ]
    if server_script_path is not None:
        command.extend(["--server-script-path", str(server_script_path)])
    if server_binary_path is not None:
        command.extend(["--server-binary-path", str(server_binary_path)])
    if not use_dumb_mode:
        command.append("--no-dumb-mode")
    if timeout_seconds is not None:
        command.extend(["--timeout-seconds", str(float(timeout_seconds))])
    if min_execs_done is not None:
        command.extend(["--min-execs-done", str(int(min_execs_done))])
    if min_sessions is not None:
        command.extend(["--min-sessions", str(int(min_sessions))])
    if min_plot_data_rows is not None:
        command.extend(["--min-plot-data-rows", str(int(min_plot_data_rows))])
    return command


def build_runtime_symbolic_execution_campaign_command(
    *,
    python_path: Path,
    target_binary_path: Path,
    output_dir: Path,
    input_size_bytes: int,
    max_steps: int,
    max_generated_inputs: int,
) -> list[str]:
    return [
        str(python_path),
        str(DWARF_ROOT / "scripts" / "runtime_symbolic_execution_campaign.py"),
        "--target-binary-path",
        str(target_binary_path),
        "--output-dir",
        str(output_dir),
        "--input-size-bytes",
        str(input_size_bytes),
        "--max-steps",
        str(max_steps),
        "--max-generated-inputs",
        str(max_generated_inputs),
    ]


def build_runtime_cargo_mutants_campaign_command(
    *,
    repo_dir: Path,
    file: str,
    output_dir: Path,
    package: str | None,
    jobs: int,
    timeout: int,
    baseline: str,
    toolchain: str | None,
    no_config: bool,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_cargo_mutants_campaign.py"),
        "--repo-dir",
        str(repo_dir),
        "--file",
        file,
        "--output-dir",
        str(output_dir),
        "--jobs",
        str(jobs),
        "--timeout",
        str(timeout),
        "--baseline",
        baseline,
    ]
    if package:
        command.extend(["--package", package])
    if toolchain:
        command.extend(["--toolchain", toolchain])
    if no_config:
        command.append("--no-config")
    return command


def build_runtime_snapshot_substrate_command(*, config_path: Path, mode: str) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_snapshot_substrate.py"),
        "--config",
        str(config_path),
        "--mode",
        mode,
    ]


def build_runtime_substrate_checkpoint_command(*, config_path: Path, mode: str) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_substrate_checkpoint.py"),
        "--config",
        str(config_path),
        "--mode",
        mode,
    ]


def build_runtime_install_version_command(*, config_path: Path) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_install_version.py"),
        "--config",
        str(config_path),
    ]


def build_runtime_compose_substrate_command(*, config_path: Path) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_compose_substrate.py"),
        "--config",
        str(config_path),
    ]


def build_runtime_teardown_substrate_command(*, config_path: Path) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_teardown_substrate.py"),
        "--config",
        str(config_path),
    ]


def build_runtime_byzantine_peer_command(*, config_path: Path, mode: str) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_byzantine_peer.py"),
        "--config",
        str(config_path),
        "--mode",
        mode,
    ]


def build_runtime_byzantine_cardano_node_command(*, config_path: Path, mode: str) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_byzantine_cardano_node.py"),
        "--config",
        str(config_path),
        "--mode",
        mode,
    ]


def build_runtime_chainsync_blockfetch_fault_command(*, config_path: Path, mode: str) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_chainsync_blockfetch_fault.py"),
        "--config",
        str(config_path),
        "--mode",
        mode,
    ]


def build_runtime_txsubmission_probe_command(*, config_path: Path, mode: str) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_txsubmission_probe.py"),
        "--config",
        str(config_path),
        "--mode",
        mode,
    ]


def build_runtime_recovery_fault_command(*, config_path: Path, mode: str) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_recovery_fault.py"),
        "--config",
        str(config_path),
        "--mode",
        mode,
    ]


def build_runtime_protocol_fault_command(*, config_path: Path, mode: str) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_protocol_fault.py"),
        "--config",
        str(config_path),
        "--mode",
        mode,
    ]


def build_runtime_topology_fault_command(*, config_path: Path, mode: str) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_topology_fault.py"),
        "--config",
        str(config_path),
        "--mode",
        mode,
    ]


def build_runtime_resource_abuse_fault_command(*, config_path: Path, mode: str) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_resource_abuse_fault.py"),
        "--config",
        str(config_path),
        "--mode",
        mode,
    ]


def build_runtime_network_impairment_command(*, config_path: Path) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_network_impairment.py"),
        "--config",
        str(config_path),
    ]


def build_runtime_time_skew_command(*, config_path: Path) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_time_skew.py"),
        "--config",
        str(config_path),
        "--mode",
        "apply",
    ]


def build_runtime_exposure_probe_command(*, config_path: Path, mode: str) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_exposure_probe.py"),
        "--config",
        str(config_path),
        "--mode",
        mode,
    ]


def build_runtime_hardening_probe_command(*, config_path: Path, mode: str) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_hardening_probe.py"),
        "--config",
        str(config_path),
        "--mode",
        mode,
    ]


def build_runtime_plutus_phase2_probe_command(*, config_path: Path, mode: str) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_plutus_phase2_probe.py"),
        "--config",
        str(config_path),
        "--mode",
        mode,
    ]


def build_runtime_epoch_boundary_probe_command(*, config_path: Path, mode: str) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_epoch_boundary_probe.py"),
        "--config",
        str(config_path),
        "--mode",
        mode,
    ]


def build_runtime_force_hf_boundary_command(*, config_path: Path) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_force_hf_boundary.py"),
        "--config",
        str(config_path),
    ]


def build_runtime_simulate_era_transition_command(*, config_path: Path) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_simulate_era_transition.py"),
        "--config",
        str(config_path),
    ]


def build_runtime_genesis_mode_simulate_command(*, config_path: Path) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_genesis_mode_simulate.py"),
        "--config",
        str(config_path),
    ]


def build_runtime_aggregate_coverage_command(
    *,
    runs_root: Path,
    output_dir: Path,
    bundle_ids: list[str],
    campaign_bundle_ids: list[str],
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "aggregate_coverage.py"),
        "--runs-root",
        str(runs_root),
        "--output-dir",
        str(output_dir),
    ]
    for bundle_id in bundle_ids:
        command.extend(["--bundle-id", bundle_id])
    for bundle_id in campaign_bundle_ids:
        command.extend(["--campaign-bundle-id", bundle_id])
    return command


def build_runtime_execution_trace_differential_command(
    *,
    protocol: str,
    corpus_dir: Path,
    output_dir: Path,
    differential_binary: Path | None = None,
    corpus_size: int | None = None,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_execution_trace_differential.py"),
        "--protocol",
        str(protocol),
        "--corpus-dir",
        str(corpus_dir),
        "--output-dir",
        str(output_dir),
    ]
    if differential_binary is not None:
        command.extend(["--differential-binary", str(differential_binary)])
    if corpus_size is not None:
        command.extend(["--corpus-size", str(int(corpus_size))])
    return command


def build_runtime_crash_triage_command(
    *,
    bundle_dir: Path,
    output_dir: Path,
    minimizer: dict | None,
    target_binary: Path | None = None,
    target_args: list[str] | None = None,
    triage_env: dict | None = None,
    signature_frames: int | None = None,
    timeout_seconds: float | None = None,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_crash_triage.py"),
        "--bundle-dir",
        str(bundle_dir),
        "--output-dir",
        str(output_dir),
    ]
    if minimizer:
        command.extend(["--minimizer-json", json.dumps(minimizer, sort_keys=True)])
    if target_binary is not None:
        command.extend(["--target-binary", str(target_binary)])
    for arg in target_args or []:
        command.extend(["--target-arg", str(arg)])
    if triage_env:
        command.extend(["--triage-env-json", json.dumps(triage_env, sort_keys=True)])
    if signature_frames is not None:
        command.extend(["--signature-frames", str(int(signature_frames))])
    if timeout_seconds is not None:
        command.extend(["--timeout-seconds", str(float(timeout_seconds))])
    return command


def build_runtime_afl_corpus_min_command(
    *,
    queue_dir: Path,
    output_dir: Path,
    target_binary: Path,
    target_args: list[str] | None = None,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_afl_corpus_min.py"),
        "--queue-dir",
        str(queue_dir),
        "--output-dir",
        str(output_dir),
        "--target-binary",
        str(target_binary),
    ]
    for arg in target_args or []:
        command.extend(["--target-arg", arg])
    return command


def build_runtime_coverage_report_command(
    *,
    runs_dir: Path,
    aggregate_bundle_id: str | None,
    output_dir: Path,
    merge_mode: str = "stat-only",
    aflpp_bundle_ids: list[str] | None = None,
    cargo_fuzz_campaign_bundle_ids: list[str] | None = None,
    max_inputs_per_bundle: int = 25,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "coverage_report.py"),
        "--runs-dir",
        str(runs_dir),
        "--output-dir",
        str(output_dir),
        "--merge-mode",
        merge_mode,
    ]
    if aggregate_bundle_id:
        command.extend(["--aggregate-bundle-id", aggregate_bundle_id])
    for bundle_id in aflpp_bundle_ids or []:
        command.extend(["--aflpp-bundle-id", bundle_id])
    for bundle_id in cargo_fuzz_campaign_bundle_ids or []:
        command.extend(["--cargo-fuzz-campaign-bundle-id", bundle_id])
    if merge_mode == "file-level":
        command.extend(["--max-inputs-per-bundle", str(int(max_inputs_per_bundle))])
    return command


def build_runtime_corpus_health_report_command(
    *,
    runs_root: Path,
    scenario_id_contains: str,
    output_dir: Path,
) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_corpus_health_report.py"),
        "--runs-root",
        str(runs_root),
        "--scenario-id-contains",
        scenario_id_contains,
        "--output-dir",
        str(output_dir),
    ]


def build_runtime_bundle_attestation_command(
    *,
    output_dir: Path,
    signing_actor: str,
) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_bundle_attestation.py"),
        "--output-dir",
        str(output_dir),
        "--signing-actor",
        signing_actor,
    ]


def build_runtime_bundle_timeline_command(
    *,
    runs_dir: Path,
    bundle_ids: list[str],
    output_dir: Path,
    scenario_id_filters: list[str] | None = None,
    signature_token_filters: list[str] | None = None,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_bundle_timeline.py"),
        "--runs-dir",
        str(runs_dir),
        "--output-dir",
        str(output_dir),
    ]
    for bundle_id in bundle_ids:
        command.extend(["--bundle-id", bundle_id])
    for scenario_id in scenario_id_filters or []:
        command.extend(["--scenario-id", scenario_id])
    for token in signature_token_filters or []:
        command.extend(["--signature-token", token])
    return command


def build_runtime_multi_node_observation_command(
    *,
    runtime_metadata_path: Path,
    node_ids: list[str],
    observation_window_seconds: float,
    output_dir: Path,
    observation_primitives: list[str],
    sample_interval_seconds: float = 1.0,
    network_magic: int | None = None,
    cardano_cli: str | None = None,
    connect_attempts: int | None = None,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_multi_node_observation.py"),
        "--runtime-metadata-path",
        str(runtime_metadata_path),
        "--observation-window-seconds",
        str(observation_window_seconds),
        "--sample-interval-seconds",
        str(sample_interval_seconds),
        "--output-dir",
        str(output_dir),
    ]
    for node_id in node_ids:
        command.extend(["--node-id", node_id])
    for primitive in observation_primitives:
        command.extend(["--observation", primitive])
    if network_magic is not None:
        command.extend(["--network-magic", str(network_magic)])
    if cardano_cli:
        command.extend(["--cardano-cli", str(cardano_cli)])
    if connect_attempts is not None:
        command.extend(["--connect-attempts", str(int(connect_attempts))])
    return command


def build_runtime_substrate_tip_warmup_command(
    *,
    runtime_metadata_path: Path,
    node_ids: list[str],
    output_dir: Path,
    timeout_seconds: float,
    sample_interval_seconds: float = 2.0,
    minimum_ready_nodes: int | None = None,
    minimum_slot: int = 1,
    network_magic: int | None = None,
    cardano_cli: str | None = None,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_substrate_tip_warmup.py"),
        "--runtime-metadata-path",
        str(runtime_metadata_path),
        "--timeout-seconds",
        str(timeout_seconds),
        "--sample-interval-seconds",
        str(sample_interval_seconds),
        "--minimum-slot",
        str(int(minimum_slot)),
        "--output-dir",
        str(output_dir),
    ]
    for node_id in node_ids:
        command.extend(["--node-id", node_id])
    if minimum_ready_nodes is not None:
        command.extend(["--minimum-ready-nodes", str(int(minimum_ready_nodes))])
    if network_magic is not None:
        command.extend(["--network-magic", str(network_magic)])
    if cardano_cli:
        command.extend(["--cardano-cli", str(cardano_cli)])
    return command


def _summarize_differential_rule_artifacts(output_dir: Path) -> dict:
    diff_path = output_dir / "diff.json"
    diff_is_empty = True
    if diff_path.is_file():
        body = diff_path.read_text(encoding="utf-8", errors="replace").strip()
        if body and body not in ("{}", "null", "[]"):
            diff_is_empty = False
    return {
        "output_dir": str(output_dir),
        "has_input_json": (output_dir / "input.json").is_file(),
        "has_amaru_result_json": (output_dir / "amaru-result.json").is_file(),
        "has_reference_result_json": (output_dir / "reference-result.json").is_file(),
        "has_diff_json": diff_path.is_file(),
        "diff_is_empty": diff_is_empty,
        "has_stdout_log": (output_dir / "harness.stdout.log").is_file(),
        "has_stderr_log": (output_dir / "harness.stderr.log").is_file(),
        "has_assertion_input_json": (output_dir / "assertion-input.json").is_file(),
    }


def _summarize_fuzz_campaign_artifacts(output_dir: Path) -> dict:
    combined_corpus_dir = output_dir / "combined-corpus"
    return {
        "output_dir": str(output_dir),
        "has_campaign_report": (output_dir / "campaign-report.json").is_file(),
        "has_aggregated_stats": (output_dir / "aggregated-stats.json").is_file(),
        "has_combined_corpus": combined_corpus_dir.is_dir(),
        "combined_corpus_count": _count_files(combined_corpus_dir),
    }


def _summarize_long_campaign_artifacts(output_dir: Path) -> dict:
    checkpoints_dir = output_dir / "checkpoints"
    checkpoint_dirs = sorted(path for path in checkpoints_dir.iterdir() if path.is_dir()) if checkpoints_dir.is_dir() else []
    checkpoint_stats_count = sum(1 for path in checkpoint_dirs if (path / "stats.json").is_file())
    checkpoint_coverage_count = sum(1 for path in checkpoint_dirs if (path / "coverage.json").is_file())
    checkpoint_queue_archive_count = sum(1 for path in checkpoint_dirs if (path / "queue-snapshot.tar.gz").is_file())
    return {
        "output_dir": str(output_dir),
        "has_campaign_report": (output_dir / "campaign-report.json").is_file(),
        "checkpoint_count": len(checkpoint_dirs),
        "checkpoint_stats_count": checkpoint_stats_count,
        "checkpoint_coverage_count": checkpoint_coverage_count,
        "checkpoint_queue_archive_count": checkpoint_queue_archive_count,
    }


def _summarize_persistent_campaign_artifacts(output_dir: Path) -> dict:
    report = {}
    report_path = output_dir / "campaign-report.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    return {
        "output_dir": str(output_dir),
        "has_campaign_report": report_path.is_file(),
        "has_regressions_sarif": (output_dir / "regressions.sarif").is_file(),
        "history_length": int(report.get("run_index", 0)),
    }


def _summarize_fuzz_env_setup_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_provisioning_report": (output_dir / "provisioning-report.json").is_file(),
        "has_install_log": (output_dir / "install-log.txt").is_file(),
    }


def _summarize_miri_campaign_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_result_json": (output_dir / "result.json").is_file(),
        "has_summary_markdown": (output_dir / "summary.md").is_file(),
        "has_stdout_log": (output_dir / "stdout.log").is_file(),
        "has_stderr_log": (output_dir / "stderr.log").is_file(),
    }


def _summarize_proptest_campaign_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_result_json": (output_dir / "result.json").is_file(),
        "has_summary_markdown": (output_dir / "summary.md").is_file(),
        "has_stdout_log": (output_dir / "stdout.log").is_file(),
        "has_stderr_log": (output_dir / "stderr.log").is_file(),
    }


def _summarize_credential_ceremony_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_result_json": (output_dir / "result.json").is_file(),
        "has_summary_markdown": (output_dir / "summary.md").is_file(),
        "has_env_dir": (output_dir / "env").is_dir(),
        "has_pools_keys_dir": (output_dir / "env" / "pools-keys").is_dir(),
    }


def _summarize_amaru_proptest_oracle_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_result_json": (output_dir / "result.json").is_file(),
        "has_summary_markdown": (output_dir / "summary.md").is_file(),
        "has_stdout_log": (output_dir / "stdout.log").is_file(),
        "has_stderr_log": (output_dir / "stderr.log").is_file(),
        "has_corpus_dir": (output_dir / "corpus").is_dir(),
    }


def _summarize_execution_trace_differential_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_result_json": (output_dir / "result.json").is_file(),
        "has_summary_markdown": (output_dir / "summary.md").is_file(),
    }


def _summarize_aflnet_campaign_artifacts(output_dir: Path) -> dict:
    top_level_stats = output_dir / "fuzzer_stats"
    default_stats = output_dir / "default" / "fuzzer_stats"
    stats_path = top_level_stats if top_level_stats.is_file() else default_stats if default_stats.is_file() else None
    return {
        "output_dir": str(output_dir),
        "has_result_json": (output_dir / "result.json").is_file(),
        "has_summary_markdown": (output_dir / "summary.md").is_file(),
        "has_stdout_log": (output_dir / "stdout.log").is_file(),
        "has_stderr_log": (output_dir / "stderr.log").is_file(),
        "has_state_report": (output_dir / "server-state-report.json").is_file(),
        "has_seed_corpus": (output_dir / "seeds").is_dir(),
        "has_fuzzer_stats": stats_path is not None,
        "fuzzer_stats_path": str(stats_path) if stats_path is not None else None,
    }


def _summarize_symbolic_execution_campaign_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_result_json": (output_dir / "result.json").is_file(),
        "has_summary_markdown": (output_dir / "summary.md").is_file(),
        "has_stdout_log": (output_dir / "stdout.log").is_file(),
        "has_stderr_log": (output_dir / "stderr.log").is_file(),
        "has_generated_inputs_dir": (output_dir / "generated-inputs").is_dir(),
    }


def _summarize_cargo_mutants_campaign_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_campaign_report": (output_dir / "campaign-report.json").is_file(),
        "has_summary_markdown": (output_dir / "summary.md").is_file(),
        "has_mutants_out_dir": (output_dir / "mutants.out").is_dir(),
    }


def _summarize_afl_stability_artifacts(output_dir: Path) -> dict:
    runs_dir = output_dir / "runs"
    run_dir_count = sum(1 for path in runs_dir.iterdir() if path.is_dir()) if runs_dir.is_dir() else 0
    return {
        "output_dir": str(output_dir),
        "has_stability_report": (output_dir / "stability-report.json").is_file(),
        "has_runs_dir": runs_dir.is_dir(),
        "run_dir_count": run_dir_count,
    }


def _summarize_aggregate_coverage_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_coverage_report": (output_dir / "coverage-report.json").is_file(),
        "has_summary_markdown": (output_dir / "coverage-summary.md").is_file(),
    }


def _extract_throughput_metric_from_artifact(*, artifact_path: Path, artifact_format: str, metric_key: str) -> float:
    if artifact_format == "fuzzer_stats":
        stats = _parse_aflpp_fuzzer_stats(artifact_path)
        return float(stats[metric_key])
    if artifact_format == "json":
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        value: Any = payload
        for part in metric_key.split("."):
            if not isinstance(value, dict) or part not in value:
                raise KeyError(metric_key)
            value = value[part]
        return float(value)
    raise ValueError(f"unsupported artifact_format: {artifact_format}")


def _summarize_custom_mutator_template_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_template_report": (output_dir / "template-report.json").is_file(),
    }


def _summarize_crash_triage_artifacts(output_dir: Path) -> dict:
    groups_dir = output_dir / "groups"
    group_dirs = [path for path in groups_dir.iterdir() if path.is_dir()] if groups_dir.is_dir() else []
    return {
        "output_dir": str(output_dir),
        "has_triage_report": (output_dir / "triage-report.json").is_file(),
        "has_result_report": (output_dir / "result.json").is_file(),
        "has_triage_markdown": (output_dir / "triage-report.md").is_file(),
        "group_count": len(group_dirs),
    }


def _summarize_afl_corpus_min_artifacts(output_dir: Path) -> dict:
    cmin_dir = output_dir / "cmin"
    tmin_dir = output_dir / "tmin"
    return {
        "output_dir": str(output_dir),
        "has_cmin_dir": cmin_dir.is_dir(),
        "has_tmin_dir": tmin_dir.is_dir(),
        "has_cmin_stats": (output_dir / "cmin-stats.json").is_file(),
        "has_tmin_stats": (output_dir / "tmin-stats.json").is_file(),
        "has_summary_markdown": (output_dir / "summary.md").is_file(),
        "cmin_count": _count_files(cmin_dir),
        "tmin_count": _count_files(tmin_dir),
    }


def _summarize_coverage_report_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_coverage_html": (output_dir / "coverage.html").is_file(),
        "has_coverage_markdown": (output_dir / "coverage.md").is_file(),
        "has_coverage_summary": (output_dir / "coverage-summary.json").is_file(),
        "has_coverage_file_level_report": (output_dir / "coverage-report-file-level.json").is_file(),
        "has_coverage_file_level_markdown": (output_dir / "coverage-file-level.md").is_file(),
    }


def _summarize_corpus_health_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_corpus_health_report": (output_dir / "corpus-health-report.json").is_file(),
        "has_corpus_health_markdown": (output_dir / "corpus-health-report.md").is_file(),
        "has_corpus_health_html": (output_dir / "corpus-health-report.html").is_file(),
    }


def _summarize_bundle_attestation_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_attestation": (output_dir / "attestation.json").is_file(),
    }


def _summarize_bundle_chain_verify_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_chain_verify_report": (output_dir / "chain-verify-report.json").is_file(),
    }


def _summarize_bundle_tag_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_tags_json": (output_dir / "tags.json").is_file(),
    }


def _summarize_bundle_timeline_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_timeline_json": (output_dir / "timeline.json").is_file(),
        "has_timeline_markdown": (output_dir / "timeline-summary.md").is_file(),
    }


def _summarize_substrate_tip_warmup_artifacts(output_dir: Path) -> dict:
    summary_path = output_dir / "warmup-summary.json"
    ready = False
    ready_node_count = 0
    if summary_path.is_file():
        try:
            body = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            body = {}
        ready = bool(body.get("ready"))
        ready_node_count = int(body.get("ready_node_count", 0) or 0)
    return {
        "output_dir": str(output_dir),
        "has_warmup_summary": summary_path.is_file(),
        "ready": ready,
        "ready_node_count": ready_node_count,
    }


def _summarize_multi_node_observation_artifacts(output_dir: Path) -> dict:
    per_node_dir = output_dir / "per-node"
    node_dirs = [path for path in per_node_dir.iterdir() if path.is_dir()] if per_node_dir.is_dir() else []
    return {
        "output_dir": str(output_dir),
        "has_observation_summary": (output_dir / "observation-summary.json").is_file(),
        "has_correlated_timeline": (output_dir / "correlated-timeline.json").is_file(),
        "per_node_dir_count": len(node_dirs),
    }


def _summarize_forensic_snapshot_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_snapshot_tarball": (output_dir / "snapshot.tar.gz").is_file(),
        "has_snapshot_manifest": (output_dir / "snapshot-manifest.json").is_file(),
        "has_snapshot_readme": (output_dir / "README.md").is_file(),
    }


def _summarize_bundle_summary_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_summary_json": (output_dir / "summary.json").is_file(),
        "has_summary_md": (output_dir / "summary.md").is_file(),
        "has_summary_html": (output_dir / "summary.html").is_file(),
    }


def _summarize_static_analysis_artifacts(output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "has_stdout": (output_dir / "stdout.log").is_file(),
        "has_stderr": (output_dir / "stderr.log").is_file(),
        "has_findings": (output_dir / "findings.json").is_file(),
    }


def build_runtime_cardano_lsq_extract_command(
    *,
    socket_path: str,
    network_magic: int,
    era: int | str,
    output_path: Path,
    debug_raw_response_path: Path | None = None,
    result_json_path: Path | None = None,
) -> list[str]:
    manifest_path = DWARF_ROOT / "extractors" / "cardano-debug-epoch-state-extractor" / "Cargo.toml"
    command = [
        "cargo",
        "run",
        "--manifest-path",
        str(manifest_path),
        "--",
        "--socket",
        str(socket_path),
        "--network-magic",
        str(network_magic),
        "--era",
        str(era),
        "--out",
        str(output_path),
    ]
    if debug_raw_response_path is not None:
        command.extend(["--debug-raw-response", str(debug_raw_response_path)])
    if result_json_path is not None:
        command.extend(["--result-json", str(result_json_path)])
    return command


def build_runtime_corpus_synthesize_command(
    *,
    grammar_dict: Path,
    structure_spec: Path,
    target_id: str,
    count: int,
    strategy: str,
    output_dir: Path,
    seed: int | None = None,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "corpus_synthesizer.py"),
        "--grammar-dict",
        str(grammar_dict),
        "--structure-spec",
        str(structure_spec),
        "--target-id",
        target_id,
        "--count",
        str(count),
        "--strategy",
        strategy,
        "--output-dir",
        str(output_dir),
    ]
    if seed is not None:
        command.extend(["--seed", str(seed)])
    return command


def build_runtime_bundle_replay_command(
    *,
    runs_dir: Path,
    state_dir: Path,
    target_run_id: str,
    output_dir: Path,
    compare_relpaths: list[str],
    registry_path: Path | None = None,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_bundle_replay.py"),
        "--runs-dir",
        str(runs_dir),
        "--state-dir",
        str(state_dir),
        "--target-run-id",
        target_run_id,
        "--output-dir",
        str(output_dir),
    ]
    if registry_path is not None:
        command.extend(["--registry-path", str(registry_path)])
    for relpath in compare_relpaths:
        command.extend(["--compare-relpath", relpath])
    return command


def build_runtime_bundle_diff_command(
    *,
    runs_dir: Path,
    left_run_id: str,
    right_run_id: str,
    output_dir: Path,
    compare_relpaths: list[str],
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_bundle_diff.py"),
        "--runs-dir",
        str(runs_dir),
        "--left-run-id",
        left_run_id,
        "--right-run-id",
        right_run_id,
        "--output-dir",
        str(output_dir),
    ]
    for relpath in compare_relpaths:
        command.extend(["--compare-relpath", relpath])
    return command


def build_runtime_bundle_chain_verify_command(
    *,
    runs_dir: Path,
    target_run_id: str,
    output_dir: Path,
) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_bundle_chain_verify.py"),
        "--runs-dir",
        str(runs_dir),
        "--target-run-id",
        target_run_id,
        "--output-dir",
        str(output_dir),
    ]


def build_runtime_bundle_tag_command(
    *,
    runs_dir: Path,
    target_run_id: str,
    tags: list[str],
    output_dir: Path,
    signing_actor: str,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_bundle_tag.py"),
        "--runs-dir",
        str(runs_dir),
        "--target-run-id",
        target_run_id,
        "--output-dir",
        str(output_dir),
        "--signing-actor",
        signing_actor,
    ]
    for tag in tags:
        command.extend(["--tag", tag])
    return command


def build_runtime_forensic_snapshot_command(
    *,
    runs_dir: Path,
    run_ids: list[str],
    output_dir: Path,
    tag_filters: list[str],
    output_format: str,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_forensic_snapshot.py"),
        "--runs-dir",
        str(runs_dir),
        "--output-dir",
        str(output_dir),
        "--output-format",
        output_format,
    ]
    for run_id in run_ids:
        command.extend(["--run-id", run_id])
    for tag in tag_filters:
        command.extend(["--tag-filter", tag])
    return command


def build_runtime_bundle_summary_compose_command(
    *,
    runs_dir: Path,
    bundle_ids: list[str],
    output_dir: Path,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_bundle_summary_compose.py"),
        "--runs-dir",
        str(runs_dir),
        "--output-dir",
        str(output_dir),
    ]
    for bundle_id in bundle_ids:
        command.extend(["--bundle-id", bundle_id])
    return command


def build_runtime_static_analysis_command(
    *,
    tool: str,
    crate_dir: Path,
    output_dir: Path,
) -> list[str]:
    return [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_static_analysis.py"),
        "--tool",
        tool,
        "--crate-dir",
        str(crate_dir),
        "--output-dir",
        str(output_dir),
    ]


def build_runtime_bundle_export_sarif_command(
    *,
    runs_dir: Path,
    target_run_id: str,
    output_dir: Path,
    schema_path: Path | None = None,
) -> list[str]:
    command = [
        "python3",
        str(DWARF_ROOT / "scripts" / "runtime_bundle_export_sarif.py"),
        "--runs-dir",
        str(runs_dir),
        "--target-run-id",
        target_run_id,
        "--output-dir",
        str(output_dir),
    ]
    if schema_path is not None:
        command.extend(["--schema-path", str(schema_path)])
    return command


def load_target_manifest(target_id, *, manifests_dir):
    """Load a target manifest by id from a manifests directory.

    Manifests are stored as YAML but the loader accepts JSON-structured content
    (a deliberately narrow subset) to avoid adding a YAML dependency in v1.
    """
    manifests_dir = _resolve_runtime_path(manifests_dir)
    path = manifests_dir / f"{target_id}.yaml"
    original = Path(manifests_dir)
    if not path.exists() and original.parts[:3] == ("dwarf", "targets", "manifests"):
        path = DWARF_ROOT / "targets" / "manifests" / f"{target_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"target manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return TargetManifest(
        id=data["id"],
        binary=data["binary"],
        input_format=data.get("input_format", "stdin_bytes"),
        implementation=data.get("implementation", "unknown"),
        language=data.get("language", "unknown"),
        upstream_commit=data.get("upstream_commit", "unknown"),
        invariants=list(data.get("invariants", [])),
    )


def _classify_shim_outcome(returncode, stdout):
    """Apply the shim outcome contract: OK / clean_error / crash."""
    first_line = stdout.splitlines()[0] if stdout else ""
    if returncode == 0 and first_line.startswith("OK"):
        return "ok"
    if returncode == 1 and first_line.startswith("ERR "):
        return "clean_error"
    return "crash"


def _run_stdin_target(*, binary, data: bytes, timeout_seconds: float):
    import subprocess

    try:
        proc = subprocess.run(
            [binary],
            input=data,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        stdout = proc.stdout.decode("utf-8", errors="replace")
        outcome = _classify_shim_outcome(proc.returncode, stdout)
        return outcome, stdout
    except subprocess.TimeoutExpired:
        return "crash", ""


# ---------------------------------------------------------------------------
# Minimal CBOR encoder + structured generator / mutator
#
# Used by CborFuzzStructured to produce *almost-valid* CBOR (real outer
# envelope + mutated inner fields) so deeper decoder paths get exercised
# than random bytes can reach. We hand-roll a small subset rather than depend
# on a CBOR library; the primitive only needs to emit the major types and
# basic shapes the Conway era's serialised types are built from.
# ---------------------------------------------------------------------------


def _cbor_emit_uint(value):
    """Major type 0: unsigned integer."""
    if value < 0:
        raise ValueError("uint must be non-negative")
    if value < 24:
        return bytes([value])
    if value < 0x100:
        return bytes([0x18, value])
    if value < 0x10000:
        return bytes([0x19, (value >> 8) & 0xff, value & 0xff])
    if value < 0x100000000:
        return bytes([0x1a]) + value.to_bytes(4, "big")
    if value < 0x10000000000000000:
        return bytes([0x1b]) + value.to_bytes(8, "big")
    raise ValueError(f"uint too large: {value}")


def _cbor_emit_header(major_type, length):
    """Emit the CBOR major-type header for a value of given length."""
    base = (major_type & 0x7) << 5
    if length < 24:
        return bytes([base | length])
    if length < 0x100:
        return bytes([base | 0x18, length])
    if length < 0x10000:
        return bytes([base | 0x19, (length >> 8) & 0xff, length & 0xff])
    if length < 0x100000000:
        return bytes([base | 0x1a]) + length.to_bytes(4, "big")
    return bytes([base | 0x1b]) + length.to_bytes(8, "big")


def _cbor_emit_bytes(data):
    """Major type 2: byte string."""
    return _cbor_emit_header(2, len(data)) + data


def _cbor_emit_array_header(length):
    """Major type 4: array header (caller appends elements)."""
    return _cbor_emit_header(4, length)


def _cbor_emit_map_header(length):
    """Major type 5: map header (caller appends key/value pairs)."""
    return _cbor_emit_header(5, length)


def generate_cbor(shape, rng):
    """Generate a well-formed CBOR value matching the given shape descriptor.

    Shape descriptor schema (recursive):
      {"type": "uint", "max": N (default 2**32)}
      {"type": "bytes", "length": N (or {"min": N, "max": M})}
      {"type": "text", "length": N or {"min", "max"}}
      {"type": "bool"}
      {"type": "null"}
      {"type": "array", "elements": [shape, shape, ...]}    # fixed-length
      {"type": "map", "entries": [(key, shape), ...]}        # int keys, fixed
      {"type": "tag", "tag": N, "inner": shape}
      {"type": "any"} — random short value (terminal)
    """
    t = shape.get("type")
    if t == "uint":
        max_v = int(shape.get("max", 0xffffffff))
        return _cbor_emit_uint(rng.randint(0, max_v))
    if t == "bytes":
        length_spec = shape.get("length", 0)
        n = _resolve_length(length_spec, rng)
        return _cbor_emit_bytes(bytes(rng.getrandbits(8) for _ in range(n)))
    if t == "text":
        n = _resolve_length(shape.get("length", 0), rng)
        # ASCII printable random
        s = bytes(rng.randint(0x20, 0x7e) for _ in range(n))
        return _cbor_emit_header(3, n) + s
    if t == "bool":
        return b"\xf5" if rng.random() < 0.5 else b"\xf4"
    if t == "null":
        return b"\xf6"
    if t == "array":
        elements = shape.get("elements", [])
        out = _cbor_emit_array_header(len(elements))
        for elem in elements:
            out += generate_cbor(elem, rng)
        return out
    if t == "map":
        entries = shape.get("entries", [])
        out = _cbor_emit_map_header(len(entries))
        for key, value_shape in entries:
            if isinstance(key, int) and key >= 0:
                out += _cbor_emit_uint(key)
            elif isinstance(key, int):
                out += _cbor_emit_header(1, -1 - key)  # negative int
            else:
                # text key
                ks = str(key).encode("utf-8")
                out += _cbor_emit_header(3, len(ks)) + ks
            out += generate_cbor(value_shape, rng)
        return out
    if t == "tag":
        tag = int(shape.get("tag", 0))
        inner = shape.get("inner", {"type": "null"})
        return _cbor_emit_header(6, tag) + generate_cbor(inner, rng)
    if t == "any":
        # terminal — emit a short uint
        return _cbor_emit_uint(rng.randint(0, 100))
    raise ValueError(f"unknown shape type: {t!r}")


def _target_hook_events_from_handle(handle, *, event=None, primitive=None):
    events = []
    if hasattr(handle, "events"):
        events.extend(list(getattr(handle, "events")))
    run_dir = getattr(handle, "run_dir", None)
    if run_dir is not None:
        path = Path(run_dir) / "events" / "target-hooks.ndjson"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if not events:
        return []
    out = []
    for e in events:
        if event is not None and e.get("event") != event:
            continue
        if primitive is not None and e.get("primitive") != primitive:
            continue
        out.append(e)
    return out


def _runtime_metric_samples_from_handle(handle, metric_name: str):
    run_dir = getattr(handle, "run_dir", None)
    if run_dir is None:
        return []
    path = Path(run_dir) / "metrics" / "runtime" / f"{metric_name}.ndjson"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _resolve_length(spec, rng):
    if isinstance(spec, int):
        return spec
    if isinstance(spec, dict):
        return rng.randint(int(spec.get("min", 0)), int(spec.get("max", 32)))
    return 0


def mutate_cbor(data, rng, *, mutation_rate=0.05):
    """Apply byte-level mutations to well-formed CBOR.

    `mutation_rate` is the probability per byte of being flipped to a random
    value. Length is preserved (offsets stay aligned for downstream decoders
    that read by position). For mutation_rate=0.0 returns input unchanged.
    """
    if mutation_rate <= 0.0:
        return data
    out = bytearray(data)
    for i in range(len(out)):
        if rng.random() < mutation_rate:
            out[i] = rng.getrandbits(8)
    return bytes(out)


class CborFuzzStructured(LoadPrimitive):
    """Generate almost-valid CBOR per a shape, optionally mutate, feed to shim.

    Parameters:
      target_id        — shim manifest id under manifests_dir
      manifests_dir    — directory holding <target_id>.yaml
      shape            — CBOR shape descriptor (see generate_cbor)
      iterations       — number of inputs to feed (default 100)
      mutation_rate    — probability per byte of random flip (default 0.05)
      per_input_timeout_seconds — per-iteration timeout (default 5)

    Compared to cbor_fuzz_target's random-byte stream, this exercises deeper
    decoder paths because every input has the right outer envelope shape and
    only inner fields are corrupted.
    """

    def run(self, handle, rng):
        import subprocess
        target_id = self.params["target_id"]
        manifests_dir = Path(self.params["manifests_dir"])
        iterations = int(self.params.get("iterations", 100))
        mutation_rate = float(self.params.get("mutation_rate", 0.05))
        per_input_timeout = float(self.params.get("per_input_timeout_seconds", 5))
        shape = self.params["shape"]

        manifest = load_target_manifest(target_id, manifests_dir=manifests_dir)
        binary = str(_resolve_runtime_path(manifest.binary))

        handle.log(
            phase="load", primitive="cbor_fuzz_structured", level="info", event="started",
            payload={"target_id": target_id, "iterations": iterations, "binary": binary,
                     "upstream_commit": manifest.upstream_commit, "mutation_rate": mutation_rate},
        )

        for i in range(iterations):
            clean = generate_cbor(shape, rng)
            data = mutate_cbor(clean, rng, mutation_rate=mutation_rate)
            try:
                proc = subprocess.run([binary], input=data, capture_output=True,
                                      timeout=per_input_timeout, check=False)
                outcome = _classify_shim_outcome(proc.returncode, proc.stdout.decode("utf-8", errors="replace"))
                stdout = proc.stdout.decode("utf-8", errors="replace")
            except subprocess.TimeoutExpired:
                outcome = "crash"
                stdout = ""
            handle.log(
                phase="load", primitive="cbor_fuzz_structured", level="info", event="iteration",
                payload={"i": i, "outcome": outcome, "size": len(data), "stdout_head": stdout[:200]},
            )

        handle.log(
            phase="load", primitive="cbor_fuzz_structured", level="info", event="completed",
            payload={"iterations": iterations},
        )


class CborEdgeCases(LoadPrimitive):
    """Feed a curated list of CBOR edge-case byte strings to a shim and classify each.

    Parameters:
      target_id        — manifest id under manifests_dir
      manifests_dir    — directory holding <target_id>.yaml
      edge_cases       — list of {"name": str, "hex": "<hex bytes>"}
      per_input_timeout_seconds — per-case timeout (default 5)
    """

    def run(self, handle, rng):
        import subprocess
        target_id = self.params["target_id"]
        manifests_dir = Path(self.params["manifests_dir"])
        cases = self.params["edge_cases"]
        per_input_timeout = float(self.params.get("per_input_timeout_seconds", 5))
        manifest = load_target_manifest(target_id, manifests_dir=manifests_dir)
        binary = str(_resolve_runtime_path(manifest.binary))
        handle.log(phase="load", primitive="cbor_edge_cases", level="info", event="started",
                   payload={"target_id": target_id, "binary": binary,
                            "case_count": len(cases),
                            "upstream_commit": manifest.upstream_commit})
        for case in cases:
            name = case["name"]
            data = bytes.fromhex(case["hex"])
            try:
                proc = subprocess.run([binary], input=data, capture_output=True,
                                      timeout=per_input_timeout, check=False)
                outcome = _classify_shim_outcome(proc.returncode, proc.stdout.decode("utf-8", errors="replace"))
                stdout = proc.stdout.decode("utf-8", errors="replace")
            except subprocess.TimeoutExpired:
                outcome = "crash"
                stdout = ""
            handle.log(phase="load", primitive="cbor_edge_cases", level="info", event="case",
                       payload={"name": name, "size": len(data), "outcome": outcome,
                                "stdout_head": stdout[:200]})
        handle.log(phase="load", primitive="cbor_edge_cases", level="info", event="completed",
                   payload={"case_count": len(cases)})


class MiniProtocolSequenceTarget(LoadPrimitive):
    """Replay named mini-protocol message sequences through a shim target.

    Parameters:
      target_id        — manifest id under manifests_dir
      manifests_dir    — directory holding <target_id>.yaml
      corpus           — JSON file with {id, protocol, sequences:[{id,messages:[{name,hex}]}]}
      per_input_timeout_seconds — per-message timeout (default 5)
    """

    def run(self, handle, rng):
        import subprocess
        target_id = self.params["target_id"]
        manifests_dir = Path(self.params["manifests_dir"])
        corpus_path = _resolve_runtime_path(self.params["corpus"])
        per_input_timeout = float(self.params.get("per_input_timeout_seconds", 5))

        corpus = _load_sequence_corpus(corpus_path)
        sequence_filter = self.params.get("sequence_filter")
        if sequence_filter is not None:
            if not isinstance(sequence_filter, list) or not all(isinstance(item, str) for item in sequence_filter):
                raise ValueError("sequence_filter must be a list of sequence ids when present")
            wanted = set(sequence_filter)
            corpus["sequences"] = [seq for seq in corpus["sequences"] if seq["id"] in wanted]
            missing = sorted(wanted - {seq["id"] for seq in corpus["sequences"]})
            if missing:
                raise ValueError(f"sequence_filter references missing sequence ids: {missing}")
        manifest = load_target_manifest(target_id, manifests_dir=manifests_dir)
        binary = str(_resolve_runtime_path(manifest.binary))

        handle.log(
            phase="load", primitive="mini_protocol_sequence_target", level="info", event="started",
            payload={
                "target_id": target_id,
                "binary": binary,
                "corpus": str(corpus_path),
                "corpus_id": corpus.get("id"),
                "protocol": corpus.get("protocol"),
                "sequence_count": len(corpus["sequences"]),
                "upstream_commit": manifest.upstream_commit,
            },
        )

        i = 0
        for sequence in corpus["sequences"]:
            sequence_id = sequence["id"]
            handle.log(
                phase="load", primitive="mini_protocol_sequence_target", level="info",
                event="sequence_started", payload={"sequence_id": sequence_id},
            )
            for message_index, message in enumerate(sequence["messages"]):
                data = message["_bytes"]
                try:
                    proc = subprocess.run(
                        [binary],
                        input=data,
                        capture_output=True,
                        timeout=per_input_timeout,
                        check=False,
                    )
                    stdout = proc.stdout.decode("utf-8", errors="replace")
                    outcome = _classify_shim_outcome(proc.returncode, stdout)
                except subprocess.TimeoutExpired:
                    stdout = ""
                    outcome = "crash"
                payload = {
                    "i": i,
                    "input_id": f"{sequence_id}:{message_index}",
                    "sequence_id": sequence_id,
                    "message_index": message_index,
                    "message": message["name"],
                    "outcome": outcome,
                    "size": len(data),
                    "input_hex": message["hex"],
                    "stdout_head": stdout[:200],
                }
                if "expect" in message:
                    payload["expected_outcome"] = message["expect"]
                handle.log(
                    phase="load", primitive="mini_protocol_sequence_target", level="info",
                    event="message", payload=dict(payload),
                )
                handle.log(
                    phase="load", primitive="mini_protocol_sequence_target", level="info",
                    event="iteration", payload=payload,
                )
                i += 1

        handle.log(
            phase="load", primitive="mini_protocol_sequence_target", level="info", event="completed",
            payload={"messages": i, "sequence_count": len(corpus["sequences"])},
        )


class MiniProtocolStateMachine(LoadPrimitive):
    """Replay declared protocol transitions and emit a state trace.

    Parameters:
      target_id        — manifest id under manifests_dir
      manifests_dir    — directory holding <target_id>.yaml
      corpus           — JSON file with state-machine sequences
      sequence_filter  — optional list of sequence ids to execute
      per_input_timeout_seconds — per-message timeout (default 5)

    This primitive validates the declared transition chain and each message's
    expected envelope outcome. It does not prove live implementation state.
    """

    def run(self, handle, rng):
        import subprocess
        target_id = self.params["target_id"]
        manifests_dir = Path(self.params["manifests_dir"])
        corpus_path = _resolve_runtime_path(self.params["corpus"])
        per_input_timeout = float(self.params.get("per_input_timeout_seconds", 5))

        corpus = _load_state_machine_corpus(corpus_path)
        sequence_filter = self.params.get("sequence_filter")
        if sequence_filter is not None:
            if not isinstance(sequence_filter, list) or not all(isinstance(item, str) for item in sequence_filter):
                raise ValueError("sequence_filter must be a list of sequence ids when present")
            wanted = set(sequence_filter)
            corpus["sequences"] = [seq for seq in corpus["sequences"] if seq["id"] in wanted]
            missing = sorted(wanted - {seq["id"] for seq in corpus["sequences"]})
            if missing:
                raise ValueError(f"sequence_filter references missing state-machine sequence ids: {missing}")

        manifest = load_target_manifest(target_id, manifests_dir=manifests_dir)
        binary = str(_resolve_runtime_path(manifest.binary))
        handle.log(
            phase="load", primitive="mini_protocol_state_machine", level="info", event="started",
            payload={
                "target_id": target_id,
                "binary": binary,
                "corpus": str(corpus_path),
                "corpus_id": corpus.get("id"),
                "protocol": corpus.get("protocol"),
                "sequence_count": len(corpus["sequences"]),
                "upstream_commit": manifest.upstream_commit,
            },
        )

        trace = {"corpus_id": corpus.get("id"), "protocol": corpus.get("protocol"), "sequences": []}
        i = 0
        for sequence in corpus["sequences"]:
            sequence_id = sequence["id"]
            current_state = sequence["initial_state"]
            seq_trace = {"id": sequence_id, "initial_state": current_state, "transitions": []}
            handle.log(
                phase="load", primitive="mini_protocol_state_machine", level="info",
                event="sequence_started", payload={"sequence_id": sequence_id, "initial_state": current_state},
            )
            for transition_index, transition in enumerate(sequence["transitions"]):
                message = transition["message"]
                data = message["_bytes"]
                try:
                    proc = subprocess.run(
                        [binary], input=data, capture_output=True,
                        timeout=per_input_timeout, check=False,
                    )
                    stdout = proc.stdout.decode("utf-8", errors="replace")
                    outcome = _classify_shim_outcome(proc.returncode, stdout)
                except subprocess.TimeoutExpired:
                    stdout = ""
                    outcome = "crash"
                state_matches = transition["from"] == current_state
                expected_outcome = message.get("expect")
                outcome_matches = expected_outcome is None or expected_outcome == outcome
                transition_ok = state_matches and outcome_matches
                if state_matches:
                    current_state = transition["to"]
                payload = {
                    "i": i,
                    "input_id": f"{sequence_id}:{transition_index}",
                    "sequence_id": sequence_id,
                    "transition_index": transition_index,
                    "message": message["name"],
                    "from_state": transition["from"],
                    "to_state": transition["to"],
                    "current_state_before": transition["from"],
                    "current_state_after": current_state,
                    "state_matches": state_matches,
                    "expected_outcome": expected_outcome,
                    "outcome": outcome,
                    "outcome_matches": outcome_matches,
                    "transition_ok": transition_ok,
                    "size": len(data),
                    "input_hex": message["hex"],
                    "stdout_head": stdout[:200],
                }
                handle.log(
                    phase="load", primitive="mini_protocol_state_machine", level="info",
                    event="transition", payload=dict(payload),
                )
                handle.log(
                    phase="load", primitive="mini_protocol_state_machine", level="info",
                    event="iteration", payload=dict(payload),
                )
                seq_trace["transitions"].append(dict(payload))
                i += 1
            seq_trace["final_state"] = current_state
            trace["sequences"].append(seq_trace)

        outputs_dir = Path(handle.run_dir) / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        (outputs_dir / "state-machine-trace.json").write_text(
            json.dumps(trace, sort_keys=True, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        handle.log(
            phase="load", primitive="mini_protocol_state_machine", level="info", event="completed",
            payload={"transitions": i, "sequence_count": len(corpus["sequences"])},
        )


def _load_sequence_corpus(corpus_path):
    data = json.loads(Path(corpus_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("sequence corpus must be a mapping")
    sequences = data.get("sequences")
    if not isinstance(sequences, list) or not sequences:
        raise ValueError("sequence corpus requires a non-empty sequences list")
    for seq_index, sequence in enumerate(sequences):
        if not isinstance(sequence, dict):
            raise ValueError(f"sequences[{seq_index}] must be a mapping")
        sequence_id = sequence.get("id")
        if not isinstance(sequence_id, str) or not sequence_id:
            raise ValueError(f"sequences[{seq_index}].id must be a non-empty string")
        messages = sequence.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError(f"sequences[{seq_index}].messages must be a non-empty list")
        for msg_index, message in enumerate(messages):
            if not isinstance(message, dict):
                raise ValueError(f"sequences[{seq_index}].messages[{msg_index}] must be a mapping")
            name = message.get("name")
            hex_value = message.get("hex")
            if not isinstance(name, str) or not name:
                raise ValueError(f"sequences[{seq_index}].messages[{msg_index}].name must be a non-empty string")
            if not isinstance(hex_value, str):
                raise ValueError(f"sequences[{seq_index}].messages[{msg_index}].hex must be a string")
            try:
                message["_bytes"] = bytes.fromhex(hex_value)
            except ValueError as exc:
                raise ValueError(
                    f"sequences[{seq_index}].messages[{msg_index}].hex is not valid hex"
                ) from exc
            expect = message.get("expect")
            if expect is not None and expect not in ("ok", "clean_error", "crash"):
                raise ValueError(
                    f"sequences[{seq_index}].messages[{msg_index}].expect must be ok, clean_error, or crash"
                )
    return data


def _load_state_machine_corpus(corpus_path):
    data = json.loads(Path(corpus_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("state-machine corpus must be a mapping")
    sequences = data.get("sequences")
    if not isinstance(sequences, list) or not sequences:
        raise ValueError("state-machine corpus requires a non-empty sequences list")
    for seq_index, sequence in enumerate(sequences):
        if not isinstance(sequence, dict):
            raise ValueError(f"sequences[{seq_index}] must be a mapping")
        if not isinstance(sequence.get("id"), str) or not sequence["id"]:
            raise ValueError(f"sequences[{seq_index}].id must be a non-empty string")
        if not isinstance(sequence.get("initial_state"), str) or not sequence["initial_state"]:
            raise ValueError(f"sequences[{seq_index}].initial_state must be a non-empty string")
        transitions = sequence.get("transitions")
        if not isinstance(transitions, list) or not transitions:
            raise ValueError(f"sequences[{seq_index}].transitions must be a non-empty list")
        for tr_index, transition in enumerate(transitions):
            if not isinstance(transition, dict):
                raise ValueError(f"sequences[{seq_index}].transitions[{tr_index}] must be a mapping")
            for key in ("from", "to"):
                if not isinstance(transition.get(key), str) or not transition[key]:
                    raise ValueError(
                        f"sequences[{seq_index}].transitions[{tr_index}].{key} must be a non-empty string"
                    )
            message = transition.get("message")
            if not isinstance(message, dict):
                raise ValueError(f"sequences[{seq_index}].transitions[{tr_index}].message must be a mapping")
            name = message.get("name")
            hex_value = message.get("hex")
            if not isinstance(name, str) or not name:
                raise ValueError(
                    f"sequences[{seq_index}].transitions[{tr_index}].message.name must be a non-empty string"
                )
            if not isinstance(hex_value, str):
                raise ValueError(
                    f"sequences[{seq_index}].transitions[{tr_index}].message.hex must be a string"
                )
            try:
                message["_bytes"] = bytes.fromhex(hex_value)
            except ValueError as exc:
                raise ValueError(
                    f"sequences[{seq_index}].transitions[{tr_index}].message.hex is not valid hex"
                ) from exc
            expect = message.get("expect")
            if expect is not None and expect not in ("ok", "clean_error", "crash"):
                raise ValueError(
                    f"sequences[{seq_index}].transitions[{tr_index}].message.expect must be ok, clean_error, or crash"
                )
    return data


class LoadShellCommand(LoadPrimitive):
    """Run a host shell command and capture its outcome into the bundle.

    Used for resource-abuse smokes that drive existing host tools (ps, dd,
    df, free, cardano-cli) without requiring docker primitives.

    Parameters:
      command           — string, passed to /bin/sh -c
      timeout_seconds   — float, default 60
      expect_exit       — int, default 0; if set and mismatched, marks fail
    """

    def run(self, handle, rng):
        cmd = self.params["command"]
        timeout = float(self.params.get("timeout_seconds", 60))
        expect_exit = self.params.get("expect_exit", 0)
        handle.log(phase="load", primitive="load_shell_command", level="info",
                   event="started", payload={"command": cmd, "timeout_seconds": timeout})
        env = _build_dwarf_telemetry_env(handle)
        try:
            proc = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True,
                                  timeout=timeout, check=False, env=env)
            stdout = _decode_process_output(proc.stdout)
            stderr = _decode_process_output(proc.stderr)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as e:
            stdout = _decode_process_output(e.stdout)
            stderr = _decode_process_output(e.stderr)
            exit_code = -1
        outcome = "ok" if exit_code == expect_exit else "unexpected_exit"
        handle.log(phase="load", primitive="load_shell_command", level="info",
                   event="completed",
                   payload={"exit_code": exit_code, "outcome": outcome,
                            "stdout": stdout[:4096], "stderr": stderr[:1024]})


class RuntimeAflppCampaign(LoadPrimitive):
    """Run the AFL++ campaign helper and capture its bundle/artifact summary."""

    def run(self, handle, rng):
        working_dir = _resolve_runtime_path(self.params["working_dir"])
        bin_name = str(self.params["bin"])
        seed_dirs = [_resolve_runtime_path(path) for path in self.params["seed_dirs"]]
        output_dir = _resolve_runtime_path(self.params["output_dir"])
        seconds = int(self.params["seconds"])
        timeout = float(self.params.get("timeout_seconds", max(seconds + 300, 600)))
        expect_exit = int(self.params.get("expect_exit", 0))
        dict_path = self.params.get("dict_path")
        dict_path = _resolve_runtime_path(dict_path) if dict_path else None
        rustup_toolchain = self.params.get("rustup_toolchain")
        sanitizer = self.params.get("sanitizer")
        target_triple = self.params.get("target_triple")
        afl_mode = str(self.params.get("afl_mode", "instrumented"))
        target_binary_path = self.params.get("target_binary_path")
        target_binary_path = _resolve_runtime_path(target_binary_path) if target_binary_path else None
        target_implementation = str(self.params.get("target_implementation", "amaru"))
        replay_harness = str(self.params["replay_harness"])
        replay_target_id = str(self.params["replay_target_id"])
        replay_targets = [str(target) for target in self.params["replay_targets"]]
        command = build_runtime_aflpp_campaign_command(
            working_dir=working_dir,
            bin_name=bin_name,
            seed_dirs=seed_dirs,
            output_dir=output_dir,
            seconds=seconds,
            dict_path=dict_path,
            target_implementation=target_implementation,
            replay_harness=replay_harness,
            replay_target_id=replay_target_id,
            replay_targets=replay_targets,
            sanitizer=sanitizer,
            target_triple=target_triple,
            afl_mode=afl_mode,
            target_binary_path=target_binary_path,
        )

        handle.log(
            phase="load",
            primitive="runtime_aflpp_campaign",
            level="info",
            event="started",
            payload={
                "command": command,
                "working_dir": str(working_dir),
                "bin": bin_name,
                "seed_dirs": [str(path) for path in seed_dirs],
                "output_dir": str(output_dir),
                "seconds": seconds,
                "timeout_seconds": timeout,
                "dict_path": str(dict_path) if dict_path else None,
                "rustup_toolchain": rustup_toolchain,
                "sanitizer": sanitizer,
                "target_triple": target_triple,
                "afl_mode": afl_mode,
                "target_binary_path": str(target_binary_path) if target_binary_path else None,
                "replay_harness": replay_harness,
                "replay_target_id": replay_target_id,
                "replay_targets": replay_targets,
            },
        )

        try:
            env = _ensure_cargo_path(_build_dwarf_telemetry_env(handle))
            if rustup_toolchain:
                env["RUSTUP_TOOLCHAIN"] = str(rustup_toolchain)
            with _temporary_workspace_root_manifest_move_aside(working_dir) as moved_root_manifest:
                proc = subprocess.run(
                    command,
                    cwd=DWARF_ROOT,
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                    env=env,
                )
            stdout = _decode_process_output(proc.stdout)
            stderr = _decode_process_output(proc.stderr)
            exit_code = proc.returncode
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            stdout = _decode_process_output(exc.stdout)
            stderr = _decode_process_output(exc.stderr)
            exit_code = -1
            timed_out = True

        artifact_summary, stats = _read_aflpp_result_artifacts(handle, output_dir=output_dir)
        outcome = "timeout" if timed_out else ("ok" if exit_code == expect_exit else "unexpected_exit")
        handle.log(
            phase="load",
            primitive="runtime_aflpp_campaign",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": exit_code,
                "outcome": outcome,
                "timed_out": timed_out,
                "working_dir": str(working_dir),
                "bin": bin_name,
                "output_dir": str(output_dir),
                "moved_root_manifest": moved_root_manifest if 'moved_root_manifest' in locals() else False,
                "artifact_summary": artifact_summary,
                "stats": stats,
                "stdout": stdout[:4096],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeCardanoLsqExtract(LoadPrimitive):
    """Extract a CBOR DebugEpochState snapshot from a live cardano-node socket."""

    bound_state = None

    def run(self, handle, rng):
        run_dir = Path(getattr(handle, "run_dir", Path.cwd()))

        def _resolve_output_path(value):
            candidate = Path(value)
            if candidate.is_absolute():
                return candidate
            return run_dir / candidate

        state = self.bound_state or {}
        compose_report = state.get("substrate_compose_report") or {}
        socket_path = str(_resolve_output_path(self.params["socket_path"]))
        if not Path(socket_path).exists():
            socket_path = str(self.params["socket_path"])
        network_magic = compose_report.get("network_magic")
        if network_magic is None:
            network_magic = int(self.params["network_magic"])
        else:
            network_magic = int(network_magic)
        era = self.params["era"]
        output_path = _resolve_output_path(self.params["output_path"])
        raw_path_param = self.params.get("debug_raw_response_path")
        result_path_param = self.params.get("result_json_path")
        debug_raw_response_path = _resolve_output_path(raw_path_param) if raw_path_param else None
        result_json_path = _resolve_output_path(result_path_param) if result_path_param else None
        timeout = float(self.params.get("timeout_seconds", 180))
        expect_exit = int(self.params.get("expect_exit", 0))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if debug_raw_response_path is not None:
            debug_raw_response_path.parent.mkdir(parents=True, exist_ok=True)
        if result_json_path is not None:
            result_json_path.parent.mkdir(parents=True, exist_ok=True)

        command = build_runtime_cardano_lsq_extract_command(
            socket_path=socket_path,
            network_magic=network_magic,
            era=era,
            output_path=output_path,
            debug_raw_response_path=debug_raw_response_path,
            result_json_path=result_json_path,
        )

        handle.log(
            phase="load",
            primitive="runtime_cardano_lsq_extract",
            level="info",
            event="started",
            payload={
                "command": command,
                "socket_path": socket_path,
                "network_magic": network_magic,
                "era": era,
                "output_path": str(output_path),
                "debug_raw_response_path": str(debug_raw_response_path) if debug_raw_response_path else None,
                "result_json_path": str(result_json_path) if result_json_path else None,
                "timeout_seconds": timeout,
            },
        )

        try:
            proc = subprocess.run(
                command,
                cwd=DWARF_ROOT.parent,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
            )
            stdout = _decode_process_output(proc.stdout)
            stderr = _decode_process_output(proc.stderr)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = _decode_process_output(exc.stdout)
            stderr = _decode_process_output(exc.stderr)
            exit_code = -1

        result = None
        if result_json_path is not None and result_json_path.is_file():
            result = json.loads(result_json_path.read_text(encoding="utf-8"))
            provenance = {
                "extractor_crate": str(DWARF_ROOT / "extractors" / "cardano-debug-epoch-state-extractor"),
                "socket_path": result["socket_path"],
                "network_magic": result["network_magic"],
                "era": result["era"],
                "snapshot_path": result["snapshot_path"],
                "raw_response_path": result.get("raw_response_path"),
                "result_json_path": str(result_json_path),
            }
            provenance_path = result_json_path.with_name("provenance.json")
            provenance_path.write_text(json.dumps(provenance, sort_keys=True, indent=2), encoding="utf-8")

        stdout_path = output_path.with_name("stdout.log")
        stderr_path = output_path.with_name("stderr.log")
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")

        outcome = "ok" if exit_code == expect_exit else "unexpected_exit"
        handle.log(
            phase="load",
            primitive="runtime_cardano_lsq_extract",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": exit_code,
                "outcome": outcome,
                "stdout": stdout[:4096],
                "stderr": stderr[:1024],
                "result": result,
                "output_path": str(output_path),
                "debug_raw_response_path": str(debug_raw_response_path) if debug_raw_response_path else None,
                "result_json_path": str(result_json_path) if result_json_path else None,
            },
        )


class RuntimeCorpusSynthesize(LoadPrimitive):
    """Generate a target corpus under the bundle using the corpus synthesizer helper."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        grammar_dict = _resolve_runtime_path(self.params["grammar_dict"])
        structure_spec = _resolve_runtime_path(self.params["structure_spec"])
        target_id = str(self.params["target_id"])
        count = int(self.params["count"])
        strategy = str(self.params["strategy"])
        seed = int(self.params.get("seed", rng.getrandbits(32)))
        timeout = float(self.params.get("timeout_seconds", 60))
        expect_exit = int(self.params.get("expect_exit", 0))

        output_dir.mkdir(parents=True, exist_ok=True)
        stdout_log_path = output_dir / "stdout.log"
        stderr_log_path = output_dir / "stderr.log"
        manifest_path = output_dir / "manifest.json"

        command = build_runtime_corpus_synthesize_command(
            grammar_dict=grammar_dict,
            structure_spec=structure_spec,
            target_id=target_id,
            count=count,
            strategy=strategy,
            output_dir=output_dir,
            seed=seed,
        )

        handle.log(
            phase="load",
            primitive="runtime_corpus_synthesize",
            level="info",
            event="started",
            payload={
                "command": command,
                "grammar_dict": str(grammar_dict),
                "structure_spec": str(structure_spec),
                "target_id": target_id,
                "count": count,
                "strategy": strategy,
                "output_dir": str(output_dir),
                "seed": seed,
                "timeout_seconds": timeout,
            },
        )

        try:
            proc = subprocess.run(
                command,
                cwd=DWARF_ROOT,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=_build_dwarf_telemetry_env(handle),
            )
            stdout = _decode_process_output(proc.stdout)
            stderr = _decode_process_output(proc.stderr)
            exit_code = proc.returncode
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            stdout = _decode_process_output(exc.stdout)
            stderr = _decode_process_output(exc.stderr)
            exit_code = -1
            timed_out = True

        stdout_log_path.write_text(stdout, encoding="utf-8")
        stderr_log_path.write_text(stderr, encoding="utf-8")

        manifest = None
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                manifest = {"invalid_json": True}

        outcome = "timeout" if timed_out else ("ok" if exit_code == expect_exit else "unexpected_exit")
        handle.log(
            phase="load",
            primitive="runtime_corpus_synthesize",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": exit_code,
                "outcome": outcome,
                "timed_out": timed_out,
                "target_id": target_id,
                "count": count,
                "strategy": strategy,
                "output_dir": str(output_dir),
                "seed": seed,
                "manifest": manifest,
                "stdout_log_path": str(stdout_log_path),
                "stderr_log_path": str(stderr_log_path),
                "stdout": stdout[:4096],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeDifferentialRuleHarness(LoadPrimitive):
    """Build and run a reusable differential rule harness binary."""

    def run(self, handle, rng):
        working_dir = _resolve_runtime_path(self.params["working_dir"])
        manifest_path = working_dir / "Cargo.toml"
        bin_name = str(self.params["bin"])
        input_path = _resolve_runtime_path(self.params["input_path"])
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 300))
        rustup_toolchain = self.params.get("rustup_toolchain")
        target_implementation = str(self.params["target_implementation"])
        reference_implementation = str(self.params["reference_implementation"])
        binary_path = working_dir / "target" / "release" / bin_name
        build_command = [
            "cargo",
            "build",
            "--manifest-path",
            str(manifest_path),
            "--release",
            "--bin",
            bin_name,
        ]
        run_command = build_runtime_differential_rule_harness_command(
            binary_path=binary_path,
            input_path=input_path,
            output_dir=output_dir,
            target_implementation=target_implementation,
            reference_implementation=reference_implementation,
        )

        handle.log(
            phase="load",
            primitive="runtime_differential_rule_harness",
            level="info",
            event="started",
            payload={
                "working_dir": str(working_dir),
                "manifest_path": str(manifest_path),
                "bin": bin_name,
                "input_path": str(input_path),
                "output_dir": str(output_dir),
                "timeout_seconds": timeout,
                "target_implementation": target_implementation,
                "reference_implementation": reference_implementation,
                "rustup_toolchain": rustup_toolchain,
                "build_command": build_command,
                "run_command": run_command,
            },
        )

        env = _ensure_cargo_path(_build_dwarf_telemetry_env(handle))
        if rustup_toolchain:
            env["RUSTUP_TOOLCHAIN"] = str(rustup_toolchain)

        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            build = subprocess.run(
                build_command,
                cwd=working_dir,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=env,
            )
            build_stdout = _decode_process_output(build.stdout)
            build_stderr = _decode_process_output(build.stderr)
            if build.returncode != 0:
                stdout = build_stdout
                stderr = build_stderr
                exit_code = build.returncode
                timed_out = False
                outcome = "build_failed"
            else:
                run = subprocess.run(
                    run_command,
                    cwd=working_dir,
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                    env=env,
                )
                stdout = _decode_process_output(run.stdout)
                stderr = _decode_process_output(run.stderr)
                exit_code = run.returncode
                timed_out = False
                if exit_code == 0:
                    outcome = "ok"
                elif exit_code == 1:
                    outcome = "decode_error"
                elif exit_code == 2:
                    outcome = "divergence"
                elif exit_code == 3:
                    outcome = "panic"
                else:
                    outcome = "unexpected_exit"
        except subprocess.TimeoutExpired as exc:
            build_stdout = ""
            build_stderr = ""
            stdout = _decode_process_output(exc.stdout)
            stderr = _decode_process_output(exc.stderr)
            exit_code = -1
            timed_out = True
            outcome = "timeout"

        (output_dir / "harness.stdout.log").write_text(stdout, encoding="utf-8")
        (output_dir / "harness.stderr.log").write_text(stderr, encoding="utf-8")
        artifact_summary = _summarize_differential_rule_artifacts(output_dir)
        assertion_input = {
            "exit_code": exit_code,
            "outcome": outcome,
            "target_implementation": target_implementation,
            "reference_implementation": reference_implementation,
            "artifact_summary": artifact_summary,
        }
        (output_dir / "assertion-input.json").write_text(
            json.dumps(assertion_input, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        artifact_summary = _summarize_differential_rule_artifacts(output_dir)

        handle.log(
            phase="load",
            primitive="runtime_differential_rule_harness",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "working_dir": str(working_dir),
                "bin": bin_name,
                "input_path": str(input_path),
                "output_dir": str(output_dir),
                "exit_code": exit_code,
                "outcome": outcome,
                "timed_out": timed_out,
                "artifact_summary": artifact_summary,
                "build_stdout": build_stdout[-2048:] if 'build_stdout' in locals() else "",
                "build_stderr": build_stderr[-2048:] if 'build_stderr' in locals() else "",
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeCustomMutatorTemplate(LoadPrimitive):
    """Run a cargo-fuzz target through the shared structural custom mutator wrapper."""

    def run(self, handle, rng):
        working_dir = _resolve_runtime_path(self.params["working_dir"])
        fuzz_dir = _resolve_runtime_path(self.params["fuzz_dir"])
        target_name = str(self.params["target_name"])
        seed_dirs = [_resolve_runtime_path(path) for path in self.params["seed_dirs"]]
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        seconds = int(self.params["seconds"])
        timeout = float(self.params.get("timeout_seconds", max(seconds + 300, 600)))
        expect_exit = int(self.params.get("expect_exit", 0))
        toolchain = self.params.get("toolchain")
        dict_path = self.params.get("dict_path")
        dict_path = _resolve_runtime_path(dict_path) if dict_path else None
        extra_libfuzzer_args = [str(arg) for arg in self.params.get("extra_libfuzzer_args", [])]
        target_implementation = str(self.params.get("target_implementation", "amaru"))
        replay_harness = str(self.params["replay_harness"])
        replay_target_id = str(self.params["replay_target_id"])
        replay_targets = [str(target) for target in self.params["replay_targets"]]
        command = build_runtime_custom_mutator_template_command(
            working_dir=working_dir,
            fuzz_dir=fuzz_dir,
            target_name=target_name,
            seed_dirs=seed_dirs,
            output_dir=output_dir,
            seconds=seconds,
            toolchain=toolchain,
            dict_path=dict_path,
            extra_libfuzzer_args=extra_libfuzzer_args,
            target_implementation=target_implementation,
            replay_harness=replay_harness,
            replay_target_id=replay_target_id,
            replay_targets=replay_targets,
        )

        handle.log(
            phase="load",
            primitive="runtime_custom_mutator_template",
            level="info",
            event="started",
            payload={
                "command": command,
                "working_dir": str(working_dir),
                "fuzz_dir": str(fuzz_dir),
                "target_name": target_name,
                "seed_dirs": [str(path) for path in seed_dirs],
                "output_dir": str(output_dir),
                "seconds": seconds,
                "timeout_seconds": timeout,
                "toolchain": toolchain,
                "dict_path": str(dict_path) if dict_path else None,
                "extra_libfuzzer_args": extra_libfuzzer_args,
                "replay_harness": replay_harness,
                "replay_target_id": replay_target_id,
                "replay_targets": replay_targets,
            },
        )

        try:
            proc = subprocess.run(
                command,
                cwd=DWARF_ROOT,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=_build_dwarf_telemetry_env(handle),
            )
            stdout = _decode_process_output(proc.stdout)
            stderr = _decode_process_output(proc.stderr)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = _decode_process_output(exc.stdout)
            stderr = _decode_process_output(exc.stderr)
            exit_code = -1

        artifact_summary = _summarize_custom_mutator_template_artifacts(output_dir)
        report = {}
        report_path = output_dir / "template-report.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if exit_code == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_custom_mutator_template",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": exit_code,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeFuzzCampaign(LoadPrimitive):
    """Run a sequential multi-engine fuzz campaign and capture the unified report."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        campaign_id = str(self.params["campaign_id"])
        total_seconds_budget = int(self.params["total_seconds_budget"])
        timeout = float(self.params.get("timeout_seconds", max(total_seconds_budget + 300, 600)))
        expect_exit = int(self.params.get("expect_exit", 0))
        subcampaigns = list(self.params["subcampaigns"])
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / "campaign-config.json"
        config_body = {
            "campaign_id": campaign_id,
            "output_dir": str(output_dir),
            "total_seconds_budget": total_seconds_budget,
            "subcampaigns": subcampaigns,
        }
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_fuzz_campaign_command(config_path=config_path)

        handle.log(
            phase="load",
            primitive="runtime_fuzz_campaign",
            level="info",
            event="started",
            payload={
                "campaign_id": campaign_id,
                "output_dir": str(output_dir),
                "config_path": str(config_path),
                "total_seconds_budget": total_seconds_budget,
                "timeout_seconds": timeout,
                "command": command,
                "subcampaign_count": len(subcampaigns),
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_fuzz_campaign_artifacts(output_dir)
        report = {}
        aggregated_stats = {}
        report_path = output_dir / "campaign-report.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        aggregated_stats_path = output_dir / "aggregated-stats.json"
        if aggregated_stats_path.is_file():
            aggregated_stats = json.loads(aggregated_stats_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_fuzz_campaign",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "campaign_id": campaign_id,
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "report": report,
                "aggregated_stats": aggregated_stats,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeLongCampaign(LoadPrimitive):
    """Run a multi-round long fuzz campaign with periodic checkpoint exports."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        campaign_id = str(self.params["campaign_id"])
        total_seconds_budget = int(self.params["total_seconds_budget"])
        checkpoint_seconds = int(self.params["checkpoint_seconds"])
        timeout = float(self.params.get("timeout_seconds", max(total_seconds_budget + 300, 600)))
        expect_exit = int(self.params.get("expect_exit", 0))
        subcampaigns = list(self.params["subcampaigns"])
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / "campaign-config.json"
        config_body = {
            "campaign_id": campaign_id,
            "output_dir": str(output_dir),
            "total_seconds_budget": total_seconds_budget,
            "checkpoint_seconds": checkpoint_seconds,
            "subcampaigns": subcampaigns,
        }
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_long_campaign_command(config_path=config_path)

        handle.log(
            phase="load",
            primitive="runtime_long_campaign",
            level="info",
            event="started",
            payload={
                "campaign_id": campaign_id,
                "output_dir": str(output_dir),
                "config_path": str(config_path),
                "total_seconds_budget": total_seconds_budget,
                "checkpoint_seconds": checkpoint_seconds,
                "timeout_seconds": timeout,
                "command": command,
                "subcampaign_count": len(subcampaigns),
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_long_campaign_artifacts(output_dir)
        report = {}
        report_path = output_dir / "campaign-report.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_long_campaign",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "campaign_id": campaign_id,
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeAflStability(LoadPrimitive):
    """Run a bounded AFL++ stability check across repeated reruns."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        stability_id = str(self.params["stability_id"])
        rerun_count = int(self.params["rerun_count"])
        seconds_per_run = int(self.params["seconds_per_run"])
        timeout = float(self.params.get("timeout_seconds", max((rerun_count * seconds_per_run) + 300, 600)))
        expect_exit = int(self.params.get("expect_exit", 0))
        campaign = dict(self.params["campaign"])
        rng_seed = self.params.get("rng_seed")
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / "stability-config.json"
        config_body = {
            "stability_id": stability_id,
            "output_dir": str(output_dir),
            "rerun_count": rerun_count,
            "seconds_per_run": seconds_per_run,
            "rng_seed": rng_seed,
            "campaign": campaign,
        }
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_afl_stability_command(config_path=config_path)

        handle.log(
            phase="load",
            primitive="runtime_afl_stability",
            level="info",
            event="started",
            payload={
                "stability_id": stability_id,
                "output_dir": str(output_dir),
                "config_path": str(config_path),
                "rerun_count": rerun_count,
                "seconds_per_run": seconds_per_run,
                "rng_seed": rng_seed,
                "timeout_seconds": timeout,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_afl_stability_artifacts(output_dir)
        report = {}
        report_path = output_dir / "stability-report.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_afl_stability",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "stability_id": stability_id,
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimePersistentCampaign(LoadPrimitive):
    """Run a child campaign with persisted history, regression checks, and SARIF emission."""

    bound_schedule = None

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        campaign_id = str(self.params["campaign_id"])
        runner_type = str(self.params["runner_type"])
        timeout = float(self.params.get("timeout_seconds", 1800))
        expect_exit = int(self.params.get("expect_exit", 0))
        child_config = dict(self.params["child_config"])
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / "campaign-config.json"
        config_body = {
            "campaign_id": campaign_id,
            "output_dir": str(output_dir),
            "state_dir": str(getattr(handle, "_state_dir")),
            "schedule": self.params.get("schedule", self.bound_schedule),
            "runner_type": runner_type,
            "child_config": child_config,
            "coverage_drop_threshold_pct": float(self.params.get("coverage_drop_threshold_pct", 0.0)),
            "sarif_upload_command": self.params.get("sarif_upload_command"),
        }
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_persistent_campaign_command(config_path=config_path)

        handle.log(
            phase="load",
            primitive="runtime_persistent_campaign",
            level="info",
            event="started",
            payload={
                "campaign_id": campaign_id,
                "runner_type": runner_type,
                "schedule": config_body["schedule"],
                "output_dir": str(output_dir),
                "config_path": str(config_path),
                "timeout_seconds": timeout,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_persistent_campaign_artifacts(output_dir)
        report = {}
        report_path = output_dir / "campaign-report.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_persistent_campaign",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "campaign_id": campaign_id,
                "runner_type": runner_type,
                "schedule": config_body["schedule"],
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeFuzzEnvSetup(LoadPrimitive):
    """Provision or verify the local fuzzing toolchain stack for the current host."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        nightly_toolchain = str(self.params.get("nightly_toolchain", "nightly-2025-11-21"))
        timeout = float(self.params.get("timeout_seconds", 900))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_runtime_fuzz_env_setup_command(
            output_dir=output_dir,
            nightly_toolchain=nightly_toolchain,
        )

        handle.log(
            phase="load",
            primitive="runtime_fuzz_env_setup",
            level="info",
            event="started",
            payload={
                "output_dir": str(output_dir),
                "nightly_toolchain": nightly_toolchain,
                "timeout_seconds": timeout,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_fuzz_env_setup_artifacts(output_dir)
        report = {}
        report_path = output_dir / "provisioning-report.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_fuzz_env_setup",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "nightly_toolchain": nightly_toolchain,
                "artifact_summary": artifact_summary,
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeMiriCampaign(LoadPrimitive):
    """Run bounded cargo-miri tests and normalize UB findings into a campaign report."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        repo_dir = _resolve_runtime_path(self.params["repo_dir"])
        packages = [str(pkg) for pkg in self.params["packages"]]
        toolchain = str(self.params.get("toolchain", "nightly-2025-11-21"))
        miriflags = [str(flag) for flag in self.params.get("miriflags", ["-Zmiri-disable-isolation"])]
        test_filter = self.params.get("test_filter")
        timeout_seconds = float(self.params.get("timeout_seconds", 1800))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_runtime_miri_campaign_command(
            repo_dir=repo_dir,
            packages=packages,
            output_dir=output_dir,
            toolchain=toolchain,
            miriflags=miriflags,
            test_filter=test_filter,
        )

        handle.log(
            phase="load",
            primitive="runtime_miri_campaign",
            level="info",
            event="started",
            payload={
                "repo_dir": str(repo_dir),
                "packages": packages,
                "toolchain": toolchain,
                "miriflags": miriflags,
                "test_filter": test_filter,
                "output_dir": str(output_dir),
                "timeout_seconds": timeout_seconds,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_miri_campaign_artifacts(output_dir)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_miri_campaign",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeProptestCampaign(LoadPrimitive):
    """Run bounded property-test checks and record shrunk minimal repros."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        repo_dir = _resolve_runtime_path(self.params["repo_dir"])
        checks = [dict(check) for check in self.params["checks"]]
        cases = int(self.params.get("cases", 8))
        toolchain = self.params.get("toolchain")
        timeout_seconds = float(self.params.get("timeout_seconds", 1800))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_runtime_proptest_campaign_command(
            repo_dir=repo_dir,
            checks=checks,
            output_dir=output_dir,
            cases=cases,
            toolchain=toolchain,
        )

        handle.log(
            phase="load",
            primitive="runtime_proptest_campaign",
            level="info",
            event="started",
            payload={
                "repo_dir": str(repo_dir),
                "checks": checks,
                "cases": cases,
                "toolchain": toolchain,
                "output_dir": str(output_dir),
                "timeout_seconds": timeout_seconds,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_proptest_campaign_artifacts(output_dir)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_proptest_campaign",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeCredentialCeremony(LoadPrimitive):
    """Generate reusable offline pool credentials via cardano-testnet."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        pool_count = int(self.params.get("pool_count", 1))
        testnet_magic = int(self.params.get("testnet_magic", 42))
        kes_period_window = int(self.params.get("kes_period_window", 1))
        deterministic_seed = self.params.get("deterministic_seed")
        cardano_testnet_bin = self.params.get("cardano_testnet_bin")
        timeout_seconds = float(self.params.get("timeout_seconds", 300))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_runtime_credential_ceremony_command(
            output_dir=output_dir,
            pool_count=pool_count,
            testnet_magic=testnet_magic,
            kes_period_window=kes_period_window,
            deterministic_seed=deterministic_seed,
            cardano_testnet_bin=cardano_testnet_bin,
        )

        handle.log(
            phase="load",
            primitive="runtime_credential_ceremony",
            level="info",
            event="started",
            payload={
                "output_dir": str(output_dir),
                "pool_count": pool_count,
                "testnet_magic": testnet_magic,
                "kes_period_window": kes_period_window,
                "deterministic_seed": deterministic_seed,
                "cardano_testnet_bin": cardano_testnet_bin,
                "timeout_seconds": timeout_seconds,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=_ensure_user_local_bin_path(_ensure_cargo_path(_build_dwarf_telemetry_env(handle))),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_credential_ceremony_artifacts(output_dir)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_credential_ceremony",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeAmaruProptestOracle(LoadPrimitive):
    """Run selected Amaru proptest fixtures and capture any emitted corpus files."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        repo_dir = _resolve_runtime_path(self.params["repo_dir"])
        target_subcrate = str(self.params["target_subcrate"])
        fixture_filter = self.params.get("fixture_filter")
        corpus_size = int(self.params.get("corpus_size", 32))
        toolchain = self.params.get("toolchain")
        timeout_seconds = float(self.params.get("timeout_seconds", 1800))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_runtime_amaru_proptest_oracle_command(
            repo_dir=repo_dir,
            target_subcrate=target_subcrate,
            fixture_filter=fixture_filter,
            corpus_size=corpus_size,
            output_dir=output_dir,
            toolchain=toolchain,
        )

        handle.log(
            phase="load",
            primitive="runtime_amaru_proptest_oracle",
            level="info",
            event="started",
            payload={
                "repo_dir": str(repo_dir),
                "target_subcrate": target_subcrate,
                "fixture_filter": fixture_filter,
                "corpus_size": corpus_size,
                "toolchain": toolchain,
                "output_dir": str(output_dir),
                "timeout_seconds": timeout_seconds,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_amaru_proptest_oracle_artifacts(output_dir)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_amaru_proptest_oracle",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeExecutionTraceDifferential(LoadPrimitive):
    """Replay a structured corpus through the protocol differential decoder harness."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        corpus_dir = _resolve_runtime_path(self.params["corpus_dir"])
        protocol = str(self.params["protocol"])
        differential_binary = self.params.get("differential_binary")
        corpus_size = int(self.params.get("corpus_size", 16))
        timeout_seconds = float(self.params.get("timeout_seconds", 600))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_runtime_execution_trace_differential_command(
            protocol=protocol,
            corpus_dir=corpus_dir,
            output_dir=output_dir,
            differential_binary=_resolve_runtime_path(differential_binary) if differential_binary else None,
            corpus_size=corpus_size,
        )

        handle.log(
            phase="load",
            primitive="runtime_execution_trace_differential",
            level="info",
            event="started",
            payload={
                "protocol": protocol,
                "corpus_dir": str(corpus_dir),
                "output_dir": str(output_dir),
                "differential_binary": differential_binary,
                "corpus_size": corpus_size,
                "timeout_seconds": timeout_seconds,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_execution_trace_differential_artifacts(output_dir)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_execution_trace_differential",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeAflNetCampaign(LoadPrimitive):
    """Run a bounded AFLNet session-fuzz campaign against the handshake decoder wrapper."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        aflnet_dir = _resolve_runtime_path(self.params["aflnet_dir"])
        target_binary_path = _resolve_aflnet_target_binary_path(self.params["target_binary_path"])
        state_corpus = _resolve_runtime_path(self.params["state_corpus"])
        seconds = int(self.params.get("seconds", 30))
        port = int(self.params.get("port", 8554))
        protocol = str(self.params.get("protocol", "SMTP"))
        startup_wait_usec = int(self.params.get("startup_wait_usec", 100000))
        timeout_seconds = float(self.params.get("timeout_seconds", max(120, seconds + 60)))
        min_execs_done = int(self.params.get("min_execs_done", 2))
        min_sessions = int(self.params.get("min_sessions", 2))
        min_plot_data_rows = int(self.params.get("min_plot_data_rows", 1))
        expect_exit = int(self.params.get("expect_exit", 0))
        server_script_path = _resolve_runtime_path(self.params["server_script_path"]) if self.params.get("server_script_path") else None
        server_binary_path = _resolve_runtime_path(self.params["server_binary_path"]) if self.params.get("server_binary_path") else None
        use_dumb_mode = bool(self.params.get("use_dumb_mode", True))
        if not target_binary_path.exists():
            raise FileNotFoundError(f"AFLNet target binary not found: {target_binary_path}")
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_runtime_aflnet_campaign_command(
            aflnet_dir=aflnet_dir,
            target_binary_path=target_binary_path,
            state_corpus=state_corpus,
            output_dir=output_dir,
            seconds=seconds,
            port=port,
            protocol=protocol,
            startup_wait_usec=startup_wait_usec,
            server_script_path=server_script_path,
            server_binary_path=server_binary_path,
            use_dumb_mode=use_dumb_mode,
            timeout_seconds=timeout_seconds,
            min_execs_done=min_execs_done,
            min_sessions=min_sessions,
            min_plot_data_rows=min_plot_data_rows,
        )

        handle.log(
            phase="load",
            primitive="runtime_aflnet_campaign",
            level="info",
            event="started",
            payload={
                "aflnet_dir": str(aflnet_dir),
                "target_binary_path": str(target_binary_path),
                "state_corpus": str(state_corpus),
                "output_dir": str(output_dir),
                "seconds": seconds,
                "port": port,
                "protocol": protocol,
                "startup_wait_usec": startup_wait_usec,
                "server_script_path": str(server_script_path) if server_script_path else None,
                "server_binary_path": str(server_binary_path) if server_binary_path else None,
                "use_dumb_mode": use_dumb_mode,
                "timeout_seconds": timeout_seconds,
                "min_execs_done": min_execs_done,
                "min_sessions": min_sessions,
                "min_plot_data_rows": min_plot_data_rows,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_aflnet_campaign_artifacts(output_dir)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_aflnet_campaign",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeSymbolicExecutionCampaign(LoadPrimitive):
    """Run a bounded binary-level symbolic exploration campaign against a release decoder target."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        python_path = _resolve_runtime_path(self.params["python_path"])
        target_binary_path = _resolve_runtime_path(self.params["target_binary_path"])
        input_size_bytes = int(self.params.get("input_size_bytes", 8))
        max_steps = int(self.params.get("max_steps", 60))
        max_generated_inputs = int(self.params.get("max_generated_inputs", 4))
        timeout_seconds = float(self.params.get("timeout_seconds", 300))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_runtime_symbolic_execution_campaign_command(
            python_path=python_path,
            target_binary_path=target_binary_path,
            output_dir=output_dir,
            input_size_bytes=input_size_bytes,
            max_steps=max_steps,
            max_generated_inputs=max_generated_inputs,
        )

        handle.log(
            phase="load",
            primitive="runtime_symbolic_execution_campaign",
            level="info",
            event="started",
            payload={
                "python_path": str(python_path),
                "target_binary_path": str(target_binary_path),
                "output_dir": str(output_dir),
                "input_size_bytes": input_size_bytes,
                "max_steps": max_steps,
                "max_generated_inputs": max_generated_inputs,
                "timeout_seconds": timeout_seconds,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_symbolic_execution_campaign_artifacts(output_dir)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_symbolic_execution_campaign",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeCargoMutantsCampaign(LoadPrimitive):
    """Run a bounded cargo-mutants campaign and record a normalized kill-rate report."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        repo_dir = _resolve_runtime_path(self.params["repo_dir"])
        package = self.params.get("package")
        file = str(self.params["file"])
        jobs = int(self.params.get("jobs", 1))
        timeout = int(self.params.get("timeout", 20))
        baseline = str(self.params.get("baseline", "skip"))
        toolchain = self.params.get("toolchain")
        no_config = bool(self.params.get("no_config", False))
        timeout_seconds = float(self.params.get("timeout_seconds", 1800))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_runtime_cargo_mutants_campaign_command(
            repo_dir=repo_dir,
            file=file,
            output_dir=output_dir,
            package=package,
            jobs=jobs,
            timeout=timeout,
            baseline=baseline,
            toolchain=toolchain,
            no_config=no_config,
        )

        handle.log(
            phase="load",
            primitive="runtime_cargo_mutants_campaign",
            level="info",
            event="started",
            payload={
                "repo_dir": str(repo_dir),
                "package": package,
                "file": file,
                "jobs": jobs,
                "timeout": timeout,
                "baseline": baseline,
                "toolchain": toolchain,
                "no_config": no_config,
                "output_dir": str(output_dir),
                "timeout_seconds": timeout_seconds,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_cargo_mutants_campaign_artifacts(output_dir)
        report = {}
        report_path = output_dir / "campaign-report.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_cargo_mutants_campaign",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class _RuntimeSnapshotSubstratePrimitive(LoadPrimitive):
    bound_state = None
    primitive_name = ""
    mode_name = ""

    def run(self, handle, rng):
        state = self.bound_state or {}
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 240))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / f"{self.primitive_name}-config.json"
        runtime_metadata_value = (
            self.params.get("runtime_metadata_path")
            or state.get("substrate_bundle_runtime_metadata_path")
            or state.get("substrate_runtime_metadata_path")
        )
        config_body = {"output_dir": str(output_dir)}
        if runtime_metadata_value:
            config_body["runtime_metadata_path"] = str(_resolve_output_path(handle, runtime_metadata_value))
        if "target_node" in self.params:
            config_body["target_node"] = str(self.params["target_node"])
        if "snapshot_path" in self.params:
            config_body["snapshot_path"] = str(_resolve_output_path(handle, self.params["snapshot_path"]))
        for key in (
            "stop_node_during_capture",
            "healthy_timeout_seconds",
            "corruption_mode",
            "byte_offset",
            "byte_count",
            "truncate_bytes",
            "xor_mask",
        ):
            if key in self.params:
                config_body[key] = self.params[key]
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_snapshot_substrate_command(config_path=config_path, mode=self.mode_name)

        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info",
            event="started",
            payload={
                "output_dir": str(output_dir),
                "config_path": str(config_path),
                "timeout_seconds": timeout,
                "command": command,
                **({"runtime_metadata_path": config_body["runtime_metadata_path"]} if "runtime_metadata_path" in config_body else {}),
                **({"snapshot_path": config_body["snapshot_path"]} if "snapshot_path" in config_body else {}),
                **({"target_node": config_body["target_node"]} if "target_node" in config_body else {}),
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {
                    "has_result_report": report_path.is_file(),
                    "has_snapshot_tar": any(output_dir.glob("*-snapshot.tar")),
                    "has_snapshot_manifest": any(output_dir.glob("*-snapshot-manifest.json")),
                    "has_corrupted_snapshot": any(output_dir.glob("*-corrupted.tar")),
                },
                "report": report,
                "result": report.get("result") or {},
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeSnapshotCapture(_RuntimeSnapshotSubstratePrimitive):
    primitive_name = "runtime_snapshot_capture"
    mode_name = "capture"


class RuntimeSnapshotCorrupt(_RuntimeSnapshotSubstratePrimitive):
    primitive_name = "runtime_snapshot_corrupt"
    mode_name = "corrupt"


class RuntimeSnapshotRestore(_RuntimeSnapshotSubstratePrimitive):
    primitive_name = "runtime_snapshot_restore"
    mode_name = "restore"


class _RuntimeSubstrateCheckpointPrimitive(LoadPrimitive):
    primitive_name = "runtime_substrate_checkpoint"
    mode_name = "checkpoint"
    bound_state = None

    def run(self, handle, rng):
        state = self.bound_state or {}
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 360))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / f"{self.primitive_name}-config.json"
        runtime_metadata_value = (
            self.params.get("runtime_metadata_path")
            or state.get("substrate_bundle_runtime_metadata_path")
            or state.get("substrate_runtime_metadata_path")
        )
        config_body = {"output_dir": str(output_dir)}
        if runtime_metadata_value:
            config_body["runtime_metadata_path"] = str(_resolve_output_path(handle, runtime_metadata_value))
        if "checkpoint_path" in self.params:
            config_body["checkpoint_path"] = str(_resolve_output_path(handle, self.params["checkpoint_path"]))
        for key in ("stop_nodes_during_capture", "healthy_timeout_seconds"):
            if key in self.params:
                config_body[key] = self.params[key]
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_substrate_checkpoint_command(config_path=config_path, mode=self.mode_name)

        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info",
            event="started",
            payload={
                "output_dir": str(output_dir),
                "config_path": str(config_path),
                "timeout_seconds": timeout,
                "command": command,
                **({"runtime_metadata_path": config_body["runtime_metadata_path"]} if "runtime_metadata_path" in config_body else {}),
                **({"checkpoint_path": config_body["checkpoint_path"]} if "checkpoint_path" in config_body else {}),
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {
                    "has_result_report": report_path.is_file(),
                    "has_checkpoint_tar": any(output_dir.glob("*checkpoint.tar")),
                    "has_checkpoint_manifest": any(output_dir.glob("*checkpoint-manifest.json")),
                },
                "report": report,
                "result": report.get("result") or {},
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeSubstrateCheckpoint(_RuntimeSubstrateCheckpointPrimitive):
    primitive_name = "runtime_substrate_checkpoint"
    mode_name = "checkpoint"


class RuntimeSubstrateResume(_RuntimeSubstrateCheckpointPrimitive):
    primitive_name = "runtime_substrate_resume"
    mode_name = "resume"


class RuntimeInstallVersion(LoadPrimitive):
    """Resolve requested Cardano/Amaru node versions for a composed substrate."""

    bound_substrate = None
    bound_state = None

    def run(self, handle, rng):
        if not self.bound_substrate:
            raise ValueError("runtime_install_version requires scenario.substrate")
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 300))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / "install-version-config.json"
        config_body = {
            "substrate": self.bound_substrate,
            "output_dir": str(output_dir),
        }
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_install_version_command(config_path=config_path)

        handle.log(
            phase="setup",
            primitive="runtime_install_version",
            level="info",
            event="started",
            payload={
                "output_dir": str(output_dir),
                "config_path": str(config_path),
                "timeout_seconds": timeout,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / "install-report.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        if self.bound_state is not None:
            self.bound_state["substrate_install_report"] = report
            self.bound_state["substrate_install_report_path"] = str(report_path)
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="setup",
            primitive="runtime_install_version",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {
                    "has_install_report": report_path.is_file(),
                    "has_install_log": (output_dir / "install-log.txt").is_file(),
                },
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeComposeSubstrate(LoadPrimitive):
    """Compose a temporary mixed Cardano/Amaru substrate from scenario.substrate."""

    bound_substrate = None
    bound_state = None

    def run(self, handle, rng):
        if not self.bound_substrate:
            raise ValueError("runtime_compose_substrate requires scenario.substrate")
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 420))
        expect_exit = int(self.params.get("expect_exit", 0))
        healthy_timeout = float(self.params.get("healthy_timeout_seconds", 180))
        run_id = str(getattr(handle, "run_id", "substrate")).replace(":", "").replace("/", "-")
        compose_project = str(self.params.get("compose_project", f"dwarf-substrate-{run_id}"))
        runtime_root = Path(self.params.get("runtime_root") or str(Path(tempfile.gettempdir()) / compose_project))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / "compose-substrate-config.json"
        config_body = {
            "substrate": self.bound_substrate,
            "output_dir": str(output_dir),
            "runtime_root": str(runtime_root),
            "compose_project": compose_project,
            "healthy_timeout_seconds": healthy_timeout,
            "install_report_path": (self.bound_state or {}).get("substrate_install_report_path"),
        }
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_compose_substrate_command(config_path=config_path)

        handle.log(
            phase="setup",
            primitive="runtime_compose_substrate",
            level="info",
            event="started",
            payload={
                "output_dir": str(output_dir),
                "runtime_root": str(runtime_root),
                "compose_project": compose_project,
                "config_path": str(config_path),
                "healthy_timeout_seconds": healthy_timeout,
                "timeout_seconds": timeout,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / "compose-report.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        if self.bound_state is not None:
            self.bound_state["substrate_runtime_root"] = report.get("runtime_root", str(runtime_root))
            self.bound_state["substrate_runtime_metadata_path"] = report.get("runtime_metadata_path")
            self.bound_state["substrate_bundle_runtime_metadata_path"] = report.get("bundle_runtime_metadata_path")
            self.bound_state["substrate_compose_report"] = report
        for node in report.get("nodes", []):
            handle.log(
                phase="setup",
                primitive="runtime_compose_substrate",
                level="info" if node.get("healthy") else "error",
                event="node_started",
                payload=node,
            )
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="setup",
            primitive="runtime_compose_substrate",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {
                    "has_compose_report": report_path.is_file(),
                    "node_count": len(report.get("nodes", [])),
                },
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeSubstrateTipWarmup(LoadPrimitive):
    """Wait for a composed substrate to produce real non-zero tips before faults apply."""

    bound_state = None

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        state = self.bound_state or {}
        runtime_metadata_path = _resolve_output_path(handle, self.params["runtime_metadata_path"])
        if not runtime_metadata_path.exists():
            fallback_metadata = state.get("substrate_bundle_runtime_metadata_path") or state.get("substrate_runtime_metadata_path")
            if fallback_metadata:
                runtime_metadata_path = Path(str(fallback_metadata))
        node_ids = [str(node_id) for node_id in self.params.get("node_ids", [])]
        timeout = float(self.params.get("timeout_seconds", 120.0))
        sample_interval_seconds = float(self.params.get("sample_interval_seconds", 2.0))
        minimum_ready_nodes = self.params.get("minimum_ready_nodes")
        minimum_slot = int(self.params.get("minimum_slot", 1))
        compose_report = state.get("substrate_compose_report") or {}
        network_magic = compose_report.get("network_magic", self.params.get("network_magic"))
        cardano_cli = self.params.get("cardano_cli")
        if not cardano_cli:
            support_binaries = compose_report.get("support_binaries") or {}
            cardano_cli = support_binaries.get("cardano-cli")
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_runtime_substrate_tip_warmup_command(
            runtime_metadata_path=runtime_metadata_path,
            node_ids=node_ids,
            output_dir=output_dir,
            timeout_seconds=timeout,
            sample_interval_seconds=sample_interval_seconds,
            minimum_ready_nodes=int(minimum_ready_nodes) if minimum_ready_nodes is not None else None,
            minimum_slot=minimum_slot,
            network_magic=int(network_magic) if network_magic is not None else None,
            cardano_cli=str(cardano_cli) if cardano_cli else None,
        )

        handle.log(
            phase="setup",
            primitive="runtime_substrate_tip_warmup",
            level="info",
            event="started",
            payload={
                "runtime_metadata_path": str(runtime_metadata_path),
                "node_ids": node_ids,
                "output_dir": str(output_dir),
                "timeout_seconds": timeout,
                "sample_interval_seconds": sample_interval_seconds,
                "minimum_ready_nodes": minimum_ready_nodes,
                "minimum_slot": minimum_slot,
                "network_magic": network_magic,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout + 30.0,
            check=False,
            env=_build_dwarf_telemetry_env(handle),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        result = {}
        result_path = output_dir / "warmup-summary.json"
        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding="utf-8"))
        if self.bound_state is not None:
            self.bound_state["substrate_tip_warmup_summary"] = result
            self.bound_state["substrate_tip_warmup_summary_path"] = str(result_path)
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="setup",
            primitive="runtime_substrate_tip_warmup",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": _summarize_substrate_tip_warmup_artifacts(output_dir),
                "result": result,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )
        if outcome != "ok":
            raise RuntimeError(
                "runtime_substrate_tip_warmup did not reach the configured ready floor "
                f"(ready_node_count={result.get('ready_node_count', 0)}, "
                f"minimum_ready_nodes={result.get('minimum_ready_nodes', minimum_ready_nodes or len(node_ids))})"
            )


class RuntimeTeardownSubstrate(LoadPrimitive):
    """Cleanly tear down a composed substrate at scenario teardown."""

    bound_state = None

    def run(self, handle, rng):
        state = self.bound_state or {}
        runtime_metadata_path = str(self.params.get("runtime_metadata_path") or state.get("substrate_runtime_metadata_path") or "")
        if not runtime_metadata_path:
            handle.log(
                phase="teardown",
                primitive="runtime_teardown_substrate",
                level="info",
                event="completed",
                payload={
                    "exit_code": 0,
                    "outcome": "not_configured",
                    "artifact_summary": {
                        "has_teardown_report": False,
                        "stopped_count": 0,
                    },
                    "report": {
                        "stopped_count": 0,
                        "remaining_sessions": 0,
                        "nodes": [],
                    },
                    "stdout": "",
                    "stderr": "",
                },
            )
            return
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 180))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / "teardown-substrate-config.json"
        config_body = {
            "runtime_metadata_path": runtime_metadata_path,
            "output_dir": str(output_dir),
        }
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_teardown_substrate_command(config_path=config_path)

        handle.log(
            phase="teardown",
            primitive="runtime_teardown_substrate",
            level="info",
            event="started",
            payload={
                "output_dir": str(output_dir),
                "runtime_metadata_path": runtime_metadata_path,
                "timeout_seconds": timeout,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / "teardown-report.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="teardown",
            primitive="runtime_teardown_substrate",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {
                    "has_teardown_report": report_path.is_file(),
                    "stopped_count": report.get("stopped_count", 0),
                },
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class _RuntimeEraTransitionPrimitive(LoadPrimitive):
    bound_state = None
    primitive_name = ""
    report_name = ""

    def _build_config(self, output_dir: Path, state: dict) -> dict:
        raise NotImplementedError

    def _build_command(self, *, config_path: Path) -> list[str]:
        raise NotImplementedError

    def run(self, handle, rng):
        state = self.bound_state or {}
        runtime_metadata_value = (
            self.params.get("runtime_metadata_path")
            or state.get("substrate_bundle_runtime_metadata_path")
            or state.get("substrate_runtime_metadata_path")
            or ""
        )
        if not runtime_metadata_value:
            raise ValueError(f"{self.primitive_name} requires substrate runtime metadata")
        runtime_metadata_path = str(_resolve_output_path(handle, runtime_metadata_value))
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 180))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / f"{self.primitive_name}-config.json"
        config_body = {"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir)}
        config_body.update(self._build_config(output_dir, state))
        if "credential_report_path" in config_body:
            config_body["credential_report_path"] = str(
                _resolve_output_path(handle, str(config_body["credential_report_path"]))
            )
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = self._build_command(config_path=config_path)

        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info",
            event="started",
            payload={"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir), "command": command},
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / self.report_name
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {f"has_{self.report_name.replace('.json', '').replace('-', '_')}": report_path.is_file()},
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeForceHfBoundary(_RuntimeEraTransitionPrimitive):
    primitive_name = "runtime_force_hf_boundary"
    report_name = "hf-boundary-report.json"

    def _build_config(self, output_dir: Path, state: dict) -> dict:
        return {"target_slot": int(self.params["target_slot"])}

    def _build_command(self, *, config_path: Path) -> list[str]:
        return build_runtime_force_hf_boundary_command(config_path=config_path)


class RuntimeSimulateEraTransition(_RuntimeEraTransitionPrimitive):
    primitive_name = "runtime_simulate_era_transition"
    report_name = "era-transition-report.json"

    def _build_config(self, output_dir: Path, state: dict) -> dict:
        return {
            "window_start_slot": int(self.params["window_start_slot"]),
            "window_end_slot": int(self.params["window_end_slot"]),
        }

    def _build_command(self, *, config_path: Path) -> list[str]:
        return build_runtime_simulate_era_transition_command(config_path=config_path)


class RuntimeGenesisModeSimulate(_RuntimeEraTransitionPrimitive):
    primitive_name = "runtime_genesis_mode_simulate"
    report_name = "genesis-mode-report.json"

    def _build_config(self, output_dir: Path, state: dict) -> dict:
        return {"target_node": str(self.params["target_node"])}

    def _build_command(self, *, config_path: Path) -> list[str]:
        return build_runtime_genesis_mode_simulate_command(config_path=config_path)


class _RuntimeChainsyncBlockfetchFaultPrimitive(LoadPrimitive):
    bound_state = None
    primitive_name = ""
    mode_name = ""

    def _build_config(self, output_dir: Path, state: dict) -> dict:
        return {
            "target_node": str(self.params["target_node"]),
            "upstream_node_id": self.params.get("upstream_node_id"),
            "healthy_timeout_seconds": float(self.params.get("healthy_timeout_seconds", 90)),
            "activity_timeout_seconds": float(self.params.get("activity_timeout_seconds", 15)),
            "configured_limit": int(self.params.get("configured_limit", 64)),
        }

    def run(self, handle, rng):
        state = self.bound_state or {}
        runtime_metadata_value = (
            self.params.get("runtime_metadata_path")
            or state.get("substrate_bundle_runtime_metadata_path")
            or state.get("substrate_runtime_metadata_path")
            or ""
        )
        if not runtime_metadata_value:
            raise ValueError(f"{self.primitive_name} requires substrate runtime metadata")
        runtime_metadata_path = str(_resolve_output_path(handle, runtime_metadata_value))
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 240))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / f"{self.primitive_name}-config.json"
        config_body = {"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir)}
        config_body.update(self._build_config(output_dir, state))
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_chainsync_blockfetch_fault_command(config_path=config_path, mode=self.mode_name)

        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info",
            event="started",
            payload={"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir), "command": command},
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {"has_result_report": report_path.is_file()},
                "report": report,
                "result": report.get("result") or {},
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeChainsyncParentDiscontinuity(_RuntimeChainsyncBlockfetchFaultPrimitive):
    primitive_name = "runtime_chainsync_parent_discontinuity"
    mode_name = "chainsync_parent_discontinuity"


class RuntimeChainsyncNonincrementingHeight(_RuntimeChainsyncBlockfetchFaultPrimitive):
    primitive_name = "runtime_chainsync_nonincrementing_height"
    mode_name = "chainsync_nonincrementing_height"


class RuntimeChainsyncNonmonotonicSlot(_RuntimeChainsyncBlockfetchFaultPrimitive):
    primitive_name = "runtime_chainsync_nonmonotonic_slot"
    mode_name = "chainsync_nonmonotonic_slot"


class RuntimeChainsyncResponderForkSwitch(_RuntimeChainsyncBlockfetchFaultPrimitive):
    primitive_name = "runtime_chainsync_responder_fork_switch"
    mode_name = "chainsync_responder_fork_switch"


class RuntimeBlockfetchInvalidRange(_RuntimeChainsyncBlockfetchFaultPrimitive):
    primitive_name = "runtime_blockfetch_invalid_range"
    mode_name = "blockfetch_invalid_range"


class RuntimeBlockfetchRangePressure(_RuntimeChainsyncBlockfetchFaultPrimitive):
    primitive_name = "runtime_blockfetch_range_pressure"
    mode_name = "blockfetch_range_pressure"


class RuntimeBlockfetchInvalidBlockCbor(_RuntimeChainsyncBlockfetchFaultPrimitive):
    primitive_name = "runtime_blockfetch_invalid_block_cbor"
    mode_name = "blockfetch_invalid_block_cbor"


class RuntimeBlockfetchRangeMismatch(_RuntimeChainsyncBlockfetchFaultPrimitive):
    primitive_name = "runtime_blockfetch_range_mismatch"
    mode_name = "blockfetch_range_mismatch"


class RuntimeBlockfetchContinuityFailure(_RuntimeChainsyncBlockfetchFaultPrimitive):
    primitive_name = "runtime_blockfetch_continuity_failure"
    mode_name = "blockfetch_continuity_failure"


class _RuntimeTxsubmissionProbePrimitive(LoadPrimitive):
    bound_state = None
    primitive_name = ""
    mode_name = ""

    def _build_config(self, output_dir: Path, state: dict) -> dict:
        return {
            "target_node": str(self.params["target_node"]),
            "target_host": str(self.params.get("target_host", "127.0.0.1")),
            "response_timeout_seconds": float(self.params.get("response_timeout_seconds", 2.0)),
            "receive_bytes": int(self.params.get("receive_bytes", 64)),
        }

    def run(self, handle, rng):
        state = self.bound_state or {}
        runtime_metadata_value = (
            self.params.get("runtime_metadata_path")
            or state.get("substrate_bundle_runtime_metadata_path")
            or state.get("substrate_runtime_metadata_path")
            or ""
        )
        if not runtime_metadata_value:
            raise ValueError(f"{self.primitive_name} requires substrate runtime metadata")
        runtime_metadata_path = str(_resolve_output_path(handle, runtime_metadata_value))
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 240))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / f"{self.primitive_name}-config.json"
        config_body = {"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir)}
        config_body.update(self._build_config(output_dir, state))
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_txsubmission_probe_command(config_path=config_path, mode=self.mode_name)

        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info",
            event="started",
            payload={"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir), "command": command},
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {"has_result_report": report_path.is_file()},
                "report": report,
                "result": report.get("result") or {},
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeTxsubmissionWindowPressure(_RuntimeTxsubmissionProbePrimitive):
    primitive_name = "runtime_txsubmission_window_pressure"
    mode_name = "txsubmission_window_pressure"


class RuntimeTxsubmissionBatchPressure(_RuntimeTxsubmissionProbePrimitive):
    primitive_name = "runtime_txsubmission_batch_pressure"
    mode_name = "txsubmission_batch_pressure"


class RuntimeTxsubmissionUnexpectedBody(_RuntimeTxsubmissionProbePrimitive):
    primitive_name = "runtime_txsubmission_unexpected_body"
    mode_name = "txsubmission_unexpected_body"


class RuntimeMempoolFailureProbe(_RuntimeTxsubmissionProbePrimitive):
    primitive_name = "runtime_mempool_failure_probe"
    mode_name = "mempool_failure_probe"


class _RuntimeProtocolFaultPrimitive(LoadPrimitive):
    bound_state = None
    primitive_name = ""
    mode_name = ""

    def _build_config(self, output_dir: Path, state: dict) -> dict:
        config = {"target_node": str(self.params["target_node"])}
        if "reference_node" in self.params:
            config["reference_node"] = str(self.params["reference_node"])
        for key in ("timeout_seconds", "network_magic", "localtxmonitor_decoder_path", "localtxmonitor_state_corpus"):
            if key in self.params:
                config[key] = self.params[key]
        return config

    def run(self, handle, rng):
        state = self.bound_state or {}
        runtime_metadata_value = (
            self.params.get("runtime_metadata_path")
            or state.get("substrate_bundle_runtime_metadata_path")
            or state.get("substrate_runtime_metadata_path")
            or ""
        )
        if not runtime_metadata_value:
            raise ValueError(f"{self.primitive_name} requires substrate runtime metadata")
        runtime_metadata_path = str(_resolve_output_path(handle, runtime_metadata_value))
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 240))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / f"{self.primitive_name}-config.json"
        config_body = {"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir)}
        config_body.update(self._build_config(output_dir, state))
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_protocol_fault_command(config_path=config_path, mode=self.mode_name)

        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info",
            event="started",
            payload={"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir), "command": command},
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {"has_result_report": report_path.is_file()},
                "report": report,
                "result": report.get("result") or {},
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimePeerSharingFault(_RuntimeProtocolFaultPrimitive):
    primitive_name = "runtime_peersharing_fault"
    mode_name = "peersharing_fault"


class RuntimeLocalTxMonitorFault(_RuntimeProtocolFaultPrimitive):
    primitive_name = "runtime_localtxmonitor_fault"
    mode_name = "localtxmonitor_fault"


class _RuntimeRecoveryFaultPrimitive(LoadPrimitive):
    bound_state = None
    primitive_name = ""
    mode_name = ""

    def _build_config(self, output_dir: Path, state: dict) -> dict:
        config = {"target_node": str(self.params["target_node"])}
        for key in ("requested_rollback_slots", "security_parameter_k"):
            if key in self.params:
                config[key] = int(self.params[key])
        return config

    def run(self, handle, rng):
        state = self.bound_state or {}
        runtime_metadata_value = (
            self.params.get("runtime_metadata_path")
            or state.get("substrate_bundle_runtime_metadata_path")
            or state.get("substrate_runtime_metadata_path")
            or ""
        )
        if not runtime_metadata_value:
            raise ValueError(f"{self.primitive_name} requires substrate runtime metadata")
        runtime_metadata_path = str(_resolve_output_path(handle, runtime_metadata_value))
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 240))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / f"{self.primitive_name}-config.json"
        config_body = {"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir)}
        config_body.update(self._build_config(output_dir, state))
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_recovery_fault_command(config_path=config_path, mode=self.mode_name)

        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info",
            event="started",
            payload={"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir), "command": command},
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {"has_result_report": report_path.is_file()},
                "report": report,
                "result": report.get("result") or {},
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeForceRollback(_RuntimeRecoveryFaultPrimitive):
    primitive_name = "runtime_force_rollback"
    mode_name = "force_rollback"


class RuntimeChainSwitchInject(_RuntimeRecoveryFaultPrimitive):
    primitive_name = "runtime_chain_switch_inject"
    mode_name = "chain_switch_inject"


class RuntimeKillNode(_RuntimeRecoveryFaultPrimitive):
    primitive_name = "runtime_kill_node"
    mode_name = "kill_node"


class RuntimeRestartNode(_RuntimeRecoveryFaultPrimitive):
    primitive_name = "runtime_restart_node"
    mode_name = "restart_node"


class _RuntimeTopologyFaultPrimitive(LoadPrimitive):
    bound_state = None
    primitive_name = ""
    mode_name = ""

    def _build_config(self, output_dir: Path, state: dict) -> dict:
        config = {"target_node": str(self.params["target_node"])}
        if "sybil_node_ids" in self.params:
            config["sybil_node_ids"] = [str(node_id) for node_id in self.params.get("sybil_node_ids", [])]
        if "adversary_node" in self.params:
            config["adversary_node"] = str(self.params["adversary_node"])
        if "events_per_hour" in self.params:
            config["events_per_hour"] = float(self.params["events_per_hour"])
        return config

    def run(self, handle, rng):
        state = self.bound_state or {}
        runtime_metadata_value = (
            self.params.get("runtime_metadata_path")
            or state.get("substrate_bundle_runtime_metadata_path")
            or state.get("substrate_runtime_metadata_path")
            or ""
        )
        if not runtime_metadata_value:
            raise ValueError(f"{self.primitive_name} requires substrate runtime metadata")
        runtime_metadata_path = str(_resolve_output_path(handle, runtime_metadata_value))
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 240))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / f"{self.primitive_name}-config.json"
        config_body = {"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir)}
        config_body.update(self._build_config(output_dir, state))
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_topology_fault_command(config_path=config_path, mode=self.mode_name)

        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info",
            event="started",
            payload={"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir), "command": command},
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {"has_result_report": report_path.is_file()},
                "report": report,
                "result": report.get("result") or {},
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeSimulatePeerSetCapture(_RuntimeTopologyFaultPrimitive):
    primitive_name = "runtime_simulate_peer_set_capture"
    mode_name = "simulate_peer_set_capture"


class RuntimeInjectHotWarmChurn(_RuntimeTopologyFaultPrimitive):
    primitive_name = "runtime_inject_hot_warm_churn"
    mode_name = "inject_hot_warm_churn"


class RuntimePerturbLedgerPeerWeights(_RuntimeTopologyFaultPrimitive):
    primitive_name = "runtime_perturb_ledger_peer_weights"
    mode_name = "perturb_ledger_peer_weights"


class RuntimeSubstituteBigLedgerPeers(_RuntimeTopologyFaultPrimitive):
    primitive_name = "runtime_substitute_big_ledger_peers"
    mode_name = "substitute_big_ledger_peers"


class _RuntimeResourceAbuseFaultPrimitive(LoadPrimitive):
    bound_state = None
    primitive_name = ""
    mode_name = ""

    def _build_config(self, output_dir: Path, state: dict) -> dict:
        return {}

    def run(self, handle, rng):
        state = self.bound_state or {}
        runtime_metadata_value = (
            self.params.get("runtime_metadata_path")
            or state.get("substrate_bundle_runtime_metadata_path")
            or state.get("substrate_runtime_metadata_path")
            or ""
        )
        if not runtime_metadata_value:
            raise ValueError(f"{self.primitive_name} requires substrate runtime metadata")
        runtime_metadata_path = str(_resolve_output_path(handle, runtime_metadata_value))
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 240))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / f"{self.primitive_name}-config.json"
        config_body = {"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir)}
        config_body.update(self._build_config(output_dir, state))
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_resource_abuse_fault_command(config_path=config_path, mode=self.mode_name)

        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info",
            event="started",
            payload={"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir), "command": command},
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {"has_result_report": report_path.is_file()},
                "report": report,
                "result": report.get("result") or {},
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeBandwidthThrottle(_RuntimeResourceAbuseFaultPrimitive):
    primitive_name = "runtime_bandwidth_throttle"
    mode_name = "bandwidth_throttle"

    def _build_config(self, output_dir: Path, state: dict) -> dict:
        return {
            "from_node": str(self.params["from_node"]),
            "to_node": str(self.params["to_node"]),
            "kilobits_per_second": int(self.params.get("kilobits_per_second", 128)),
            "healthy_timeout_seconds": float(self.params.get("healthy_timeout_seconds", 90)),
        }


class RuntimeSlowLorisChainsync(_RuntimeResourceAbuseFaultPrimitive):
    primitive_name = "runtime_slow_loris_chainsync"
    mode_name = "slow_loris_chainsync"

    def _build_config(self, output_dir: Path, state: dict) -> dict:
        return {
            "target_node": str(self.params["target_node"]),
            "bytes_per_second": max(1, int(self.params.get("bytes_per_second", 1))),
            "healthy_timeout_seconds": float(self.params.get("healthy_timeout_seconds", 90)),
        }


class RuntimeDiskFullProbe(_RuntimeResourceAbuseFaultPrimitive):
    primitive_name = "runtime_disk_full_probe"
    mode_name = "disk_full_probe"

    def _build_config(self, output_dir: Path, state: dict) -> dict:
        config = {
            "target_node": str(self.params["target_node"]),
            "duration_seconds": float(self.params.get("duration_seconds", 20)),
            "max_fill_bytes": int(self.params.get("max_fill_bytes", 134217728)),
        }
        if "target_usage_percent" in self.params:
            config["target_usage_percent"] = int(self.params["target_usage_percent"])
        if "fill_target_free_bytes" in self.params:
            config["fill_target_free_bytes"] = int(self.params["fill_target_free_bytes"])
        return config


class RuntimeNetworkImpairment(LoadPrimitive):
    bound_state = None

    def run(self, handle, rng):
        state = self.bound_state or {}
        runtime_metadata_value = (
            self.params.get("runtime_metadata_path")
            or state.get("substrate_bundle_runtime_metadata_path")
            or state.get("substrate_runtime_metadata_path")
            or ""
        )
        if not runtime_metadata_value:
            raise ValueError("runtime_network_impairment requires substrate runtime metadata")
        runtime_metadata_path = str(_resolve_output_path(handle, runtime_metadata_value))
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 240))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / "runtime_network_impairment-config.json"
        config_body = {
            "runtime_metadata_path": runtime_metadata_path,
            "output_dir": str(output_dir),
            "from_node": str(self.params["from_node"]),
            "to_node": str(self.params["to_node"]),
            "latency_ms": int(self.params.get("latency_ms", 0)),
            "jitter_ms": int(self.params.get("jitter_ms", 0)),
            "loss_percent": int(self.params.get("loss_percent", 0)),
            "partition": bool(self.params.get("partition", False)),
            "healthy_timeout_seconds": float(self.params.get("healthy_timeout_seconds", 90)),
        }
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_network_impairment_command(config_path=config_path)

        handle.log(
            phase="load",
            primitive="runtime_network_impairment",
            level="info",
            event="started",
            payload={"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir), "command": command},
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="load",
            primitive="runtime_network_impairment",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {"has_result_report": report_path.is_file()},
                "report": report,
                "result": report.get("result") or {},
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeTimeSkew(LoadPrimitive):
    bound_state = None

    def run(self, handle, rng):
        state = self.bound_state or {}
        runtime_metadata_value = (
            self.params.get("runtime_metadata_path")
            or state.get("substrate_bundle_runtime_metadata_path")
            or state.get("substrate_runtime_metadata_path")
            or ""
        )
        if not runtime_metadata_value:
            raise ValueError("runtime_time_skew requires substrate runtime metadata")
        runtime_metadata_path = str(_resolve_output_path(handle, runtime_metadata_value))
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 240))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / "runtime_time_skew-config.json"
        config_body = {
            "runtime_metadata_path": runtime_metadata_path,
            "output_dir": str(output_dir),
            "target_node": str(self.params["target_node"]),
            "skew_seconds": int(self.params["skew_seconds"]),
            "duration_seconds": float(self.params.get("duration_seconds", 20)),
            "healthy_timeout_seconds": float(self.params.get("healthy_timeout_seconds", 90)),
        }
        if "libfaketime_path" in self.params:
            config_body["libfaketime_path"] = str(self.params["libfaketime_path"])
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_time_skew_command(config_path=config_path)

        handle.log(
            phase="load",
            primitive="runtime_time_skew",
            level="info",
            event="started",
            payload={"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir), "command": command},
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="load",
            primitive="runtime_time_skew",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {"has_result_report": report_path.is_file()},
                "report": report,
                "result": report.get("result") or {},
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class _RuntimeExposureProbePrimitive(LoadPrimitive):
    bound_state = None
    primitive_name = ""
    mode_name = ""

    def _build_config(self, output_dir: Path, state: dict) -> dict:
        config = {"target_node": str(self.params["target_node"])}
        if "reference_node" in self.params:
            config["reference_node"] = str(self.params["reference_node"])
        if "minimum_required_trustable_peers" in self.params:
            config["minimum_required_trustable_peers"] = int(self.params["minimum_required_trustable_peers"])
        if "cpu_ceiling_pct" in self.params:
            config["cpu_ceiling_pct"] = float(self.params["cpu_ceiling_pct"])
        if "submit_queue_depth_limit" in self.params:
            config["submit_queue_depth_limit"] = int(self.params["submit_queue_depth_limit"])
        if "hard_limit" in self.params:
            config["hard_limit"] = int(self.params["hard_limit"])
        if "max_keepalive_failures" in self.params:
            config["max_keepalive_failures"] = int(self.params["max_keepalive_failures"])
        return config

    def run(self, handle, rng):
        state = self.bound_state or {}
        runtime_metadata_value = (
            self.params.get("runtime_metadata_path")
            or state.get("substrate_bundle_runtime_metadata_path")
            or state.get("substrate_runtime_metadata_path")
            or ""
        )
        if not runtime_metadata_value:
            raise ValueError(f"{self.primitive_name} requires substrate runtime metadata")
        runtime_metadata_path = str(_resolve_output_path(handle, runtime_metadata_value))
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 240))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / f"{self.primitive_name}-config.json"
        config_body = {"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir)}
        config_body.update(self._build_config(output_dir, state))
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_exposure_probe_command(config_path=config_path, mode=self.mode_name)

        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info",
            event="started",
            payload={"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir), "command": command},
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {"has_result_report": report_path.is_file()},
                "report": report,
                "result": report.get("result") or {},
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeBootstrapTopologyConcentration(_RuntimeExposureProbePrimitive):
    primitive_name = "runtime_bootstrap_topology_concentration"
    mode_name = "bootstrap_topology_concentration"


class RuntimeLocalQueryStress(_RuntimeExposureProbePrimitive):
    primitive_name = "runtime_local_query_stress"
    mode_name = "local_query_stress"


class RuntimeLocalSubmitStress(_RuntimeExposureProbePrimitive):
    primitive_name = "runtime_local_submit_stress"
    mode_name = "local_submit_stress"


class RuntimeBootstrapAssumptionProbe(_RuntimeExposureProbePrimitive):
    primitive_name = "runtime_bootstrap_assumption_probe"
    mode_name = "bootstrap_assumption_probe"


class RuntimeHandshakeVersionNegotiationPressure(_RuntimeExposureProbePrimitive):
    primitive_name = "runtime_handshake_version_negotiation_pressure"
    mode_name = "handshake_version_negotiation_pressure"


class RuntimeMuxIngressOverrun(_RuntimeExposureProbePrimitive):
    primitive_name = "runtime_mux_ingress_overrun"
    mode_name = "mux_ingress_overrun"


class RuntimeDuplexPromotionPressure(_RuntimeExposureProbePrimitive):
    primitive_name = "runtime_duplex_promotion_pressure"
    mode_name = "duplex_promotion_pressure"


class RuntimeKeepaliveFailureCascade(_RuntimeExposureProbePrimitive):
    primitive_name = "runtime_keepalive_failure_cascade"
    mode_name = "keepalive_failure_cascade"


class _RuntimeHardeningProbePrimitive(LoadPrimitive):
    bound_state = None
    primitive_name = ""
    mode_name = ""
    requires_target_node = True

    def _build_config(self, output_dir: Path, state: dict) -> dict:
        config = {}
        if self.requires_target_node:
            config["target_node"] = str(self.params["target_node"])
        elif "target_node" in self.params:
            config["target_node"] = str(self.params["target_node"])
        if "reference_node" in self.params:
            config["reference_node"] = str(self.params["reference_node"])
        if "memory_ceiling_mb" in self.params:
            config["memory_ceiling_mb"] = int(self.params["memory_ceiling_mb"])
        if "credential_report_path" in self.params:
            config["credential_report_path"] = str(self.params["credential_report_path"])
        return config

    def run(self, handle, rng):
        state = self.bound_state or {}
        runtime_metadata_value = (
            self.params.get("runtime_metadata_path")
            or state.get("substrate_bundle_runtime_metadata_path")
            or state.get("substrate_runtime_metadata_path")
            or ""
        )
        if not runtime_metadata_value:
            raise ValueError(f"{self.primitive_name} requires substrate runtime metadata")
        runtime_metadata_path = str(_resolve_output_path(handle, runtime_metadata_value))
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 240))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / f"{self.primitive_name}-config.json"
        config_body = {"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir)}
        config_body.update(self._build_config(output_dir, state))
        if "credential_report_path" in config_body:
            config_body["credential_report_path"] = str(
                _resolve_output_path(handle, str(config_body["credential_report_path"]))
            )
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_hardening_probe_command(config_path=config_path, mode=self.mode_name)

        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info",
            event="started",
            payload={"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir), "command": command},
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {"has_result_report": report_path.is_file()},
                "report": report,
                "result": report.get("result") or {},
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimePraosHeaderAssertionProbe(_RuntimeHardeningProbePrimitive):
    primitive_name = "runtime_praos_header_assertion_probe"
    mode_name = "praos_header_assertion_probe"


class RuntimeMalformedInputDifferential(_RuntimeHardeningProbePrimitive):
    primitive_name = "runtime_malformed_input_differential"
    mode_name = "malformed_input_differential"


class RuntimeValidationPathDifferential(_RuntimeHardeningProbePrimitive):
    primitive_name = "runtime_validation_path_differential"
    mode_name = "validation_path_differential"


class RuntimeMempoolRelayPressure(_RuntimeHardeningProbePrimitive):
    primitive_name = "runtime_mempool_relay_pressure"
    mode_name = "mempool_relay_pressure"


class RuntimeParserBoundsProbe(_RuntimeHardeningProbePrimitive):
    primitive_name = "runtime_parser_bounds_probe"
    mode_name = "parser_bounds_probe"


class RuntimeRuntimeStarvationProbe(_RuntimeHardeningProbePrimitive):
    primitive_name = "runtime_runtime_starvation_probe"
    mode_name = "runtime_starvation_probe"


class RuntimeBlockingWorkStarvation(_RuntimeHardeningProbePrimitive):
    primitive_name = "runtime_blocking_work_starvation"
    mode_name = "blocking_work_starvation"


class RuntimePanicPathProbe(_RuntimeHardeningProbePrimitive):
    primitive_name = "runtime_panic_path_probe"
    mode_name = "panic_path_probe"


class RuntimeOverlaySlotForging(_RuntimeHardeningProbePrimitive):
    primitive_name = "runtime_overlay_slot_forging"
    mode_name = "overlay_slot_forging"


class RuntimeContainerRuntimeInspect(_RuntimeHardeningProbePrimitive):
    primitive_name = "runtime_container_runtime_inspect"
    mode_name = "container_runtime_inspect"
    requires_target_node = False


class _RuntimePlutusPhase2ProbePrimitive(LoadPrimitive):
    primitive_name = ""
    mode_name = ""

    def run(self, handle, rng):
        state = {}
        runtime_metadata_value = (
            self.params.get("runtime_metadata_path")
            or state.get("substrate_bundle_runtime_metadata_path")
            or state.get("substrate_runtime_metadata_path")
            or ""
        )
        if not runtime_metadata_value:
            raise ValueError(f"{self.primitive_name} requires substrate runtime metadata")
        runtime_metadata_path = str(_resolve_output_path(handle, runtime_metadata_value))
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 240))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / f"{self.primitive_name}-config.json"
        config_body = {
            "runtime_metadata_path": runtime_metadata_path,
            "output_dir": str(output_dir),
            "target_node": str(self.params["target_node"]),
            "observer_node": str(self.params["observer_node"]),
            "probe_case": str(self.params["probe_case"]),
        }
        if "is_valid_flag_override" in self.params:
            config_body["is_valid_flag_override"] = str(self.params["is_valid_flag_override"])
        if "ex_units_override" in self.params:
            config_body["ex_units_override"] = int(self.params["ex_units_override"])
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_plutus_phase2_probe_command(config_path=config_path, mode=self.mode_name)

        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info",
            event="started",
            payload={"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir), "command": command},
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_user_local_bin_path(_ensure_cargo_path(_build_dwarf_telemetry_env(handle))),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {"has_result_report": report_path.is_file()},
                "report": report,
                "result": report.get("result") or {},
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimePlutusPhase2SubmitProbe(_RuntimePlutusPhase2ProbePrimitive):
    primitive_name = "runtime_plutus_phase2_submit_probe"
    mode_name = "submit"


class RuntimePlutusPhase2DifferentialObservation(_RuntimePlutusPhase2ProbePrimitive):
    primitive_name = "runtime_plutus_phase2_differential_observation"
    mode_name = "differential"


class _RuntimeEpochBoundaryProbePrimitive(LoadPrimitive):
    bound_state = None
    primitive_name = ""
    mode_name = ""

    def _build_config(self, output_dir: Path, state: dict) -> dict:
        config = {}
        if "target_node" in self.params:
            config["target_node"] = str(self.params["target_node"])
        if "target_slot" in self.params:
            config["target_slot"] = int(self.params["target_slot"])
        return config

    def run(self, handle, rng):
        state = self.bound_state or {}
        runtime_metadata_value = (
            self.params.get("runtime_metadata_path")
            or state.get("substrate_bundle_runtime_metadata_path")
            or state.get("substrate_runtime_metadata_path")
            or ""
        )
        if not runtime_metadata_value:
            raise ValueError(f"{self.primitive_name} requires substrate runtime metadata")
        runtime_metadata_path = str(_resolve_output_path(handle, runtime_metadata_value))
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 180))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / f"{self.primitive_name}-config.json"
        config_body = {"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir)}
        config_body.update(self._build_config(output_dir, state))
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_epoch_boundary_probe_command(config_path=config_path, mode=self.mode_name)

        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info",
            event="started",
            payload={"runtime_metadata_path": runtime_metadata_path, "output_dir": str(output_dir), "command": command},
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report = {}
        report_path = output_dir / "result.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="load",
            primitive=self.primitive_name,
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {"has_result_report": report_path.is_file()},
                "report": report,
                "result": report.get("result") or {},
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeForceEpochBoundary(_RuntimeEpochBoundaryProbePrimitive):
    primitive_name = "runtime_force_epoch_boundary"
    mode_name = "force_epoch_boundary"


class RuntimeSimulateStakeSnapshotUpdate(_RuntimeEpochBoundaryProbePrimitive):
    primitive_name = "runtime_simulate_stake_snapshot_update"
    mode_name = "simulate_stake_snapshot_update"


class RuntimeRecomputeLeadershipSchedule(_RuntimeEpochBoundaryProbePrimitive):
    primitive_name = "runtime_recompute_leadership_schedule"
    mode_name = "recompute_leadership_schedule"


class RuntimeTriggerRupdPulse(_RuntimeEpochBoundaryProbePrimitive):
    primitive_name = "runtime_trigger_rupd_pulse"
    mode_name = "trigger_rupd_pulse"


class RuntimeAggregateCoverage(LoadPrimitive):
    """Aggregate per-bundle coverage stats into a unified report."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        runs_root = _resolve_runtime_path(self.params.get("runs_root", str(DWARF_ROOT / "runs")))
        bundle_ids = [str(bundle_id) for bundle_id in self.params.get("bundle_ids", [])]
        campaign_bundle_ids = [str(bundle_id) for bundle_id in self.params.get("campaign_bundle_ids", [])]
        timeout = float(self.params.get("timeout_seconds", 300))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_runtime_aggregate_coverage_command(
            runs_root=runs_root,
            output_dir=output_dir,
            bundle_ids=bundle_ids,
            campaign_bundle_ids=campaign_bundle_ids,
        )

        handle.log(
            phase="load",
            primitive="runtime_aggregate_coverage",
            level="info",
            event="started",
            payload={
                "runs_root": str(runs_root),
                "output_dir": str(output_dir),
                "bundle_ids": bundle_ids,
                "campaign_bundle_ids": campaign_bundle_ids,
                "timeout_seconds": timeout,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_build_dwarf_telemetry_env(handle),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_aggregate_coverage_artifacts(output_dir)
        report = {}
        report_path = output_dir / "coverage-report.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_aggregate_coverage",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeCrashTriage(LoadPrimitive):
    """Group and summarize crash inputs from a prior fuzz bundle."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        bundle_dir = _resolve_runtime_path(self.params["bundle_dir"])
        minimizer = self.params.get("minimizer")
        target_binary = self.params.get("target_binary")
        target_args = [str(arg) for arg in self.params.get("target_args", [])]
        triage_env = self.params.get("triage_env")
        signature_frames = int(self.params.get("signature_frames", 5))
        timeout = float(self.params.get("timeout_seconds", 300))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_runtime_crash_triage_command(
            bundle_dir=bundle_dir,
            output_dir=output_dir,
            minimizer=minimizer,
            target_binary=_resolve_runtime_path(target_binary) if target_binary else None,
            target_args=target_args,
            triage_env=triage_env,
            signature_frames=signature_frames,
            timeout_seconds=timeout,
        )

        handle.log(
            phase="load",
            primitive="runtime_crash_triage",
            level="info",
            event="started",
            payload={
                "bundle_dir": str(bundle_dir),
                "output_dir": str(output_dir),
                "minimizer": minimizer,
                "target_binary": str(_resolve_runtime_path(target_binary)) if target_binary else None,
                "target_args": target_args,
                "triage_env": triage_env,
                "signature_frames": signature_frames,
                "timeout_seconds": timeout,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_build_dwarf_telemetry_env(handle),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_crash_triage_artifacts(output_dir)
        report = {}
        report_path = output_dir / "triage-report.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_crash_triage",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeAflCorpusMin(LoadPrimitive):
    """Minimize a prior AFL++ queue into a compact retained corpus and per-input minima."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        queue_dir = _resolve_runtime_path(self.params["queue_dir"])
        target_binary = _resolve_runtime_path(self.params["target_binary"])
        target_args = [str(arg) for arg in self.params.get("target_args", [])]
        timeout = float(self.params.get("timeout_seconds", 900))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_runtime_afl_corpus_min_command(
            queue_dir=queue_dir,
            output_dir=output_dir,
            target_binary=target_binary,
            target_args=target_args,
        )

        handle.log(
            phase="load",
            primitive="runtime_afl_corpus_min",
            level="info",
            event="started",
            payload={
                "queue_dir": str(queue_dir),
                "output_dir": str(output_dir),
                "target_binary": str(target_binary),
                "target_args": target_args,
                "timeout_seconds": timeout,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_build_dwarf_telemetry_env(handle),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_afl_corpus_min_artifacts(output_dir)
        report = {"cmin": {}, "tmin": {}}
        cmin_stats_path = output_dir / "cmin-stats.json"
        tmin_stats_path = output_dir / "tmin-stats.json"
        if cmin_stats_path.is_file():
            report["cmin"] = json.loads(cmin_stats_path.read_text(encoding="utf-8"))
        if tmin_stats_path.is_file():
            report["tmin"] = json.loads(tmin_stats_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_afl_corpus_min",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeCoverageReport(LoadPrimitive):
    """Render an operator-readable coverage report from an aggregate coverage bundle."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        runs_dir = _resolve_runtime_path(self.params.get("runs_dir", str(DWARF_ROOT / "runs")))
        aggregate_bundle_id = self.params.get("aggregate_bundle_id")
        if aggregate_bundle_id is not None:
            aggregate_bundle_id = str(aggregate_bundle_id)
        merge_mode = str(self.params.get("merge_mode", "stat-only"))
        aflpp_bundle_ids = [str(bundle_id) for bundle_id in self.params.get("aflpp_bundle_ids", [])]
        cargo_fuzz_campaign_bundle_ids = [
            str(bundle_id) for bundle_id in self.params.get("cargo_fuzz_campaign_bundle_ids", [])
        ]
        max_inputs_per_bundle = int(self.params.get("max_inputs_per_bundle", 25))
        timeout = float(self.params.get("timeout_seconds", 120))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_runtime_coverage_report_command(
            runs_dir=runs_dir,
            aggregate_bundle_id=aggregate_bundle_id,
            output_dir=output_dir,
            merge_mode=merge_mode,
            aflpp_bundle_ids=aflpp_bundle_ids,
            cargo_fuzz_campaign_bundle_ids=cargo_fuzz_campaign_bundle_ids,
            max_inputs_per_bundle=max_inputs_per_bundle,
        )

        handle.log(
            phase="load",
            primitive="runtime_coverage_report",
            level="info",
            event="started",
            payload={
                "runs_dir": str(runs_dir),
                "aggregate_bundle_id": aggregate_bundle_id,
                "output_dir": str(output_dir),
                "merge_mode": merge_mode,
                "aflpp_bundle_ids": aflpp_bundle_ids,
                "cargo_fuzz_campaign_bundle_ids": cargo_fuzz_campaign_bundle_ids,
                "max_inputs_per_bundle": max_inputs_per_bundle,
                "timeout_seconds": timeout,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_build_dwarf_telemetry_env(handle),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_coverage_report_artifacts(output_dir)
        result = {}
        result_path = output_dir / ("coverage-report-file-level.json" if merge_mode == "file-level" else "coverage-summary.json")
        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_coverage_report",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "merge_mode": merge_mode,
                "artifact_summary": artifact_summary,
                "result": result,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeCorpusHealthReport(LoadPrimitive):
    """Aggregate historical AFL campaign metrics into a corpus-health timeseries."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        runs_root = _resolve_runtime_path(self.params["runs_root"])
        scenario_id_contains = str(self.params["scenario_id_contains"])
        timeout = float(self.params.get("timeout_seconds", 300))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_runtime_corpus_health_report_command(
            runs_root=runs_root,
            scenario_id_contains=scenario_id_contains,
            output_dir=output_dir,
        )
        handle.log(
            phase="load",
            primitive="runtime_corpus_health_report",
            level="info",
            event="started",
            payload={
                "runs_root": str(runs_root),
                "scenario_id_contains": scenario_id_contains,
                "output_dir": str(output_dir),
                "timeout_seconds": timeout,
                "command": command,
            },
        )
        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_build_dwarf_telemetry_env(handle),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_corpus_health_artifacts(output_dir)
        result = {}
        report_path = output_dir / "corpus-health-report.json"
        if report_path.is_file():
            result = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="load",
            primitive="runtime_corpus_health_report",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "result": result,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeBundleAttestation(LoadPrimitive):
    """Emit a signed provenance attestation for the current run bundle."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        signing_actor = str(self.params.get("signing_actor", os.environ.get("USER", "operator")))
        timeout = float(self.params.get("timeout_seconds", 120))
        expect_exit = int(self.params.get("expected_helper_exit", 0))
        helper_script = str(_resolve_runtime_path(self.params.get("helper_script", DWARF_ROOT / "scripts" / "runtime_bundle_attestation.py")))
        python_bin = str(self.params.get("python_bin", "python3"))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = [python_bin, helper_script, "--output-dir", str(output_dir), "--signing-actor", signing_actor]

        handle.log(
            phase="load",
            primitive="runtime_bundle_attestation",
            level="info",
            event="started",
            payload={
                "output_dir": str(output_dir),
                "signing_actor": signing_actor,
                "timeout_seconds": timeout,
                "command": command,
            },
        )

        try:
            proc = subprocess.run(
                command,
                cwd=DWARF_ROOT,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=_build_dwarf_telemetry_env(handle),
            )
            exit_code = proc.returncode
            stdout = _decode_process_output(proc.stdout)
            stderr = _decode_process_output(proc.stderr)
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            exit_code = -1
            stdout = _decode_process_output(exc.output)
            stderr = _decode_process_output(exc.stderr)
            timed_out = True

        artifact_summary = _summarize_bundle_attestation_artifacts(output_dir)
        result = {}
        attestation_path = output_dir / "attestation.json"
        if attestation_path.is_file():
            result = json.loads(attestation_path.read_text(encoding="utf-8"))
        verification_verdict = ((result.get("verification") or {}).get("verdict")) if result else None
        outcome = "timeout" if timed_out else ("ok" if exit_code == expect_exit else "unexpected_exit")

        payload = {
            "helper_exit_code": exit_code,
            "timed_out": timed_out,
            "outcome": outcome,
            "artifact_summary": artifact_summary,
            "target_run_id": result.get("target_run_id"),
            "active_profile_id": ((result.get("statement") or {}).get("active_profile_id")) if result else None,
            "verification_verdict": verification_verdict,
            "result": result,
            "stdout": stdout[-4096:],
            "stderr": stderr[-2048:],
        }

        _append_target_hook_event(
            handle,
            primitive="runtime_bundle_attestation",
            event="bundle_attestation_result",
            payload=payload,
            level="info" if outcome == "ok" else "error",
        )
        handle.log(
            phase="load",
            primitive="runtime_bundle_attestation",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class ShimPeerRawBytes(LoadPrimitive):
    """Connect to one runtime node as a raw TCP peer and send caller-specified bytes.

    Parameters:
      runtime_metadata_path   — runtime.json path for a generated/devnet profile
      target_node             — named node from runtime metadata
      payload_hex             — hex-encoded raw payload to send
      target_host             — optional host override (default 127.0.0.1)
      response_timeout_seconds — optional read timeout after write/shutdown (default 2)
      receive_bytes           — optional recv size cap (default 64)
    """

    def _runtime_node(self):
        metadata_path = Path(self.params["runtime_metadata_path"])
        if not metadata_path.exists():
            raise ValueError(f"shim_peer_raw_bytes runtime metadata missing: {metadata_path}")
        body = json.loads(metadata_path.read_text(encoding="utf-8"))
        target_node = str(self.params["target_node"])
        nodes = (body.get("haskell_nodes") or []) + (body.get("amaru_nodes") or [])
        for node in nodes:
            if str(node.get("name")) == target_node:
                return metadata_path, target_node, dict(node)
        raise ValueError(f"shim_peer_raw_bytes could not find target_node {target_node!r} in {metadata_path}")

    def run(self, handle, rng):
        import socket
        import time

        metadata_path, target_node, node = self._runtime_node()
        host = str(self.params.get("target_host", "127.0.0.1"))
        port = int(node["port"])
        payload_hex = str(self.params["payload_hex"])
        response_timeout = float(self.params.get("response_timeout_seconds", 2))
        receive_bytes = int(self.params.get("receive_bytes", 64))
        try:
            payload = bytes.fromhex(payload_hex)
        except ValueError as exc:
            raise ValueError(f"shim_peer_raw_bytes payload_hex is not valid hex: {payload_hex!r}") from exc

        handle.log(
            phase="load",
            primitive="shim_peer_raw_bytes",
            level="info",
            event="started",
            payload={
                "runtime_metadata_path": str(metadata_path),
                "target_node": target_node,
                "target_host": host,
                "target_port": port,
                "payload_hex": payload_hex,
                "payload_bytes": len(payload),
            },
        )

        started = time.perf_counter()
        response_kind = "unknown"
        response = b""
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.settimeout(response_timeout)
            sock.sendall(payload)
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            try:
                response = sock.recv(receive_bytes)
                response_kind = "data" if response else "eof"
            except socket.timeout:
                response_kind = "timeout"
                response = b""
            except ConnectionResetError:
                response_kind = "reset"
                response = b""

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if getattr(handle, "run_dir", None) is not None:
            output_dir = Path(handle.run_dir) / "outputs" / "shim-peer-raw-bytes"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / f"{target_node}.json").write_text(
                json.dumps(
                    {
                        "target_node": target_node,
                        "target_host": host,
                        "target_port": port,
                        "payload_hex": payload_hex,
                        "payload_bytes": len(payload),
                        "response_kind": response_kind,
                        "response_hex": response.hex(),
                        "response_bytes": len(response),
                        "elapsed_ms": elapsed_ms,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        handle.log(
            phase="load",
            primitive="shim_peer_raw_bytes",
            level="info",
            event="completed",
            payload={
                "outcome": "ok",
                "target_node": target_node,
                "target_host": host,
                "target_port": port,
                "payload_hex": payload_hex,
                "payload_bytes": len(payload),
                "response_kind": response_kind,
                "response_hex": response.hex(),
                "response_bytes": len(response),
                "elapsed_ms": elapsed_ms,
            },
        )


class ShimPeerMalformedHandshake(LoadPrimitive):
    """Connect to one runtime node and send a malformed node-to-node handshake SDU.

    Parameters:
      runtime_metadata_path    — runtime.json path for a generated/devnet profile
      target_node              — named node from runtime metadata
      malformation_id          — known malformed handshake case id
      target_host              — optional host override (default 127.0.0.1)
      response_timeout_seconds — optional read timeout after write/shutdown (default 2)
      receive_bytes            — optional recv size cap (default 64)
    """

    _HANDSHAKE_CASES = {
        "bad-v11-short-version-data": "83010b82182af4",
        "bad-peer-sharing-out-of-range": "83010b84182af402f4",
        "bad-refuse-shape": "820282090b",
        "trailing-after-query-reply": "8203a10a82182af400",
        "unsupported-version-999": "8200a11903e782182af4",
    }

    def _runtime_node(self):
        metadata_path = Path(self.params["runtime_metadata_path"])
        if not metadata_path.exists():
            raise ValueError(f"shim_peer_malformed_handshake runtime metadata missing: {metadata_path}")
        body = json.loads(metadata_path.read_text(encoding="utf-8"))
        target_node = str(self.params["target_node"])
        nodes = (body.get("haskell_nodes") or []) + (body.get("amaru_nodes") or [])
        for node in nodes:
            if str(node.get("name")) == target_node:
                return metadata_path, target_node, dict(node)
        raise ValueError(f"shim_peer_malformed_handshake could not find target_node {target_node!r} in {metadata_path}")

    @staticmethod
    def _encode_mux_sdu(payload: bytes, *, mini_protocol_num: int = 0, initiator: bool = True, timestamp: int = 0) -> bytes:
        import struct

        if mini_protocol_num < 0 or mini_protocol_num > 0x7FFF:
            raise ValueError(f"mini_protocol_num out of range for mux header: {mini_protocol_num}")
        if len(payload) > 0xFFFF:
            raise ValueError(f"payload too large for one mux SDU: {len(payload)} bytes")
        header_word = ((1 if initiator else 0) << 31) | ((mini_protocol_num & 0x7FFF) << 16) | (len(payload) & 0xFFFF)
        return struct.pack(">II", timestamp & 0xFFFFFFFF, header_word) + payload

    def run(self, handle, rng):
        import socket
        import time

        metadata_path, target_node, node = self._runtime_node()
        host = str(self.params.get("target_host", "127.0.0.1"))
        port = int(node["port"])
        malformation_id = str(self.params["malformation_id"])
        payload_hex = self._HANDSHAKE_CASES.get(malformation_id)
        if payload_hex is None:
            raise ValueError(
                f"unknown malformed handshake case {malformation_id!r}; known cases: {sorted(self._HANDSHAKE_CASES)}"
            )
        response_timeout = float(self.params.get("response_timeout_seconds", 2))
        receive_bytes = int(self.params.get("receive_bytes", 64))
        payload = bytes.fromhex(payload_hex)
        frame = self._encode_mux_sdu(payload)

        handle.log(
            phase="load",
            primitive="shim_peer_malformed_handshake",
            level="info",
            event="started",
            payload={
                "runtime_metadata_path": str(metadata_path),
                "target_node": target_node,
                "target_host": host,
                "target_port": port,
                "malformation_id": malformation_id,
                "handshake_payload_hex": payload_hex,
                "handshake_payload_bytes": len(payload),
                "frame_hex": frame.hex(),
                "frame_bytes": len(frame),
            },
        )

        started = time.perf_counter()
        response_kind = "unknown"
        response = b""
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.settimeout(response_timeout)
            sock.sendall(frame)
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            try:
                response = sock.recv(receive_bytes)
                response_kind = "data" if response else "eof"
            except socket.timeout:
                response_kind = "timeout"
                response = b""
            except ConnectionResetError:
                response_kind = "reset"
                response = b""

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if getattr(handle, "run_dir", None) is not None:
            output_dir = Path(handle.run_dir) / "outputs" / "shim-peer-malformed-handshake"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / f"{target_node}.json").write_text(
                json.dumps(
                    {
                        "target_node": target_node,
                        "target_host": host,
                        "target_port": port,
                        "malformation_id": malformation_id,
                        "handshake_payload_hex": payload_hex,
                        "handshake_payload_bytes": len(payload),
                        "frame_hex": frame.hex(),
                        "frame_bytes": len(frame),
                        "response_kind": response_kind,
                        "response_hex": response.hex(),
                        "response_bytes": len(response),
                        "elapsed_ms": elapsed_ms,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            _append_target_hook_event(
                handle,
                primitive="shim_peer_malformed_handshake",
                event="malformed_handshake_result",
                payload={
                    "target_node": target_node,
                    "target_host": host,
                    "target_port": port,
                    "malformation_id": malformation_id,
                    "handshake_payload_hex": payload_hex,
                    "handshake_payload_bytes": len(payload),
                    "frame_hex": frame.hex(),
                    "frame_bytes": len(frame),
                    "response_kind": response_kind,
                    "response_hex": response.hex(),
                    "response_bytes": len(response),
                    "elapsed_ms": elapsed_ms,
                },
            )

        handle.log(
            phase="load",
            primitive="shim_peer_malformed_handshake",
            level="info",
            event="completed",
            payload={
                "outcome": "ok",
                "target_node": target_node,
                "target_host": host,
                "target_port": port,
                "malformation_id": malformation_id,
                "handshake_payload_hex": payload_hex,
                "handshake_payload_bytes": len(payload),
                "frame_hex": frame.hex(),
                "frame_bytes": len(frame),
                "response_kind": response_kind,
                "response_hex": response.hex(),
                "response_bytes": len(response),
                "elapsed_ms": elapsed_ms,
            },
        )


class ShimPeerMalformedBlockfetch(LoadPrimitive):
    """Complete a valid handshake, then send a malformed BlockFetch request SDU.

    Parameters:
      runtime_metadata_path    — runtime.json path for a generated/devnet profile
      target_node              — named node from runtime metadata
      malformation_id          — known malformed blockfetch case id
      target_host              — optional host override (default 127.0.0.1)
      response_timeout_seconds — optional read timeout after write/shutdown (default 2)
      receive_bytes            — optional recv size cap (default 64)
    """

    _HANDSHAKE_PROPOSE_HEX = "8200a10a82182af4"
    _BLOCKFETCH_CASES = {
        "invalid-point-hash-short": "83008201581f33333333333333333333333333333333333333333333333333333333333333820158201111111111111111111111111111111111111111111111111111111111111111",
        "invalid-point-array-len-one": "83008101820158201111111111111111111111111111111111111111111111111111111111111111",
        "block-wrong-tag": "8204d81744a1617801",
        "block-not-tagged-bytes": "820400",
        "trailing-bytes-after-batch-done": "810500",
    }

    def _runtime_node(self):
        metadata_path = Path(self.params["runtime_metadata_path"])
        if not metadata_path.exists():
            raise ValueError(f"shim_peer_malformed_blockfetch runtime metadata missing: {metadata_path}")
        body = json.loads(metadata_path.read_text(encoding="utf-8"))
        target_node = str(self.params["target_node"])
        nodes = (body.get("haskell_nodes") or []) + (body.get("amaru_nodes") or [])
        for node in nodes:
            if str(node.get("name")) == target_node:
                return metadata_path, target_node, dict(node)
        raise ValueError(f"shim_peer_malformed_blockfetch could not find target_node {target_node!r} in {metadata_path}")

    @staticmethod
    def _encode_mux_sdu(payload: bytes, *, mini_protocol_num: int, initiator: bool = True, timestamp: int = 0) -> bytes:
        import struct

        if mini_protocol_num < 0 or mini_protocol_num > 0x7FFF:
            raise ValueError(f"mini_protocol_num out of range for mux header: {mini_protocol_num}")
        if len(payload) > 0xFFFF:
            raise ValueError(f"payload too large for one mux SDU: {len(payload)} bytes")
        header_word = ((1 if initiator else 0) << 31) | ((mini_protocol_num & 0x7FFF) << 16) | (len(payload) & 0xFFFF)
        return struct.pack(">II", timestamp & 0xFFFFFFFF, header_word) + payload

    def run(self, handle, rng):
        import socket
        import time

        metadata_path, target_node, node = self._runtime_node()
        host = str(self.params.get("target_host", "127.0.0.1"))
        port = int(node["port"])
        malformation_id = str(self.params["malformation_id"])
        blockfetch_payload_hex = self._BLOCKFETCH_CASES.get(malformation_id)
        if blockfetch_payload_hex is None:
            raise ValueError(
                f"unknown malformed blockfetch case {malformation_id!r}; known cases: {sorted(self._BLOCKFETCH_CASES)}"
            )
        response_timeout = float(self.params.get("response_timeout_seconds", 2))
        receive_bytes = int(self.params.get("receive_bytes", 64))
        handshake_payload_hex = self._HANDSHAKE_PROPOSE_HEX
        handshake_payload = bytes.fromhex(handshake_payload_hex)
        blockfetch_payload = bytes.fromhex(blockfetch_payload_hex)
        handshake_frame = self._encode_mux_sdu(handshake_payload, mini_protocol_num=0)
        blockfetch_frame = self._encode_mux_sdu(blockfetch_payload, mini_protocol_num=3)

        handle.log(
            phase="load",
            primitive="shim_peer_malformed_blockfetch",
            level="info",
            event="started",
            payload={
                "runtime_metadata_path": str(metadata_path),
                "target_node": target_node,
                "target_host": host,
                "target_port": port,
                "malformation_id": malformation_id,
                "handshake_payload_hex": handshake_payload_hex,
                "handshake_payload_bytes": len(handshake_payload),
                "handshake_frame_hex": handshake_frame.hex(),
                "handshake_frame_bytes": len(handshake_frame),
                "blockfetch_payload_hex": blockfetch_payload_hex,
                "blockfetch_payload_bytes": len(blockfetch_payload),
                "blockfetch_frame_hex": blockfetch_frame.hex(),
                "blockfetch_frame_bytes": len(blockfetch_frame),
            },
        )

        started = time.perf_counter()
        handshake_response_kind = "unknown"
        handshake_response = b""
        blockfetch_response_kind = "unknown"
        blockfetch_response = b""
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.settimeout(response_timeout)
            sock.sendall(handshake_frame)
            try:
                handshake_response = sock.recv(receive_bytes)
                handshake_response_kind = "data" if handshake_response else "eof"
            except socket.timeout:
                handshake_response_kind = "timeout"
                handshake_response = b""
            except ConnectionResetError:
                handshake_response_kind = "reset"
                handshake_response = b""
            if handshake_response_kind != "data":
                raise RuntimeError(
                    f"valid handshake proposal did not produce a data response; got {handshake_response_kind}"
                )
            sock.sendall(blockfetch_frame)
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            try:
                blockfetch_response = sock.recv(receive_bytes)
                blockfetch_response_kind = "data" if blockfetch_response else "eof"
            except socket.timeout:
                blockfetch_response_kind = "timeout"
                blockfetch_response = b""
            except ConnectionResetError:
                blockfetch_response_kind = "reset"
                blockfetch_response = b""

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if getattr(handle, "run_dir", None) is not None:
            output_dir = Path(handle.run_dir) / "outputs" / "shim-peer-malformed-blockfetch"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / f"{target_node}.json").write_text(
                json.dumps(
                    {
                        "target_node": target_node,
                        "target_host": host,
                        "target_port": port,
                        "malformation_id": malformation_id,
                        "handshake_payload_hex": handshake_payload_hex,
                        "handshake_payload_bytes": len(handshake_payload),
                        "handshake_frame_hex": handshake_frame.hex(),
                        "handshake_frame_bytes": len(handshake_frame),
                        "handshake_response_kind": handshake_response_kind,
                        "handshake_response_hex": handshake_response.hex(),
                        "handshake_response_bytes": len(handshake_response),
                        "blockfetch_payload_hex": blockfetch_payload_hex,
                        "blockfetch_payload_bytes": len(blockfetch_payload),
                        "blockfetch_frame_hex": blockfetch_frame.hex(),
                        "blockfetch_frame_bytes": len(blockfetch_frame),
                        "blockfetch_response_kind": blockfetch_response_kind,
                        "blockfetch_response_hex": blockfetch_response.hex(),
                        "blockfetch_response_bytes": len(blockfetch_response),
                        "elapsed_ms": elapsed_ms,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            _append_target_hook_event(
                handle,
                primitive="shim_peer_malformed_blockfetch",
                event="malformed_blockfetch_result",
                payload={
                    "target_node": target_node,
                    "target_host": host,
                    "target_port": port,
                    "malformation_id": malformation_id,
                    "handshake_payload_hex": handshake_payload_hex,
                    "handshake_payload_bytes": len(handshake_payload),
                    "handshake_frame_hex": handshake_frame.hex(),
                    "handshake_frame_bytes": len(handshake_frame),
                    "handshake_response_kind": handshake_response_kind,
                    "handshake_response_hex": handshake_response.hex(),
                    "handshake_response_bytes": len(handshake_response),
                    "blockfetch_payload_hex": blockfetch_payload_hex,
                    "blockfetch_payload_bytes": len(blockfetch_payload),
                    "blockfetch_frame_hex": blockfetch_frame.hex(),
                    "blockfetch_frame_bytes": len(blockfetch_frame),
                    "blockfetch_response_kind": blockfetch_response_kind,
                    "blockfetch_response_hex": blockfetch_response.hex(),
                    "blockfetch_response_bytes": len(blockfetch_response),
                    "elapsed_ms": elapsed_ms,
                },
            )

        handle.log(
            phase="load",
            primitive="shim_peer_malformed_blockfetch",
            level="info",
            event="completed",
            payload={
                "outcome": "ok",
                "target_node": target_node,
                "target_host": host,
                "target_port": port,
                "malformation_id": malformation_id,
                "handshake_payload_hex": handshake_payload_hex,
                "handshake_payload_bytes": len(handshake_payload),
                "handshake_frame_hex": handshake_frame.hex(),
                "handshake_frame_bytes": len(handshake_frame),
                "handshake_response_kind": handshake_response_kind,
                "handshake_response_hex": handshake_response.hex(),
                "handshake_response_bytes": len(handshake_response),
                "blockfetch_payload_hex": blockfetch_payload_hex,
                "blockfetch_payload_bytes": len(blockfetch_payload),
                "blockfetch_frame_hex": blockfetch_frame.hex(),
                "blockfetch_frame_bytes": len(blockfetch_frame),
                "blockfetch_response_kind": blockfetch_response_kind,
                "blockfetch_response_hex": blockfetch_response.hex(),
                "blockfetch_response_bytes": len(blockfetch_response),
                "elapsed_ms": elapsed_ms,
            },
        )


class ShimPeerMalformedTxsubmission(LoadPrimitive):
    """Complete a valid handshake, then send a malformed TxSubmission SDU.

    Parameters:
      runtime_metadata_path    — runtime.json path for a generated/devnet profile
      target_node              — named node from runtime metadata
      malformation_id          — known malformed txsubmission case id
      target_host              — optional host override (default 127.0.0.1)
      response_timeout_seconds — optional read timeout after write/shutdown (default 2)
      receive_bytes            — optional recv size cap (default 64)
    """

    _HANDSHAKE_PROPOSE_HEX = "8200a10a82182af4"
    _TXSUBMISSION_CASES = {
        "bad-requesttxids-shape": "8300f501",
        "bad-txid-size-pair-len-1": "8201818158201111111111111111111111111111111111111111111111111111111111111111",
        "bad-replytxids-payload-not-list": "820107",
        "trailing-bytes-after-done": "810400",
        "bad-tx-size-u32-overflow": "82018182582011111111111111111111111111111111111111111111111111111111111111111b0000000100000000",
    }

    def _runtime_node(self):
        metadata_path = Path(self.params["runtime_metadata_path"])
        if not metadata_path.exists():
            raise ValueError(f"shim_peer_malformed_txsubmission runtime metadata missing: {metadata_path}")
        body = json.loads(metadata_path.read_text(encoding="utf-8"))
        target_node = str(self.params["target_node"])
        nodes = (body.get("haskell_nodes") or []) + (body.get("amaru_nodes") or [])
        for node in nodes:
            if str(node.get("name")) == target_node:
                return metadata_path, target_node, dict(node)
        raise ValueError(
            f"shim_peer_malformed_txsubmission could not find target_node {target_node!r} in {metadata_path}"
        )

    @staticmethod
    def _encode_mux_sdu(payload: bytes, *, mini_protocol_num: int, initiator: bool = True, timestamp: int = 0) -> bytes:
        import struct

        if mini_protocol_num < 0 or mini_protocol_num > 0x7FFF:
            raise ValueError(f"mini_protocol_num out of range for mux header: {mini_protocol_num}")
        if len(payload) > 0xFFFF:
            raise ValueError(f"payload too large for one mux SDU: {len(payload)} bytes")
        header_word = ((1 if initiator else 0) << 31) | ((mini_protocol_num & 0x7FFF) << 16) | (len(payload) & 0xFFFF)
        return struct.pack(">II", timestamp & 0xFFFFFFFF, header_word) + payload

    def run(self, handle, rng):
        import socket
        import time

        metadata_path, target_node, node = self._runtime_node()
        host = str(self.params.get("target_host", "127.0.0.1"))
        port = int(node["port"])
        malformation_id = str(self.params["malformation_id"])
        txsubmission_payload_hex = self._TXSUBMISSION_CASES.get(malformation_id)
        if txsubmission_payload_hex is None:
            raise ValueError(
                f"unknown malformed txsubmission case {malformation_id!r}; known cases: {sorted(self._TXSUBMISSION_CASES)}"
            )
        response_timeout = float(self.params.get("response_timeout_seconds", 2))
        receive_bytes = int(self.params.get("receive_bytes", 64))
        handshake_payload_hex = self._HANDSHAKE_PROPOSE_HEX
        handshake_payload = bytes.fromhex(handshake_payload_hex)
        txsubmission_payload = bytes.fromhex(txsubmission_payload_hex)
        handshake_frame = self._encode_mux_sdu(handshake_payload, mini_protocol_num=0)
        txsubmission_frame = self._encode_mux_sdu(txsubmission_payload, mini_protocol_num=4)

        handle.log(
            phase="load",
            primitive="shim_peer_malformed_txsubmission",
            level="info",
            event="started",
            payload={
                "runtime_metadata_path": str(metadata_path),
                "target_node": target_node,
                "target_host": host,
                "target_port": port,
                "malformation_id": malformation_id,
                "handshake_payload_hex": handshake_payload_hex,
                "handshake_payload_bytes": len(handshake_payload),
                "handshake_frame_hex": handshake_frame.hex(),
                "handshake_frame_bytes": len(handshake_frame),
                "txsubmission_payload_hex": txsubmission_payload_hex,
                "txsubmission_payload_bytes": len(txsubmission_payload),
                "txsubmission_frame_hex": txsubmission_frame.hex(),
                "txsubmission_frame_bytes": len(txsubmission_frame),
            },
        )

        started = time.perf_counter()
        handshake_response_kind = "unknown"
        handshake_response = b""
        txsubmission_response_kind = "unknown"
        txsubmission_response = b""
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.settimeout(response_timeout)
            sock.sendall(handshake_frame)
            try:
                handshake_response = sock.recv(receive_bytes)
                handshake_response_kind = "data" if handshake_response else "eof"
            except socket.timeout:
                handshake_response_kind = "timeout"
                handshake_response = b""
            except ConnectionResetError:
                handshake_response_kind = "reset"
                handshake_response = b""
            if handshake_response_kind != "data":
                raise RuntimeError(
                    f"valid handshake proposal did not produce a data response; got {handshake_response_kind}"
                )
            sock.sendall(txsubmission_frame)
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            try:
                txsubmission_response = sock.recv(receive_bytes)
                txsubmission_response_kind = "data" if txsubmission_response else "eof"
            except socket.timeout:
                txsubmission_response_kind = "timeout"
                txsubmission_response = b""
            except ConnectionResetError:
                txsubmission_response_kind = "reset"
                txsubmission_response = b""

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if getattr(handle, "run_dir", None) is not None:
            output_dir = Path(handle.run_dir) / "outputs" / "shim-peer-malformed-txsubmission"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / f"{target_node}.json").write_text(
                json.dumps(
                    {
                        "target_node": target_node,
                        "target_host": host,
                        "target_port": port,
                        "malformation_id": malformation_id,
                        "handshake_payload_hex": handshake_payload_hex,
                        "handshake_payload_bytes": len(handshake_payload),
                        "handshake_frame_hex": handshake_frame.hex(),
                        "handshake_frame_bytes": len(handshake_frame),
                        "handshake_response_kind": handshake_response_kind,
                        "handshake_response_hex": handshake_response.hex(),
                        "handshake_response_bytes": len(handshake_response),
                        "txsubmission_payload_hex": txsubmission_payload_hex,
                        "txsubmission_payload_bytes": len(txsubmission_payload),
                        "txsubmission_frame_hex": txsubmission_frame.hex(),
                        "txsubmission_frame_bytes": len(txsubmission_frame),
                        "txsubmission_response_kind": txsubmission_response_kind,
                        "txsubmission_response_hex": txsubmission_response.hex(),
                        "txsubmission_response_bytes": len(txsubmission_response),
                        "elapsed_ms": elapsed_ms,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            _append_target_hook_event(
                handle,
                primitive="shim_peer_malformed_txsubmission",
                event="malformed_txsubmission_result",
                payload={
                    "target_node": target_node,
                    "target_host": host,
                    "target_port": port,
                    "malformation_id": malformation_id,
                    "handshake_payload_hex": handshake_payload_hex,
                    "handshake_payload_bytes": len(handshake_payload),
                    "handshake_frame_hex": handshake_frame.hex(),
                    "handshake_frame_bytes": len(handshake_frame),
                    "handshake_response_kind": handshake_response_kind,
                    "handshake_response_hex": handshake_response.hex(),
                    "handshake_response_bytes": len(handshake_response),
                    "txsubmission_payload_hex": txsubmission_payload_hex,
                    "txsubmission_payload_bytes": len(txsubmission_payload),
                    "txsubmission_frame_hex": txsubmission_frame.hex(),
                    "txsubmission_frame_bytes": len(txsubmission_frame),
                    "txsubmission_response_kind": txsubmission_response_kind,
                    "txsubmission_response_hex": txsubmission_response.hex(),
                    "txsubmission_response_bytes": len(txsubmission_response),
                    "elapsed_ms": elapsed_ms,
                },
            )

        handle.log(
            phase="load",
            primitive="shim_peer_malformed_txsubmission",
            level="info",
            event="completed",
            payload={
                "outcome": "ok",
                "target_node": target_node,
                "target_host": host,
                "target_port": port,
                "malformation_id": malformation_id,
                "handshake_payload_hex": handshake_payload_hex,
                "handshake_payload_bytes": len(handshake_payload),
                "handshake_frame_hex": handshake_frame.hex(),
                "handshake_frame_bytes": len(handshake_frame),
                "handshake_response_kind": handshake_response_kind,
                "handshake_response_hex": handshake_response.hex(),
                "handshake_response_bytes": len(handshake_response),
                "txsubmission_payload_hex": txsubmission_payload_hex,
                "txsubmission_payload_bytes": len(txsubmission_payload),
                "txsubmission_frame_hex": txsubmission_frame.hex(),
                "txsubmission_frame_bytes": len(txsubmission_frame),
                "txsubmission_response_kind": txsubmission_response_kind,
                "txsubmission_response_hex": txsubmission_response.hex(),
                "txsubmission_response_bytes": len(txsubmission_response),
                "elapsed_ms": elapsed_ms,
            },
        )


class ShimPeerInvalidCbor(LoadPrimitive):
    """Send invalid CBOR bytes inside a valid mux SDU and capture rejection.

    The first slice targets the handshake mini-protocol because a fresh node-to-node
    connection necessarily enters handshake first. This still exercises the codec
    layer rather than a post-handshake protocol state machine.

    Parameters:
      runtime_metadata_path    — runtime.json path for a generated/devnet profile
      target_node              — named node from runtime metadata
      invalid_cbor_id          — known invalid-CBOR case id
      target_host              — optional host override (default 127.0.0.1)
      response_timeout_seconds — optional read timeout after write/shutdown (default 2)
      receive_bytes            — optional recv size cap (default 64)
    """

    _INVALID_CBOR_CASES = {
        "truncated-handshake-propose-v10": {
            "mini_protocol_num": 0,
            "payload_hex": "8200a10a82",
            "notes": "Starts a valid handshake propose-v10 envelope, then truncates inside version_data.",
        },
        "truncated-handshake-map-value": {
            "mini_protocol_num": 0,
            "payload_hex": "8200a10a",
            "notes": "Starts the version table map but omits the value for version 10.",
        },
        "indefinite-handshake-no-break": {
            "mini_protocol_num": 0,
            "payload_hex": "9f00ff",
            "notes": "Invalid handshake envelope shape using an indefinite-length list without a valid message body.",
        },
    }

    def _runtime_node(self):
        metadata_path = Path(self.params["runtime_metadata_path"])
        if not metadata_path.exists():
            raise ValueError(f"shim_peer_invalid_cbor runtime metadata missing: {metadata_path}")
        body = json.loads(metadata_path.read_text(encoding="utf-8"))
        target_node = str(self.params["target_node"])
        nodes = (body.get("haskell_nodes") or []) + (body.get("amaru_nodes") or [])
        for node in nodes:
            if str(node.get("name")) == target_node:
                return metadata_path, target_node, dict(node)
        raise ValueError(f"shim_peer_invalid_cbor could not find target_node {target_node!r} in {metadata_path}")

    @staticmethod
    def _encode_mux_sdu(payload: bytes, *, mini_protocol_num: int, initiator: bool = True, timestamp: int = 0) -> bytes:
        import struct

        if mini_protocol_num < 0 or mini_protocol_num > 0x7FFF:
            raise ValueError(f"mini_protocol_num out of range for mux header: {mini_protocol_num}")
        if len(payload) > 0xFFFF:
            raise ValueError(f"payload too large for one mux SDU: {len(payload)} bytes")
        header_word = ((1 if initiator else 0) << 31) | ((mini_protocol_num & 0x7FFF) << 16) | (len(payload) & 0xFFFF)
        return struct.pack(">II", timestamp & 0xFFFFFFFF, header_word) + payload

    def run(self, handle, rng):
        import socket
        import time

        metadata_path, target_node, node = self._runtime_node()
        host = str(self.params.get("target_host", "127.0.0.1"))
        port = int(node["port"])
        invalid_cbor_id = str(self.params["invalid_cbor_id"])
        case = self._INVALID_CBOR_CASES.get(invalid_cbor_id)
        if case is None:
            raise ValueError(
                f"unknown invalid_cbor case {invalid_cbor_id!r}; known cases: {sorted(self._INVALID_CBOR_CASES)}"
            )
        response_timeout = float(self.params.get("response_timeout_seconds", 2))
        receive_bytes = int(self.params.get("receive_bytes", 64))
        mini_protocol_num = int(case["mini_protocol_num"])
        payload_hex = str(case["payload_hex"])
        payload = bytes.fromhex(payload_hex)
        frame = self._encode_mux_sdu(payload, mini_protocol_num=mini_protocol_num)

        handle.log(
            phase="load",
            primitive="shim_peer_invalid_cbor",
            level="info",
            event="started",
            payload={
                "runtime_metadata_path": str(metadata_path),
                "target_node": target_node,
                "target_host": host,
                "target_port": port,
                "invalid_cbor_id": invalid_cbor_id,
                "mini_protocol_num": mini_protocol_num,
                "payload_hex": payload_hex,
                "payload_bytes": len(payload),
                "frame_hex": frame.hex(),
                "frame_bytes": len(frame),
            },
        )

        started = time.perf_counter()
        response_kind = "unknown"
        response = b""
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.settimeout(response_timeout)
            sock.sendall(frame)
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            try:
                response = sock.recv(receive_bytes)
                response_kind = "data" if response else "eof"
            except socket.timeout:
                response_kind = "timeout"
                response = b""
            except ConnectionResetError:
                response_kind = "reset"
                response = b""

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if getattr(handle, "run_dir", None) is not None:
            output_dir = Path(handle.run_dir) / "outputs" / "shim-peer-invalid-cbor"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / f"{target_node}.json").write_text(
                json.dumps(
                    {
                        "target_node": target_node,
                        "target_host": host,
                        "target_port": port,
                        "invalid_cbor_id": invalid_cbor_id,
                        "mini_protocol_num": mini_protocol_num,
                        "payload_hex": payload_hex,
                        "payload_bytes": len(payload),
                        "frame_hex": frame.hex(),
                        "frame_bytes": len(frame),
                        "response_kind": response_kind,
                        "response_hex": response.hex(),
                        "response_bytes": len(response),
                        "elapsed_ms": elapsed_ms,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            _append_target_hook_event(
                handle,
                primitive="shim_peer_invalid_cbor",
                event="invalid_cbor_result",
                payload={
                    "target_node": target_node,
                    "target_host": host,
                    "target_port": port,
                    "invalid_cbor_id": invalid_cbor_id,
                    "mini_protocol_num": mini_protocol_num,
                    "payload_hex": payload_hex,
                    "payload_bytes": len(payload),
                    "frame_hex": frame.hex(),
                    "frame_bytes": len(frame),
                    "response_kind": response_kind,
                    "response_hex": response.hex(),
                    "response_bytes": len(response),
                    "elapsed_ms": elapsed_ms,
                },
            )

        handle.log(
            phase="load",
            primitive="shim_peer_invalid_cbor",
            level="info",
            event="completed",
            payload={
                "outcome": "ok",
                "target_node": target_node,
                "target_host": host,
                "target_port": port,
                "invalid_cbor_id": invalid_cbor_id,
                "mini_protocol_num": mini_protocol_num,
                "payload_hex": payload_hex,
                "payload_bytes": len(payload),
                "frame_hex": frame.hex(),
                "frame_bytes": len(frame),
                "response_kind": response_kind,
                "response_hex": response.hex(),
                "response_bytes": len(response),
                "elapsed_ms": elapsed_ms,
            },
        )


class ShimResponderStaleBlockfetch(LoadPrimitive):
    """Run the existing stale-response BlockFetch harness as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_blockfetch_stale_check.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        import re

        match = re.search(
            r"point=(?P<point>\S+)\s+server_port=(?P<server_port>\d+)\s+client_exit=(?P<client_exit>[-\w]+)\s+expected_mismatch=(?P<expected_mismatch>\d+)",
            stdout or "",
        )
        if not match:
            return {}
        body = match.groupdict()
        return {
            "point": body["point"],
            "server_port": int(body["server_port"]),
            "client_exit": body["client_exit"],
            "expected_mismatch": body["expected_mismatch"] == "1",
        }

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 30))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))

        handle.log(
            phase="load",
            primitive="shim_responder_stale_blockfetch",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
            **self._parse_summary(stdout),
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "shim-responder-stale-blockfetch"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="shim_responder_stale_blockfetch",
                event="stale_blockfetch_responder_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="shim_responder_stale_blockfetch",
            level="info",
            event="completed",
            payload=payload,
        )


class RuntimeClientBlockfetchBurst(LoadPrimitive):
    """Run the existing blockfetch-burst helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_fetch_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 180))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "blockfetch-burst"

        handle.log(
            phase="load",
            primitive="runtime_client_blockfetch_burst",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script, mode],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-client-blockfetch-burst"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_client_blockfetch_burst",
                event="blockfetch_burst_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_client_blockfetch_burst",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeClientChainsyncBurst(LoadPrimitive):
    """Run the existing chainsync-burst helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_fetch_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 180))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "chainsync-burst"

        handle.log(
            phase="load",
            primitive="runtime_client_chainsync_burst",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script, mode],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-client-chainsync-burst"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_client_chainsync_burst",
                event="chainsync_burst_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_client_chainsync_burst",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeClientChainsyncMultiPeer(LoadPrimitive):
    """Run the existing chainsync-multi-peer helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_fetch_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 180))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "chainsync-multi-peer"

        handle.log(
            phase="load",
            primitive="runtime_client_chainsync_multi_peer",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script, mode],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-client-chainsync-multi-peer"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_client_chainsync_multi_peer",
                event="chainsync_multi_peer_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_client_chainsync_multi_peer",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeClientBlockfetchMultiPeer(LoadPrimitive):
    """Run the existing blockfetch-multi-peer helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_fetch_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 180))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "blockfetch-multi-peer"

        handle.log(
            phase="load",
            primitive="runtime_client_blockfetch_multi_peer",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script, mode],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-client-blockfetch-multi-peer"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_client_blockfetch_multi_peer",
                event="blockfetch_multi_peer_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_client_blockfetch_multi_peer",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeProfileCopiedStateDivergence(LoadPrimitive):
    """Run the existing copied-state-divergence helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_profile_recovery_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 420))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "copied-state-divergence"

        handle.log(
            phase="load",
            primitive="runtime_profile_copied_state_divergence",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script, mode],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-profile-copied-state-divergence"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_profile_copied_state_divergence",
                event="profile_copied_state_divergence_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_profile_copied_state_divergence",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeProfileCopiedStateChainsyncDivergence(LoadPrimitive):
    """Run the existing copied-state-chainsync-divergence helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_profile_recovery_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 420))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "copied-state-chainsync-divergence"

        handle.log(
            phase="load",
            primitive="runtime_profile_copied_state_chainsync_divergence",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script, mode],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-profile-copied-state-chainsync-divergence"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_profile_copied_state_chainsync_divergence",
                event="profile_copied_state_chainsync_divergence_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_profile_copied_state_chainsync_divergence",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeBlockfetchDropTimeout(LoadPrimitive):
    """Run the existing blockfetch port-fault drop-timeout helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_blockfetch_port_fault_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 60))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "drop-timeout"

        handle.log(
            phase="load",
            primitive="runtime_blockfetch_drop_timeout",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script, mode],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-blockfetch-drop-timeout"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_blockfetch_drop_timeout",
                event="blockfetch_drop_timeout_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_blockfetch_drop_timeout",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeBlockfetchDropIsolatedPeer(LoadPrimitive):
    """Run the existing blockfetch port-fault drop-isolated-peer helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_blockfetch_port_fault_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 60))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "drop-isolated-peer"

        handle.log(
            phase="load",
            primitive="runtime_blockfetch_drop_isolated_peer",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script, mode],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-blockfetch-drop-isolated-peer"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_blockfetch_drop_isolated_peer",
                event="blockfetch_drop_isolated_peer_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_blockfetch_drop_isolated_peer",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeBlockfetchDelaySuccess(LoadPrimitive):
    """Run the existing blockfetch port-fault delay-success helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_blockfetch_port_fault_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 60))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "delay-success"

        handle.log(
            phase="load",
            primitive="runtime_blockfetch_delay_success",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script, mode],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-blockfetch-delay-success"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_blockfetch_delay_success",
                event="blockfetch_delay_success_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_blockfetch_delay_success",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeBlockfetchDelayTimeout(LoadPrimitive):
    """Run the existing blockfetch port-fault delay-timeout helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_blockfetch_port_fault_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 60))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "delay-timeout"

        handle.log(
            phase="load",
            primitive="runtime_blockfetch_delay_timeout",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script, mode],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-blockfetch-delay-timeout"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_blockfetch_delay_timeout",
                event="blockfetch_delay_timeout_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_blockfetch_delay_timeout",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeObservabilityLogBaseline(LoadPrimitive):
    """Run the existing observability log-baseline helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_observability_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 60))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "log-baseline"

        handle.log(
            phase="load",
            primitive="runtime_observability_log_baseline",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script, mode],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-observability-log-baseline"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_observability_log_baseline",
                event="observability_log_baseline_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_observability_log_baseline",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeObservabilityTraceSettingsBaseline(LoadPrimitive):
    """Run the existing observability trace-settings helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_observability_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 60))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "trace-settings-baseline"

        handle.log(
            phase="load",
            primitive="runtime_observability_trace_settings_baseline",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script, mode],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-observability-trace-settings-baseline"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_observability_trace_settings_baseline",
                event="observability_trace_settings_baseline_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_observability_trace_settings_baseline",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeAmaruPreviewProof(LoadPrimitive):
    """Run the existing Amaru preview proof helper as a declarative primitive."""

    _DEFAULT_HELPER = str(DWARF_ROOT / "scripts" / "runtime_amaru_preview_check.py")

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 60))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        runtime_root = str(self.params.get("runtime_root", "/home/nigel/cardano-profiles/profile-d-amaru-preview-proof"))
        sample_seconds = int(self.params.get("sample_seconds", 20))
        mode = "proof"

        handle.log(
            phase="load",
            primitive="runtime_amaru_preview_proof",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "runtime_root": runtime_root,
                "sample_seconds": sample_seconds,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script, mode, "--runtime-root", runtime_root, "--sample-seconds", str(sample_seconds)],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "runtime_root": runtime_root,
            "sample_seconds": sample_seconds,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-amaru-preview-proof"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_amaru_preview_proof",
                event="amaru_preview_proof_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_amaru_preview_proof",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeGeneratedNodeFreezeCheck(LoadPrimitive):
    """Run the existing generated-node freeze-check helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_generated_node_freeze_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        runtime_root = str(self.params["runtime_root"])
        blocked_node = str(self.params["blocked_node"])
        healthy_nodes = [str(node) for node in self.params["healthy_nodes"]]
        sample_seconds = float(self.params.get("sample_seconds", 2.0))
        timeout_seconds = float(self.params.get("timeout_seconds", 60))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "node-freeze-check"

        handle.log(
            phase="load",
            primitive="runtime_generated_node_freeze_check",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "runtime_root": runtime_root,
                "blocked_node": blocked_node,
                "healthy_nodes": healthy_nodes,
                "sample_seconds": sample_seconds,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [
                    python_bin,
                    helper_script,
                    mode,
                    "--runtime-root",
                    runtime_root,
                    "--blocked-node",
                    blocked_node,
                    "--healthy-nodes",
                    ",".join(healthy_nodes),
                    "--sample-seconds",
                    str(sample_seconds),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "runtime_root": runtime_root,
            "blocked_node": blocked_node,
            "healthy_nodes": healthy_nodes,
            "sample_seconds": sample_seconds,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-generated-node-freeze-check"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_generated_node_freeze_check",
                event="generated_node_freeze_check_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_generated_node_freeze_check",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeGeneratedNodeRecoveryCheck(LoadPrimitive):
    """Run the existing generated-node recovery-check helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_generated_node_freeze_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        runtime_root = str(self.params["runtime_root"])
        recovered_node = str(self.params["recovered_node"])
        healthy_nodes = [str(node) for node in self.params["healthy_nodes"]]
        required_phase_id = str(self.params["required_phase_id"])
        sample_seconds = float(self.params.get("sample_seconds", 2.0))
        timeout_seconds = float(self.params.get("timeout_seconds", 60))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "node-recovery-check"

        handle.log(
            phase="load",
            primitive="runtime_generated_node_recovery_check",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "runtime_root": runtime_root,
                "recovered_node": recovered_node,
                "healthy_nodes": healthy_nodes,
                "required_phase_id": required_phase_id,
                "sample_seconds": sample_seconds,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [
                    python_bin,
                    helper_script,
                    mode,
                    "--runtime-root",
                    runtime_root,
                    "--recovered-node",
                    recovered_node,
                    "--healthy-nodes",
                    ",".join(healthy_nodes),
                    "--required-phase-id",
                    required_phase_id,
                    "--sample-seconds",
                    str(sample_seconds),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "runtime_root": runtime_root,
            "recovered_node": recovered_node,
            "healthy_nodes": healthy_nodes,
            "required_phase_id": required_phase_id,
            "sample_seconds": sample_seconds,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-generated-node-recovery-check"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_generated_node_recovery_check",
                event="generated_node_recovery_check_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_generated_node_recovery_check",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeGeneratedNodePortDropCheck(LoadPrimitive):
    """Run the existing generated-node port-drop helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_generated_node_port_fault_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        runtime_root = str(self.params["runtime_root"])
        blocked_node = str(self.params["blocked_node"])
        healthy_nodes = [str(node) for node in self.params["healthy_nodes"]]
        timeout_seconds = float(self.params.get("timeout_seconds", 60))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "node-port-drop-check"

        handle.log(
            phase="load",
            primitive="runtime_generated_node_port_drop_check",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "runtime_root": runtime_root,
                "blocked_node": blocked_node,
                "healthy_nodes": healthy_nodes,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [
                    python_bin,
                    helper_script,
                    mode,
                    "--runtime-root",
                    runtime_root,
                    "--blocked-node",
                    blocked_node,
                    "--healthy-nodes",
                    ",".join(healthy_nodes),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "runtime_root": runtime_root,
            "blocked_node": blocked_node,
            "healthy_nodes": healthy_nodes,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-generated-node-port-drop-check"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_generated_node_port_drop_check",
                event="generated_node_port_drop_check_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_generated_node_port_drop_check",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimePcapCapture(LoadPrimitive):
    """Capture a bounded runtime pcap while driving a stable fetch workload."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_pcap_capture.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        if "interface" in body:
            parsed["interface"] = body["interface"]
        if "workload_mode" in body:
            parsed["workload_mode"] = body["workload_mode"]
        if "pcap_relpath" in body:
            parsed["pcap_relpath"] = body["pcap_relpath"]
        for key in ("packet_count", "pcap_size_bytes", "workload_exit_code", "tcpdump_exit_code", "connect_successes"):
            if key in body:
                try:
                    parsed[key] = int(body[key])
                except ValueError:
                    parsed[key] = body[key]
        return parsed

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 120))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        interface = str(self.params.get("interface", "lo"))
        workload_mode = str(self.params.get("workload_mode", "tcp-connect-burst"))
        target_host = str(self.params.get("target_host", "127.0.0.1"))
        target_ports = [int(port) for port in self.params.get("target_ports", [33001, 33002, 33003])]
        connect_attempts = int(self.params.get("connect_attempts", 3))
        startup_seconds = float(self.params.get("startup_seconds", 1.0))
        settle_seconds = float(self.params.get("settle_seconds", 0.5))

        handle.log(
            phase="load",
            primitive="runtime_pcap_capture",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "interface": interface,
                "workload_mode": workload_mode,
                "target_host": target_host,
                "target_ports": target_ports,
                "connect_attempts": connect_attempts,
                "startup_seconds": startup_seconds,
                "settle_seconds": settle_seconds,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        helper_output_dir = None
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            helper_output_dir = run_dir / "outputs" / "runtime-pcap-capture"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        command = [
            python_bin,
            helper_script,
            "--interface",
            interface,
            "--workload-mode",
            workload_mode,
            "--target-host",
            target_host,
            "--connect-attempts",
            str(connect_attempts),
            "--startup-seconds",
            str(startup_seconds),
            "--settle-seconds",
            str(settle_seconds),
        ]
        if target_ports:
            command.append("--target-ports")
            command.extend(str(port) for port in target_ports)
        if helper_output_dir is not None:
            command.extend(["--output-dir", str(helper_output_dir)])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "interface": interface,
            "workload_mode": workload_mode,
            "target_host": target_host,
            "target_ports": target_ports,
            "connect_attempts": connect_attempts,
            "startup_seconds": startup_seconds,
            "settle_seconds": settle_seconds,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }
        payload.update(self._parse_summary(stdout))

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-pcap-capture"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_pcap_capture",
                event="pcap_capture_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_pcap_capture",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeResourceProfile(LoadPrimitive):
    """Capture bounded /proc resource snapshots for a target runtime process."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_resource_profile.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        for key in ("target_node",):
            if key in body:
                parsed[key] = body[key]
        for key in ("pid", "sample_count", "max_rss_bytes", "max_fd_count", "final_threads"):
            if key in body:
                try:
                    parsed[key] = int(body[key])
                except ValueError:
                    parsed[key] = body[key]
        return parsed

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 120))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        runtime_metadata_path = str(self.params["runtime_metadata_path"])
        target_node = str(self.params.get("target_node", "node1"))
        sample_count = int(self.params.get("sample_count", 5))
        sample_interval_seconds = float(self.params.get("sample_interval_seconds", 0.5))

        handle.log(
            phase="load",
            primitive="runtime_resource_profile",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "runtime_metadata_path": runtime_metadata_path,
                "target_node": target_node,
                "sample_count": sample_count,
                "sample_interval_seconds": sample_interval_seconds,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        helper_output_dir = None
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            helper_output_dir = run_dir / "outputs" / "runtime-resource-profile"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        command = [
            python_bin,
            helper_script,
            "--runtime-metadata-path",
            runtime_metadata_path,
            "--target-node",
            target_node,
            "--sample-count",
            str(sample_count),
            "--sample-interval-seconds",
            str(sample_interval_seconds),
        ]
        if helper_output_dir is not None:
            command.extend(["--output-dir", str(helper_output_dir)])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "runtime_metadata_path": runtime_metadata_path,
            "target_node": target_node,
            "sample_count": sample_count,
            "sample_interval_seconds": sample_interval_seconds,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }
        payload.update(self._parse_summary(stdout))

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-resource-profile"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_resource_profile",
                event="resource_profile_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_resource_profile",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeSyscallTrace(LoadPrimitive):
    """Capture a bounded syscall trace for a target runtime process."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_syscall_trace.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        for key in ("target_node", "top_syscall", "summary_relpath", "trace_relpath"):
            if key in body:
                parsed[key] = body[key]
        for key in ("pid", "port", "total_syscalls", "unique_syscalls", "connect_successes"):
            if key in body:
                try:
                    parsed[key] = int(body[key])
                except ValueError:
                    parsed[key] = body[key]
        return parsed

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 120))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        runtime_metadata_path = str(self.params["runtime_metadata_path"])
        target_node = str(self.params.get("target_node", "node1"))
        connect_attempts = int(self.params.get("connect_attempts", 3))
        target_host = str(self.params.get("target_host", "127.0.0.1"))
        startup_seconds = float(self.params.get("startup_seconds", 1.0))
        settle_seconds = float(self.params.get("settle_seconds", 0.5))

        handle.log(
            phase="load",
            primitive="runtime_syscall_trace",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "runtime_metadata_path": runtime_metadata_path,
                "target_node": target_node,
                "connect_attempts": connect_attempts,
                "target_host": target_host,
                "startup_seconds": startup_seconds,
                "settle_seconds": settle_seconds,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        helper_output_dir = None
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            helper_output_dir = run_dir / "outputs" / "runtime-syscall-trace"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        command = [
            python_bin,
            helper_script,
            "--runtime-metadata-path",
            runtime_metadata_path,
            "--target-node",
            target_node,
            "--connect-attempts",
            str(connect_attempts),
            "--target-host",
            target_host,
            "--startup-seconds",
            str(startup_seconds),
            "--settle-seconds",
            str(settle_seconds),
        ]
        if helper_output_dir is not None:
            command.extend(["--output-dir", str(helper_output_dir)])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "runtime_metadata_path": runtime_metadata_path,
            "target_node": target_node,
            "connect_attempts": connect_attempts,
            "target_host": target_host,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }
        payload.update(self._parse_summary(stdout))

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-syscall-trace"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_syscall_trace",
                event="syscall_trace_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_syscall_trace",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeConnectionState(LoadPrimitive):
    """Capture bounded ss/lsof connection-state snapshots for a target runtime process."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_connection_state.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        for key in ("snapshot_name", "target_node", "summary_relpath", "ss_relpath", "lsof_relpath"):
            if key in body:
                parsed[key] = body[key]
        for key in (
            "pid",
            "port",
            "ss_match_count",
            "ss_listen_count",
            "ss_established_count",
            "lsof_socket_count",
            "connect_successes",
            "connect_failures",
        ):
            if key in body:
                try:
                    parsed[key] = int(body[key])
                except ValueError:
                    parsed[key] = body[key]
        return parsed

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 120))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        runtime_metadata_path = str(self.params["runtime_metadata_path"])
        target_node = str(self.params.get("target_node", "node1"))
        snapshot_name = str(self.params.get("snapshot_name", "snapshot"))
        target_host = str(self.params.get("target_host", "127.0.0.1"))
        connect_attempts = int(self.params.get("connect_attempts", 1))

        handle.log(
            phase="load",
            primitive="runtime_connection_state",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "runtime_metadata_path": runtime_metadata_path,
                "target_node": target_node,
                "snapshot_name": snapshot_name,
                "target_host": target_host,
                "connect_attempts": connect_attempts,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        helper_output_dir = None
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            helper_output_dir = run_dir / "outputs" / "runtime-connection-state" / snapshot_name
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        command = [
            python_bin,
            helper_script,
            "--runtime-metadata-path",
            runtime_metadata_path,
            "--target-node",
            target_node,
            "--snapshot-name",
            snapshot_name,
            "--target-host",
            target_host,
            "--connect-attempts",
            str(connect_attempts),
        ]
        if helper_output_dir is not None:
            command.extend(["--output-dir", str(helper_output_dir)])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "runtime_metadata_path": runtime_metadata_path,
            "target_node": target_node,
            "snapshot_name": snapshot_name,
            "target_host": target_host,
            "connect_attempts": connect_attempts,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }
        payload.update(self._parse_summary(stdout))

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-connection-state" / snapshot_name
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_connection_state",
                event="connection_state_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_connection_state",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeHaskellGcCapture(LoadPrimitive):
    """Capture bounded GHC RTS -s output for a live Haskell runtime node and restore it."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_haskell_gc_capture.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        for key in ("target_node", "summary_relpath", "rts_stderr_relpath"):
            if key in body:
                parsed[key] = body[key]
        for key in (
            "capture_pid",
            "restored_pid",
            "port",
            "connect_successes",
            "total_bytes_allocated",
            "max_residency_bytes",
        ):
            if key in body:
                try:
                    parsed[key] = int(body[key])
                except ValueError:
                    parsed[key] = body[key]
        for key in ("gc_cpu_seconds", "max_pause_seconds"):
            if key in body:
                try:
                    parsed[key] = float(body[key])
                except ValueError:
                    parsed[key] = body[key]
        if "restored_listener_ok" in body:
            parsed["restored_listener_ok"] = str(body["restored_listener_ok"]).lower() == "true"
        return parsed

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 120))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        runtime_metadata_path = str(self.params["runtime_metadata_path"])
        target_node = str(self.params.get("target_node", "node1"))
        target_host = str(self.params.get("target_host", "127.0.0.1"))
        connect_attempts = int(self.params.get("connect_attempts", 2))
        sample_seconds = float(self.params.get("sample_seconds", 2.0))
        startup_timeout_seconds = float(self.params.get("startup_timeout_seconds", 30.0))
        restore_timeout_seconds = float(self.params.get("restore_timeout_seconds", 30.0))

        handle.log(
            phase="load",
            primitive="runtime_haskell_gc_capture",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "runtime_metadata_path": runtime_metadata_path,
                "target_node": target_node,
                "target_host": target_host,
                "connect_attempts": connect_attempts,
                "sample_seconds": sample_seconds,
                "startup_timeout_seconds": startup_timeout_seconds,
                "restore_timeout_seconds": restore_timeout_seconds,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        helper_output_dir = None
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            helper_output_dir = run_dir / "outputs" / "runtime-haskell-gc-capture"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        command = [
            python_bin,
            helper_script,
            "--runtime-metadata-path",
            runtime_metadata_path,
            "--target-node",
            target_node,
            "--target-host",
            target_host,
            "--connect-attempts",
            str(connect_attempts),
            "--sample-seconds",
            str(sample_seconds),
            "--startup-timeout-seconds",
            str(startup_timeout_seconds),
            "--restore-timeout-seconds",
            str(restore_timeout_seconds),
        ]
        if helper_output_dir is not None:
            command.extend(["--output-dir", str(helper_output_dir)])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "runtime_metadata_path": runtime_metadata_path,
            "target_node": target_node,
            "target_host": target_host,
            "connect_attempts": connect_attempts,
            "sample_seconds": sample_seconds,
            "startup_timeout_seconds": startup_timeout_seconds,
            "restore_timeout_seconds": restore_timeout_seconds,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }
        payload.update(self._parse_summary(stdout))

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-haskell-gc-capture"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_haskell_gc_capture",
                event="haskell_gc_capture_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_haskell_gc_capture",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeBundlePromote(LoadPrimitive):
    """Write a structured promotion record into the current run bundle."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_bundle_promote.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        for key in ("target_run_id", "reason_code", "actor", "source_surface", "promotion_timestamp", "promotion_relpath"):
            if key in body:
                parsed[key] = body[key]
        return parsed

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 120))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        target_run_id = self.params.get("target_run_id")
        reason_code = str(self.params["reason_code"])
        reason_text = str(self.params["reason_text"])
        operator_notes = str(self.params.get("operator_notes", ""))
        actor = str(self.params.get("actor", "operator"))
        source_surface = str(self.params.get("source_surface", "scenario-primitive"))

        handle.log(
            phase="load",
            primitive="runtime_bundle_promote",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "target_run_id": target_run_id,
                "reason_code": reason_code,
                "reason_text": reason_text,
                "operator_notes": operator_notes,
                "actor": actor,
                "source_surface": source_surface,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        helper_output_dir = None
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            helper_output_dir = run_dir / "outputs" / "promotion"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        command = [
            python_bin,
            helper_script,
            "--reason-code",
            reason_code,
            "--reason-text",
            reason_text,
            "--operator-notes",
            operator_notes,
            "--actor",
            actor,
            "--source-surface",
            source_surface,
        ]
        if target_run_id:
            command.extend(["--target-run-id", str(target_run_id)])
        if helper_output_dir is not None:
            command.extend(["--output-dir", str(helper_output_dir)])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "target_run_id": target_run_id,
            "reason_code": reason_code,
            "reason_text": reason_text,
            "operator_notes": operator_notes,
            "actor": actor,
            "source_surface": source_surface,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }
        payload.update(self._parse_summary(stdout))

        if run_dir is not None:
            _append_target_hook_event(
                handle,
                primitive="runtime_bundle_promote",
                event="bundle_promotion_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_bundle_promote",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeBundleDedupe(LoadPrimitive):
    """Compare a target run signature against promoted bundles."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_bundle_dedupe.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        for key in ("target_run_id", "signature_primitive", "verdict", "dedupe_relpath"):
            if key in body:
                parsed[key] = body[key]
        if "matched_run_id" in body and body["matched_run_id"] != "none":
            parsed["matched_run_id"] = body["matched_run_id"]
        else:
            parsed["matched_run_id"] = None
        if "promoted_runs_scanned" in body:
            try:
                parsed["promoted_runs_scanned"] = int(body["promoted_runs_scanned"])
            except ValueError:
                parsed["promoted_runs_scanned"] = body["promoted_runs_scanned"]
        return parsed

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 120))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        runs_dir = self.params.get("runs_dir")
        target_run_id = self.params.get("target_run_id")
        signature_primitive = self.params.get("signature_primitive")

        handle.log(
            phase="load",
            primitive="runtime_bundle_dedupe",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "runs_dir": runs_dir,
                "target_run_id": target_run_id,
                "signature_primitive": signature_primitive,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        helper_output_dir = None
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            helper_output_dir = run_dir / "outputs" / "dedupe"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        command = [
            python_bin,
            helper_script,
        ]
        if runs_dir:
            command.extend(["--runs-dir", str(runs_dir)])
        if target_run_id:
            command.extend(["--target-run-id", str(target_run_id)])
        if signature_primitive:
            command.extend(["--signature-primitive", str(signature_primitive)])
        if helper_output_dir is not None:
            command.extend(["--output-dir", str(helper_output_dir)])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "runs_dir": runs_dir,
            "target_run_id": target_run_id,
            "signature_primitive": signature_primitive,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }
        payload.update(self._parse_summary(stdout))

        if run_dir is not None:
            _append_target_hook_event(
                handle,
                primitive="runtime_bundle_dedupe",
                event="bundle_dedupe_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_bundle_dedupe",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeBundleTriage(LoadPrimitive):
    """Promote and dedupe a target run in one composite step."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_bundle_triage.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        for key in ("target_run_id", "reason_code", "signature_primitive", "verdict", "triage_relpath"):
            if key in body:
                parsed[key] = body[key]
        if "matched_run_id" in body and body["matched_run_id"] != "none":
            parsed["matched_run_id"] = body["matched_run_id"]
        else:
            parsed["matched_run_id"] = None
        if "promoted_runs_scanned" in body:
            try:
                parsed["promoted_runs_scanned"] = int(body["promoted_runs_scanned"])
            except ValueError:
                parsed["promoted_runs_scanned"] = body["promoted_runs_scanned"]
        return parsed

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 120))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        runs_dir = self.params.get("runs_dir")
        target_run_id = self.params.get("target_run_id")
        reason_code = str(self.params.get("reason_code", "divergence"))
        reason_text = str(self.params.get("reason_text", "promote runtime bundle for operator review"))
        operator_notes = str(self.params.get("operator_notes", ""))
        actor = str(self.params.get("actor", os.environ.get("USER", "operator")))
        source_surface = self.params.get("source_surface")
        signature_primitive = self.params.get("signature_primitive")

        handle.log(
            phase="load",
            primitive="runtime_bundle_triage",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "runs_dir": runs_dir,
                "target_run_id": target_run_id,
                "reason_code": reason_code,
                "reason_text": reason_text,
                "operator_notes": operator_notes,
                "actor": actor,
                "source_surface": source_surface,
                "signature_primitive": signature_primitive,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        helper_output_dir = None
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            helper_output_dir = run_dir / "outputs" / "triage"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        command = [
            python_bin,
            helper_script,
            "--reason-code",
            reason_code,
            "--reason-text",
            reason_text,
            "--operator-notes",
            operator_notes,
            "--actor",
            actor,
        ]
        if runs_dir:
            command.extend(["--runs-dir", str(runs_dir)])
        if target_run_id:
            command.extend(["--target-run-id", str(target_run_id)])
        if source_surface:
            command.extend(["--source-surface", str(source_surface)])
        if signature_primitive:
            command.extend(["--signature-primitive", str(signature_primitive)])
        if helper_output_dir is not None:
            command.extend(["--output-dir", str(helper_output_dir)])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "runs_dir": runs_dir,
            "target_run_id": target_run_id,
            "reason_code": reason_code,
            "reason_text": reason_text,
            "operator_notes": operator_notes,
            "actor": actor,
            "source_surface": source_surface,
            "signature_primitive": signature_primitive,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }
        payload.update(self._parse_summary(stdout))

        if run_dir is not None:
            _append_target_hook_event(
                handle,
                primitive="runtime_bundle_triage",
                event="bundle_triage_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_bundle_triage",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeBundleSign(LoadPrimitive):
    """Write a cryptographic signature record into the current run bundle."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_bundle_sign.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        for key in (
            "target_run_id",
            "signing_actor",
            "manifest_sha256",
            "key_source",
            "signature_relpath",
        ):
            if key in body:
                parsed[key] = body[key]
        for key in ("signing_unavailable", "operator_warning"):
            if key in body:
                parsed[key] = body[key] == "true"
        return parsed

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 120))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        target_run_id = self.params.get("target_run_id")
        key_path = self.params.get("key_path")
        signing_actor = str(self.params.get("signing_actor", os.environ.get("USER", "operator")))

        handle.log(
            phase="load",
            primitive="runtime_bundle_sign",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "target_run_id": target_run_id,
                "key_path": key_path,
                "signing_actor": signing_actor,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        helper_output_dir = None
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            helper_output_dir = run_dir / "outputs" / "signature"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        command = [
            python_bin,
            helper_script,
            "--signing-actor",
            signing_actor,
        ]
        if target_run_id:
            command.extend(["--target-run-id", str(target_run_id)])
        if key_path:
            command.extend(["--key-path", str(key_path)])
        if helper_output_dir is not None:
            command.extend(["--output-dir", str(helper_output_dir)])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "target_run_id": target_run_id,
            "key_path": key_path,
            "signing_actor": signing_actor,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }
        payload.update(self._parse_summary(stdout))

        if run_dir is not None:
            _append_target_hook_event(
                handle,
                primitive="runtime_bundle_sign",
                event="bundle_sign_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_bundle_sign",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeBundleChain(LoadPrimitive):
    """Sign, promote, and dedupe the current run in one composite step."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_bundle_chain.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        for key in ("target_run_id", "reason_code", "signature_primitive", "verdict", "chain_relpath"):
            if key in body:
                parsed[key] = body[key]
        if "matched_run_id" in body and body["matched_run_id"] != "none":
            parsed["matched_run_id"] = body["matched_run_id"]
        else:
            parsed["matched_run_id"] = None
        if "promoted_runs_scanned" in body:
            try:
                parsed["promoted_runs_scanned"] = int(body["promoted_runs_scanned"])
            except ValueError:
                parsed["promoted_runs_scanned"] = body["promoted_runs_scanned"]
        return parsed

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 120))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        runs_dir = self.params.get("runs_dir")
        target_run_id = self.params.get("target_run_id")
        reason_code = str(self.params.get("reason_code", "divergence"))
        reason_text = str(self.params.get("reason_text", "promote runtime bundle for operator review"))
        operator_notes = str(self.params.get("operator_notes", ""))
        actor = str(self.params.get("actor", os.environ.get("USER", "operator")))
        signing_actor = str(self.params.get("signing_actor", actor))
        source_surface = self.params.get("source_surface")
        signature_primitive = self.params.get("signature_primitive")
        key_path = self.params.get("key_path")

        handle.log(
            phase="load",
            primitive="runtime_bundle_chain",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "runs_dir": runs_dir,
                "target_run_id": target_run_id,
                "reason_code": reason_code,
                "reason_text": reason_text,
                "operator_notes": operator_notes,
                "actor": actor,
                "signing_actor": signing_actor,
                "source_surface": source_surface,
                "signature_primitive": signature_primitive,
                "key_path": key_path,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        helper_output_dir = None
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            helper_output_dir = run_dir / "outputs" / "chain"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        command = [
            python_bin,
            helper_script,
            "--reason-code",
            reason_code,
            "--reason-text",
            reason_text,
            "--operator-notes",
            operator_notes,
            "--actor",
            actor,
            "--signing-actor",
            signing_actor,
        ]
        if runs_dir:
            command.extend(["--runs-dir", str(runs_dir)])
        if target_run_id:
            command.extend(["--target-run-id", str(target_run_id)])
        if source_surface:
            command.extend(["--source-surface", str(source_surface)])
        if signature_primitive:
            command.extend(["--signature-primitive", str(signature_primitive)])
        if key_path:
            command.extend(["--key-path", str(key_path)])
        if helper_output_dir is not None:
            command.extend(["--output-dir", str(helper_output_dir)])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "runs_dir": runs_dir,
            "target_run_id": target_run_id,
            "reason_code": reason_code,
            "reason_text": reason_text,
            "operator_notes": operator_notes,
            "actor": actor,
            "signing_actor": signing_actor,
            "source_surface": source_surface,
            "signature_primitive": signature_primitive,
            "key_path": key_path,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }
        payload.update(self._parse_summary(stdout))

        if run_dir is not None:
            _append_target_hook_event(
                handle,
                primitive="runtime_bundle_chain",
                event="bundle_chain_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_bundle_chain",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeBundleReplay(LoadPrimitive):
    """Replay a previously captured bundle and compare selected artifacts."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_bundle_replay.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        for key in ("target_run_id", "replay_run_id", "comparison_verdict", "result_relpath"):
            if key in body:
                parsed[key] = body[key]
        return parsed

    def run(self, handle, rng):
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 300))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        runs_dir = self.params.get("runs_dir")
        state_dir = self.params.get("state_dir")
        registry_path = self.params.get("registry_path")
        target_run_id = str(self.params["target_run_id"])
        compare_relpaths = [str(item) for item in self.params["compare_relpaths"]]

        handle.log(
            phase="load",
            primitive="runtime_bundle_replay",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "runs_dir": runs_dir,
                "state_dir": state_dir,
                "registry_path": registry_path,
                "target_run_id": target_run_id,
                "compare_relpaths": compare_relpaths,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        helper_output_dir = None
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            helper_output_dir = run_dir / "outputs" / "replay"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        if helper_script == self._DEFAULT_HELPER and python_bin == "python3" and runs_dir and state_dir and helper_output_dir is not None:
            command = build_runtime_bundle_replay_command(
                runs_dir=Path(runs_dir),
                state_dir=Path(state_dir),
                target_run_id=target_run_id,
                output_dir=helper_output_dir,
                compare_relpaths=compare_relpaths,
                registry_path=Path(registry_path) if registry_path else None,
            )
        else:
            command = [
                python_bin,
                helper_script,
                "--target-run-id",
                target_run_id,
            ]
            if runs_dir:
                command.extend(["--runs-dir", str(runs_dir)])
            if state_dir:
                command.extend(["--state-dir", str(state_dir)])
            if registry_path:
                command.extend(["--registry-path", str(registry_path)])
            if helper_output_dir is not None:
                command.extend(["--output-dir", str(helper_output_dir)])
            for relpath in compare_relpaths:
                command.extend(["--compare-relpath", relpath])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        result = None
        if helper_output_dir is not None:
            result_path = helper_output_dir / "result.json"
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8"))

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "runs_dir": runs_dir,
            "state_dir": state_dir,
            "registry_path": registry_path,
            "target_run_id": target_run_id,
            "compare_relpaths": compare_relpaths,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
            "result": result,
        }
        payload.update(self._parse_summary(stdout))

        if run_dir is not None:
            _append_target_hook_event(
                handle,
                primitive="runtime_bundle_replay",
                event="bundle_replay_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_bundle_replay",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeBundleDiff(LoadPrimitive):
    """Compare selected artifacts across any two captured bundles."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_bundle_diff.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        for key in ("left_run_id", "right_run_id", "comparison_verdict", "diff_relpath"):
            if key in body:
                parsed[key] = body[key]
        return parsed

    def run(self, handle, rng):
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 300))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        runs_dir = self.params.get("runs_dir")
        left_run_id = str(self.params["left_run_id"])
        right_run_id = str(self.params["right_run_id"])
        compare_relpaths = [str(item) for item in self.params["compare_relpaths"]]

        handle.log(
            phase="load",
            primitive="runtime_bundle_diff",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "runs_dir": runs_dir,
                "left_run_id": left_run_id,
                "right_run_id": right_run_id,
                "compare_relpaths": compare_relpaths,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        helper_output_dir = None
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            helper_output_dir = run_dir / "outputs" / "bundle-diff"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        if helper_script == self._DEFAULT_HELPER and python_bin == "python3" and runs_dir and helper_output_dir is not None:
            command = build_runtime_bundle_diff_command(
                runs_dir=Path(runs_dir),
                left_run_id=left_run_id,
                right_run_id=right_run_id,
                output_dir=helper_output_dir,
                compare_relpaths=compare_relpaths,
            )
        else:
            command = [
                python_bin,
                helper_script,
                "--left-run-id",
                left_run_id,
                "--right-run-id",
                right_run_id,
            ]
            if runs_dir:
                command.extend(["--runs-dir", str(runs_dir)])
            if helper_output_dir is not None:
                command.extend(["--output-dir", str(helper_output_dir)])
            for relpath in compare_relpaths:
                command.extend(["--compare-relpath", relpath])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        result = None
        if helper_output_dir is not None:
            diff_path = helper_output_dir / "diff.json"
            if diff_path.is_file():
                result = json.loads(diff_path.read_text(encoding="utf-8"))

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "runs_dir": runs_dir,
            "left_run_id": left_run_id,
            "right_run_id": right_run_id,
            "compare_relpaths": compare_relpaths,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
            "result": result,
        }
        payload.update(self._parse_summary(stdout))

        if run_dir is not None:
            _append_target_hook_event(
                handle,
                primitive="runtime_bundle_diff",
                event="bundle_diff_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_bundle_diff",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeBundleChainVerify(LoadPrimitive):
    """Verify an attestation chain for a captured bundle and emit a report."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_bundle_chain_verify.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        if "target_run_id" in body:
            parsed["target_run_id"] = body["target_run_id"]
        if "chain_verdict" in body:
            parsed["chain_verdict"] = body["chain_verdict"]
        if "report_relpath" in body:
            parsed["report_relpath"] = body["report_relpath"]
        if "chain_length" in body:
            parsed["chain_length"] = int(body["chain_length"])
        return parsed

    def run(self, handle, rng):
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 300))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        runs_dir = self.params.get("runs_dir")
        target_run_id = str(self.params["target_run_id"])

        handle.log(
            phase="load",
            primitive="runtime_bundle_chain_verify",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "runs_dir": runs_dir,
                "target_run_id": target_run_id,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        helper_output_dir = None
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            helper_output_dir = run_dir / "outputs" / "chain-verify"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        if helper_script == self._DEFAULT_HELPER and python_bin == "python3" and runs_dir and helper_output_dir is not None:
            command = build_runtime_bundle_chain_verify_command(
                runs_dir=Path(runs_dir),
                target_run_id=target_run_id,
                output_dir=helper_output_dir,
            )
        else:
            command = [
                python_bin,
                helper_script,
                "--target-run-id",
                target_run_id,
            ]
            if runs_dir:
                command.extend(["--runs-dir", str(runs_dir)])
            if helper_output_dir is not None:
                command.extend(["--output-dir", str(helper_output_dir)])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        result = None
        if helper_output_dir is not None:
            report_path = helper_output_dir / "chain-verify-report.json"
            if report_path.is_file():
                result = json.loads(report_path.read_text(encoding="utf-8"))

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "runs_dir": runs_dir,
            "target_run_id": target_run_id,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
            "artifact_summary": _summarize_bundle_chain_verify_artifacts(helper_output_dir) if helper_output_dir is not None else {},
            "result": result,
        }
        payload.update(self._parse_summary(stdout))
        if result:
            payload["chain_verdict"] = result.get("chain_verdict")
            payload["chain_length"] = result.get("chain_length")

        if run_dir is not None:
            _append_target_hook_event(
                handle,
                primitive="runtime_bundle_chain_verify",
                event="bundle_chain_verify_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_bundle_chain_verify",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeBundleTag(LoadPrimitive):
    """Attach operator-defined slug tags to a captured bundle as additive metadata."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_bundle_tag.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        if "target_run_id" in body:
            parsed["target_run_id"] = body["target_run_id"]
        if "tags_relpath" in body:
            parsed["tags_relpath"] = body["tags_relpath"]
        if "tags_count" in body:
            try:
                parsed["tags_count"] = int(body["tags_count"])
            except ValueError:
                pass
        return parsed

    def run(self, handle, rng):
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 120))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        runs_dir = _resolve_runtime_path(self.params["runs_dir"])
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        target_run_id = str(self.params["target_run_id"])
        signing_actor = str(self.params.get("signing_actor", "dwarf"))
        tags = [str(tag) for tag in self.params.get("tags", [])]

        handle.log(
            phase="load",
            primitive="runtime_bundle_tag",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "runs_dir": str(runs_dir),
                "output_dir": str(output_dir),
                "target_run_id": target_run_id,
                "signing_actor": signing_actor,
                "tags": tags,
            },
        )

        env = _build_dwarf_telemetry_env(handle)
        if helper_script == self._DEFAULT_HELPER and python_bin == "python3":
            command = build_runtime_bundle_tag_command(
                runs_dir=runs_dir,
                target_run_id=target_run_id,
                tags=tags,
                output_dir=output_dir,
                signing_actor=signing_actor,
            )
        else:
            command = [
                python_bin,
                helper_script,
                "--runs-dir",
                str(runs_dir),
                "--target-run-id",
                target_run_id,
                "--output-dir",
                str(output_dir),
                "--signing-actor",
                signing_actor,
            ]
            for tag in tags:
                command.extend(["--tag", tag])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        result = {}
        tags_path = output_dir / "tags.json"
        if tags_path.is_file():
            result = json.loads(tags_path.read_text(encoding="utf-8"))
        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "runs_dir": str(runs_dir),
            "output_dir": str(output_dir),
            "target_run_id": target_run_id,
            "signing_actor": signing_actor,
            "tags": tags,
            "stdout": stdout[:4096],
            "stderr": stderr[:2048],
            "artifact_summary": _summarize_bundle_tag_artifacts(output_dir),
            "result": result,
        }
        payload.update(self._parse_summary(stdout))
        if result:
            payload["tags_added"] = list(result.get("tags_added") or [])
            payload["hash_anchor"] = result.get("hash_anchor")

        handle.log(
            phase="load",
            primitive="runtime_bundle_tag",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeForensicSnapshot(LoadPrimitive):
    """Capture a frozen audit-handoff snapshot tarball for selected bundles."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_forensic_snapshot.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        if "snapshot_relpath" in body:
            parsed["snapshot_relpath"] = body["snapshot_relpath"]
        if "manifest_relpath" in body:
            parsed["manifest_relpath"] = body["manifest_relpath"]
        if "included_bundle_count" in body:
            try:
                parsed["included_bundle_count"] = int(body["included_bundle_count"])
            except ValueError:
                pass
        return parsed

    def run(self, handle, rng):
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 300))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        runs_dir = _resolve_runtime_path(self.params["runs_dir"])
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        run_ids = [str(run_id) for run_id in self.params.get("run_ids", [])]
        tag_filters = [str(tag) for tag in self.params.get("tag_filters", [])]
        output_format = str(self.params.get("output_format", "tar.gz"))

        handle.log(
            phase="load",
            primitive="runtime_forensic_snapshot",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "runs_dir": str(runs_dir),
                "output_dir": str(output_dir),
                "run_ids": run_ids,
                "tag_filters": tag_filters,
                "output_format": output_format,
            },
        )

        env = _build_dwarf_telemetry_env(handle)
        if helper_script == self._DEFAULT_HELPER and python_bin == "python3":
            command = build_runtime_forensic_snapshot_command(
                runs_dir=runs_dir,
                run_ids=run_ids,
                output_dir=output_dir,
                tag_filters=tag_filters,
                output_format=output_format,
            )
        else:
            command = [
                python_bin,
                helper_script,
                "--runs-dir",
                str(runs_dir),
                "--output-dir",
                str(output_dir),
                "--output-format",
                output_format,
            ]
            for run_id in run_ids:
                command.extend(["--run-id", run_id])
            for tag in tag_filters:
                command.extend(["--tag-filter", tag])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        result = None
        manifest_path = output_dir / "snapshot-manifest.json"
        if manifest_path.is_file():
            result = json.loads(manifest_path.read_text(encoding="utf-8"))
        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "runs_dir": str(runs_dir),
            "output_dir": str(output_dir),
            "run_ids": run_ids,
            "tag_filters": tag_filters,
            "output_format": output_format,
            "stdout": stdout[:4096],
            "stderr": stderr[:2048],
            "artifact_summary": _summarize_forensic_snapshot_artifacts(output_dir),
            "result": result,
        }
        payload.update(self._parse_summary(stdout))
        if result:
            payload["included_bundle_count"] = result.get("included_bundle_count")

        handle.log(
            phase="load",
            primitive="runtime_forensic_snapshot",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeBundleSummaryCompose(LoadPrimitive):
    """Compose an executive-readable rollup across captured bundles."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_bundle_summary_compose.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        if "summary_json_relpath" in body:
            parsed["summary_json_relpath"] = body["summary_json_relpath"]
        if "summary_md_relpath" in body:
            parsed["summary_md_relpath"] = body["summary_md_relpath"]
        if "summary_html_relpath" in body:
            parsed["summary_html_relpath"] = body["summary_html_relpath"]
        if "bundle_count" in body:
            try:
                parsed["bundle_count"] = int(body["bundle_count"])
            except ValueError:
                pass
        return parsed

    def run(self, handle, rng):
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 180))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        runs_dir = _resolve_runtime_path(self.params["runs_dir"])
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        bundle_ids = [str(bundle_id) for bundle_id in self.params.get("bundle_ids", [])]

        handle.log(
            phase="load",
            primitive="runtime_bundle_summary_compose",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "runs_dir": str(runs_dir),
                "output_dir": str(output_dir),
                "bundle_ids": bundle_ids,
            },
        )

        env = _build_dwarf_telemetry_env(handle)
        if helper_script == self._DEFAULT_HELPER and python_bin == "python3":
            command = build_runtime_bundle_summary_compose_command(
                runs_dir=runs_dir,
                bundle_ids=bundle_ids,
                output_dir=output_dir,
            )
        else:
            command = [
                python_bin,
                helper_script,
                "--runs-dir",
                str(runs_dir),
                "--output-dir",
                str(output_dir),
            ]
            for bundle_id in bundle_ids:
                command.extend(["--bundle-id", bundle_id])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        result = None
        summary_path = output_dir / "summary.json"
        if summary_path.is_file():
            result = json.loads(summary_path.read_text(encoding="utf-8"))
        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "runs_dir": str(runs_dir),
            "output_dir": str(output_dir),
            "bundle_ids": bundle_ids,
            "stdout": stdout[:4096],
            "stderr": stderr[:2048],
            "artifact_summary": _summarize_bundle_summary_artifacts(output_dir),
            "result": result,
        }
        payload.update(self._parse_summary(stdout))
        if result:
            payload["bundle_count"] = (result.get("summary") or {}).get("total_bundle_count")

        handle.log(
            phase="load",
            primitive="runtime_bundle_summary_compose",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeBundleTimeline(LoadPrimitive):
    """Assemble a chronological evidence timeline across captured bundles."""

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        runs_dir = _resolve_runtime_path(self.params.get("runs_dir", str(DWARF_ROOT / "runs")))
        bundle_ids = [str(bundle_id) for bundle_id in self.params.get("bundle_ids", [])]
        scenario_id_filters = [str(value) for value in self.params.get("scenario_id_filters", [])]
        signature_token_filters = [str(value) for value in self.params.get("signature_token_filters", [])]
        timeout = float(self.params.get("timeout_seconds", 120))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_runtime_bundle_timeline_command(
            runs_dir=runs_dir,
            bundle_ids=bundle_ids,
            output_dir=output_dir,
            scenario_id_filters=scenario_id_filters,
            signature_token_filters=signature_token_filters,
        )

        handle.log(
            phase="load",
            primitive="runtime_bundle_timeline",
            level="info",
            event="started",
            payload={
                "runs_dir": str(runs_dir),
                "bundle_ids": bundle_ids,
                "scenario_id_filters": scenario_id_filters,
                "signature_token_filters": signature_token_filters,
                "output_dir": str(output_dir),
                "timeout_seconds": timeout,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_build_dwarf_telemetry_env(handle),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_bundle_timeline_artifacts(output_dir)
        result = {}
        result_path = output_dir / "timeline.json"
        if result_path.is_file():
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            result = dict(payload.get("summary") or payload)
            result["timeline_relpath"] = "outputs/bundle-timeline/timeline.json" if "outputs" in str(result_path) else str(result_path)
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_bundle_timeline",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "result": result,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class RuntimeMultiNodeObservation(LoadPrimitive):
    """Capture per-node runtime observations and correlate them into a single substrate view."""

    bound_state = None

    def run(self, handle, rng):
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        state = self.bound_state or {}
        runtime_metadata_path = _resolve_output_path(handle, self.params["runtime_metadata_path"])
        if not runtime_metadata_path.exists():
            fallback_metadata = state.get("substrate_bundle_runtime_metadata_path") or state.get("substrate_runtime_metadata_path")
            if fallback_metadata:
                runtime_metadata_path = Path(str(fallback_metadata))
        node_ids = [str(node_id) for node_id in self.params.get("node_ids", [])]
        observation_window_seconds = float(self.params.get("observation_window_seconds", 5.0))
        observation_primitives = [str(value) for value in self.params.get("observation_primitives", [])]
        sample_interval_seconds = float(self.params.get("sample_interval_seconds", 1.0))
        compose_report = state.get("substrate_compose_report") or {}
        network_magic = compose_report.get("network_magic", self.params.get("network_magic"))
        cardano_cli = self.params.get("cardano_cli")
        if not cardano_cli:
            support_binaries = compose_report.get("support_binaries") or {}
            cardano_cli = support_binaries.get("cardano-cli")
        connect_attempts = self.params.get("connect_attempts")
        timeout = float(self.params.get("timeout_seconds", max(120.0, observation_window_seconds + 30.0)))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_runtime_multi_node_observation_command(
            runtime_metadata_path=runtime_metadata_path,
            node_ids=node_ids,
            observation_window_seconds=observation_window_seconds,
            output_dir=output_dir,
            observation_primitives=observation_primitives,
            sample_interval_seconds=sample_interval_seconds,
            network_magic=int(network_magic) if network_magic is not None else None,
            cardano_cli=str(cardano_cli) if cardano_cli else None,
            connect_attempts=int(connect_attempts) if connect_attempts is not None else None,
        )

        handle.log(
            phase="load",
            primitive="runtime_multi_node_observation",
            level="info",
            event="started",
            payload={
                "runtime_metadata_path": str(runtime_metadata_path),
                "node_ids": node_ids,
                "observation_window_seconds": observation_window_seconds,
                "observation_primitives": observation_primitives,
                "sample_interval_seconds": sample_interval_seconds,
                "network_magic": network_magic,
                "output_dir": str(output_dir),
                "timeout_seconds": timeout,
                "command": command,
            },
        )

        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_build_dwarf_telemetry_env(handle),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        artifact_summary = _summarize_multi_node_observation_artifacts(output_dir)
        result = {}
        result_path = output_dir / "observation-summary.json"
        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"

        handle.log(
            phase="load",
            primitive="runtime_multi_node_observation",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": artifact_summary,
                "result": result,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )


class _RuntimeStaticAnalysisBase(LoadPrimitive):
    _TOOL = ""
    _PRIMITIVE = ""
    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_static_analysis.py"

    def run(self, handle, rng):
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 300))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        crate_dir = _resolve_runtime_path(self.params["crate_dir"])
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        handle.log(
            phase="load",
            primitive=self._PRIMITIVE,
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "crate_dir": str(crate_dir),
                "output_dir": str(output_dir),
                "tool": self._TOOL,
            },
        )

        env = _ensure_cargo_path(_build_dwarf_telemetry_env(handle))
        if helper_script == self._DEFAULT_HELPER and python_bin == "python3":
            command = build_runtime_static_analysis_command(
                tool=self._TOOL,
                crate_dir=crate_dir,
                output_dir=output_dir,
            )
        else:
            command = [
                python_bin,
                helper_script,
                "--tool",
                self._TOOL,
                "--crate-dir",
                str(crate_dir),
                "--output-dir",
                str(output_dir),
            ]

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        result = {}
        findings_path = output_dir / "findings.json"
        if findings_path.is_file():
            result = json.loads(findings_path.read_text(encoding="utf-8"))
        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "crate_dir": str(crate_dir),
            "tool": self._TOOL,
            "artifact_summary": _summarize_static_analysis_artifacts(output_dir),
            "result": result,
            "stdout": stdout[:4096],
            "stderr": stderr[:2048],
            "tool_status": result.get("tool_status"),
            "tool_exit_code": result.get("tool_exit_code"),
            "findings_count": result.get("findings_count"),
        }

        _append_target_hook_event(
            handle,
            primitive=self._PRIMITIVE,
            event=f"static_analysis_{self._TOOL}_result",
            payload=payload,
            level="info" if outcome == "ok" else "error",
        )
        handle.log(
            phase="load",
            primitive=self._PRIMITIVE,
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeStaticAnalysisClippy(_RuntimeStaticAnalysisBase):
    _TOOL = "clippy"
    _PRIMITIVE = "runtime_static_analysis_clippy"


class RuntimeStaticAnalysisAudit(_RuntimeStaticAnalysisBase):
    _TOOL = "audit"
    _PRIMITIVE = "runtime_static_analysis_audit"


class RuntimeStaticAnalysisDeny(_RuntimeStaticAnalysisBase):
    _TOOL = "deny"
    _PRIMITIVE = "runtime_static_analysis_deny"


class RuntimeBundleExportSarif(LoadPrimitive):
    """Export a captured bundle into SARIF v2.1.0."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_bundle_export_sarif.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        for key in ("target_run_id", "sarif_relpath"):
            if key in body:
                parsed[key] = body[key]
        if "schema_valid" in body:
            parsed["schema_valid"] = body["schema_valid"].lower() == "true"
        if "sarif_result_count" in body:
            try:
                parsed["sarif_result_count"] = int(body["sarif_result_count"])
            except ValueError:
                pass
        return parsed

    def run(self, handle, rng):
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 120))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        runs_dir = self.params.get("runs_dir")
        target_run_id = str(self.params["target_run_id"])
        schema_path = self.params.get("schema_path")

        handle.log(
            phase="load",
            primitive="runtime_bundle_export_sarif",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "runs_dir": runs_dir,
                "target_run_id": target_run_id,
                "schema_path": schema_path,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        helper_output_dir = None
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            helper_output_dir = run_dir / "outputs" / "sarif-export"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        if helper_script == self._DEFAULT_HELPER and python_bin == "python3" and runs_dir and helper_output_dir is not None:
            command = build_runtime_bundle_export_sarif_command(
                runs_dir=Path(runs_dir),
                target_run_id=target_run_id,
                output_dir=helper_output_dir,
                schema_path=Path(schema_path) if schema_path else None,
            )
        else:
            command = [
                python_bin,
                helper_script,
                "--target-run-id",
                target_run_id,
            ]
            if runs_dir:
                command.extend(["--runs-dir", str(runs_dir)])
            if helper_output_dir is not None:
                command.extend(["--output-dir", str(helper_output_dir)])
            if schema_path:
                command.extend(["--schema-path", str(schema_path)])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        result = None
        if helper_output_dir is not None:
            result_path = helper_output_dir / "result.json"
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8"))

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "runs_dir": runs_dir,
            "target_run_id": target_run_id,
            "schema_path": schema_path,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
            "result": result,
        }
        payload.update(self._parse_summary(stdout))
        if result is not None:
            payload["schema_valid"] = result.get("schema_valid")
            payload["sarif_result_count"] = result.get("sarif_result_count")
            payload["sarif_run_count"] = result.get("sarif_run_count")
            payload["sarif_relpath"] = result.get("sarif_relpath")

        if run_dir is not None:
            _append_target_hook_event(
                handle,
                primitive="runtime_bundle_export_sarif",
                event="bundle_export_sarif_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_bundle_export_sarif",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeBundleExport(LoadPrimitive):
    """Package the current run bundle into a signed export tarball."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_bundle_export.py"

    @staticmethod
    def _parse_summary(stdout: str) -> dict:
        body = {}
        for token in (stdout or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            body[key] = value
        parsed = {}
        for key in ("target_run_id", "signing_actor", "manifest_sha256", "tarball_relpath", "signature_relpath"):
            if key in body:
                parsed[key] = body[key]
        return parsed

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 120))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        target_run_id = self.params.get("target_run_id")
        key_path = self.params.get("key_path")
        signing_actor = str(self.params.get("signing_actor", os.environ.get("USER", "operator")))

        handle.log(
            phase="load",
            primitive="runtime_bundle_export",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "target_run_id": target_run_id,
                "key_path": key_path,
                "signing_actor": signing_actor,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        helper_output_dir = None
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            helper_output_dir = run_dir / "outputs" / "export"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        command = [
            python_bin,
            helper_script,
            "--signing-actor",
            signing_actor,
        ]
        if target_run_id:
            command.extend(["--target-run-id", str(target_run_id)])
        if key_path:
            command.extend(["--key-path", str(key_path)])
        if helper_output_dir is not None:
            command.extend(["--output-dir", str(helper_output_dir)])

        timed_out = False
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "target_run_id": target_run_id,
            "key_path": key_path,
            "signing_actor": signing_actor,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }
        payload.update(self._parse_summary(stdout))

        if run_dir is not None:
            _append_target_hook_event(
                handle,
                primitive="runtime_bundle_export",
                event="bundle_export_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_bundle_export",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimePreviewParityBaseline(LoadPrimitive):
    """Run the existing preview parity baseline helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_preview_parity_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        sample_seconds = int(self.params.get("sample_seconds", 20))
        timeout_seconds = float(self.params.get("timeout_seconds", 60))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        preview_amaru_root = self.params.get("preview_amaru_root")
        preview_cardano_node_root = self.params.get("preview_cardano_node_root")
        mode = "baseline"

        run_dir = getattr(handle, "run_dir", None)
        scenario_path = str(self.params.get("scenario_path", Path(run_dir) / "scenario.yaml" if run_dir is not None else "scenario.yaml"))

        handle.log(
            phase="load",
            primitive="runtime_preview_parity_baseline",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "scenario_path": scenario_path,
                "sample_seconds": sample_seconds,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )
        if preview_amaru_root:
            env["ADA2_DWARF_PREVIEW_AMARU_ROOT"] = str(preview_amaru_root)
        if preview_cardano_node_root:
            env["ADA2_DWARF_PREVIEW_CARDANO_NODE_ROOT"] = str(preview_cardano_node_root)

        timed_out = False
        try:
            proc = subprocess.run(
                [
                    python_bin,
                    helper_script,
                    mode,
                    "--scenario-path",
                    scenario_path,
                    "--sample-seconds",
                    str(sample_seconds),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "scenario_path": scenario_path,
            "sample_seconds": sample_seconds,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-preview-parity-baseline"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_preview_parity_baseline",
                event="preview_parity_baseline_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_preview_parity_baseline",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimePreviewUpstreamDrop(LoadPrimitive):
    """Run the existing preview upstream drop helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_preview_upstream_fault_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        fault_seconds = int(self.params.get("fault_seconds", 15))
        recovery_seconds = int(self.params.get("recovery_seconds", 20))
        timeout_seconds = float(self.params.get("timeout_seconds", max(120, fault_seconds + recovery_seconds + 30)))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        preview_amaru_root = self.params.get("preview_amaru_root")
        preview_cardano_node_root = self.params.get("preview_cardano_node_root")
        mode = "drop"

        run_dir = getattr(handle, "run_dir", None)
        scenario_path = str(self.params.get("scenario_path", Path(run_dir) / "scenario.yaml" if run_dir is not None else "scenario.yaml"))

        handle.log(
            phase="load",
            primitive="runtime_preview_upstream_drop",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "scenario_path": scenario_path,
                "fault_seconds": fault_seconds,
                "recovery_seconds": recovery_seconds,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "preview_amaru_root": preview_amaru_root,
                "preview_cardano_node_root": preview_cardano_node_root,
            },
        )

        env = os.environ.copy()
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )
        if preview_amaru_root:
            env["ADA2_DWARF_PREVIEW_AMARU_ROOT"] = str(preview_amaru_root)
        if preview_cardano_node_root:
            env["ADA2_DWARF_PREVIEW_CARDANO_NODE_ROOT"] = str(preview_cardano_node_root)

        timed_out = False
        try:
            proc = subprocess.run(
                [
                    python_bin,
                    helper_script,
                    mode,
                    "--scenario-path",
                    scenario_path,
                    "--fault-seconds",
                    str(fault_seconds),
                    "--recovery-seconds",
                    str(recovery_seconds),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "scenario_path": scenario_path,
            "fault_seconds": fault_seconds,
            "recovery_seconds": recovery_seconds,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "preview_amaru_root": preview_amaru_root,
            "preview_cardano_node_root": preview_cardano_node_root,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-preview-upstream-drop"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_preview_upstream_drop",
                event="preview_upstream_drop_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_preview_upstream_drop",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimePreviewUpstreamReset(LoadPrimitive):
    """Run the existing preview upstream reset helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_preview_upstream_reset_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        fault_seconds = int(self.params.get("fault_seconds", 15))
        recovery_seconds = int(self.params.get("recovery_seconds", 20))
        timeout_seconds = float(self.params.get("timeout_seconds", max(120, fault_seconds + recovery_seconds + 30)))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        preview_amaru_root = self.params.get("preview_amaru_root")
        preview_cardano_node_root = self.params.get("preview_cardano_node_root")
        mode = "reset"

        run_dir = getattr(handle, "run_dir", None)
        scenario_path = str(self.params.get("scenario_path", Path(run_dir) / "scenario.yaml" if run_dir is not None else "scenario.yaml"))

        handle.log(
            phase="load",
            primitive="runtime_preview_upstream_reset",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "scenario_path": scenario_path,
                "fault_seconds": fault_seconds,
                "recovery_seconds": recovery_seconds,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "preview_amaru_root": preview_amaru_root,
                "preview_cardano_node_root": preview_cardano_node_root,
            },
        )

        env = os.environ.copy()
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )
        if preview_amaru_root:
            env["ADA2_DWARF_PREVIEW_AMARU_ROOT"] = str(preview_amaru_root)
        if preview_cardano_node_root:
            env["ADA2_DWARF_PREVIEW_CARDANO_NODE_ROOT"] = str(preview_cardano_node_root)

        timed_out = False
        try:
            proc = subprocess.run(
                [
                    python_bin,
                    helper_script,
                    mode,
                    "--scenario-path",
                    scenario_path,
                    "--fault-seconds",
                    str(fault_seconds),
                    "--recovery-seconds",
                    str(recovery_seconds),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "scenario_path": scenario_path,
            "fault_seconds": fault_seconds,
            "recovery_seconds": recovery_seconds,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "preview_amaru_root": preview_amaru_root,
            "preview_cardano_node_root": preview_cardano_node_root,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-preview-upstream-reset"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_preview_upstream_reset",
                event="preview_upstream_reset_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_preview_upstream_reset",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimePreviewUpstreamDelay(LoadPrimitive):
    """Run the existing preview upstream delay helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_preview_upstream_delay_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        fault_seconds = int(self.params.get("fault_seconds", 15))
        recovery_seconds = int(self.params.get("recovery_seconds", 20))
        delay_ms = int(self.params.get("delay_ms", 400))
        jitter_ms = int(self.params.get("jitter_ms", 100))
        timeout_seconds = float(self.params.get("timeout_seconds", max(120, fault_seconds + recovery_seconds + 30)))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        preview_amaru_root = self.params.get("preview_amaru_root")
        preview_cardano_node_root = self.params.get("preview_cardano_node_root")
        mode = "delay"

        run_dir = getattr(handle, "run_dir", None)
        scenario_path = str(
            self.params.get("scenario_path", Path(run_dir) / "scenario.yaml" if run_dir is not None else "scenario.yaml")
        )

        handle.log(
            phase="load",
            primitive="runtime_preview_upstream_delay",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "scenario_path": scenario_path,
                "fault_seconds": fault_seconds,
                "recovery_seconds": recovery_seconds,
                "delay_ms": delay_ms,
                "jitter_ms": jitter_ms,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "preview_amaru_root": preview_amaru_root,
                "preview_cardano_node_root": preview_cardano_node_root,
            },
        )

        env = os.environ.copy()
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )
        if preview_amaru_root:
            env["ADA2_DWARF_PREVIEW_AMARU_ROOT"] = str(preview_amaru_root)
        if preview_cardano_node_root:
            env["ADA2_DWARF_PREVIEW_CARDANO_NODE_ROOT"] = str(preview_cardano_node_root)

        timed_out = False
        try:
            proc = subprocess.run(
                [
                    python_bin,
                    helper_script,
                    mode,
                    "--scenario-path",
                    scenario_path,
                    "--fault-seconds",
                    str(fault_seconds),
                    "--recovery-seconds",
                    str(recovery_seconds),
                    "--delay-ms",
                    str(delay_ms),
                    "--jitter-ms",
                    str(jitter_ms),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "scenario_path": scenario_path,
            "fault_seconds": fault_seconds,
            "recovery_seconds": recovery_seconds,
            "delay_ms": delay_ms,
            "jitter_ms": jitter_ms,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "preview_amaru_root": preview_amaru_root,
            "preview_cardano_node_root": preview_cardano_node_root,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-preview-upstream-delay"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_preview_upstream_delay",
                event="preview_upstream_delay_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_preview_upstream_delay",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimePreviewUpstreamLoss(LoadPrimitive):
    """Run the existing preview upstream loss helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_preview_upstream_loss_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        fault_seconds = int(self.params.get("fault_seconds", 15))
        recovery_seconds = int(self.params.get("recovery_seconds", 20))
        loss_pct = int(self.params.get("loss_pct", 30))
        timeout_seconds = float(self.params.get("timeout_seconds", max(120, fault_seconds + recovery_seconds + 30)))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        preview_amaru_root = self.params.get("preview_amaru_root")
        preview_cardano_node_root = self.params.get("preview_cardano_node_root")
        mode = "loss"

        run_dir = getattr(handle, "run_dir", None)
        scenario_path = str(
            self.params.get("scenario_path", Path(run_dir) / "scenario.yaml" if run_dir is not None else "scenario.yaml")
        )

        handle.log(
            phase="load",
            primitive="runtime_preview_upstream_loss",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "scenario_path": scenario_path,
                "fault_seconds": fault_seconds,
                "recovery_seconds": recovery_seconds,
                "loss_pct": loss_pct,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
                "preview_amaru_root": preview_amaru_root,
                "preview_cardano_node_root": preview_cardano_node_root,
            },
        )

        env = os.environ.copy()
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )
        if preview_amaru_root:
            env["ADA2_DWARF_PREVIEW_AMARU_ROOT"] = str(preview_amaru_root)
        if preview_cardano_node_root:
            env["ADA2_DWARF_PREVIEW_CARDANO_NODE_ROOT"] = str(preview_cardano_node_root)

        timed_out = False
        try:
            proc = subprocess.run(
                [
                    python_bin,
                    helper_script,
                    mode,
                    "--scenario-path",
                    scenario_path,
                    "--fault-seconds",
                    str(fault_seconds),
                    "--recovery-seconds",
                    str(recovery_seconds),
                    "--loss-pct",
                    str(loss_pct),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "scenario_path": scenario_path,
            "fault_seconds": fault_seconds,
            "recovery_seconds": recovery_seconds,
            "loss_pct": loss_pct,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "preview_amaru_root": preview_amaru_root,
            "preview_cardano_node_root": preview_cardano_node_root,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-preview-upstream-loss"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_preview_upstream_loss",
                event="preview_upstream_loss_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_preview_upstream_loss",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeLiveImplementationBaseline(LoadPrimitive):
    """Run the existing live implementation baseline helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_live_implementation_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        runtime_root = str(self.params["runtime_root"])
        timeout_seconds = float(self.params.get("timeout_seconds", 60))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "baseline"

        run_dir = getattr(handle, "run_dir", None)
        scenario_path = str(
            self.params.get("scenario_path", Path(run_dir) / "scenario.yaml" if run_dir is not None else "scenario.yaml")
        )

        handle.log(
            phase="load",
            primitive="runtime_live_implementation_baseline",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "runtime_root": runtime_root,
                "scenario_path": scenario_path,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [
                    python_bin,
                    helper_script,
                    mode,
                    "--runtime-root",
                    runtime_root,
                    "--scenario-path",
                    scenario_path,
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "runtime_root": runtime_root,
            "scenario_path": scenario_path,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-live-implementation-baseline"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_live_implementation_baseline",
                event="live_implementation_baseline_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_live_implementation_baseline",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimePartitionRejoin(LoadPrimitive):
    """Run the existing partition/rejoin helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_partition_rejoin_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 240))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))

        handle.log(
            phase="load",
            primitive="runtime_partition_rejoin",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-partition-rejoin"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_partition_rejoin",
                event="partition_rejoin_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_partition_rejoin",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeProfileRestartRecovery(LoadPrimitive):
    """Run the existing profile restart recovery helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_profile_recovery_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 300))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "restart"

        handle.log(
            phase="load",
            primitive="runtime_profile_restart_recovery",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script, mode],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-profile-restart-recovery"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_profile_restart_recovery",
                event="profile_restart_recovery_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_profile_restart_recovery",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeProfileRestartPostrecoveryBlockfetch(LoadPrimitive):
    """Run the existing restart-fetch recovery helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_profile_recovery_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 420))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "restart-fetch"

        handle.log(
            phase="load",
            primitive="runtime_profile_restart_postrecovery_blockfetch",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script, mode],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-profile-restart-postrecovery-blockfetch"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_profile_restart_postrecovery_blockfetch",
                event="profile_restart_postrecovery_blockfetch_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_profile_restart_postrecovery_blockfetch",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeProfileCopiedStateRecovery(LoadPrimitive):
    """Run the existing copied-state recovery helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_profile_recovery_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 420))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "copied-state"

        handle.log(
            phase="load",
            primitive="runtime_profile_copied_state_recovery",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script, mode],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-profile-copied-state-recovery"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_profile_copied_state_recovery",
                event="profile_copied_state_recovery_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_profile_copied_state_recovery",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class RuntimeProfileCopiedStatePostremediationBlockfetch(LoadPrimitive):
    """Run the existing copied-state-fetch helper as a declarative primitive."""

    _DEFAULT_HELPER = "/home/nigel/dwarf-fw/scripts/runtime_profile_recovery_check.py"

    def run(self, handle, rng):
        import os
        import subprocess

        helper_script = str(self.params.get("helper_script", self._DEFAULT_HELPER))
        python_bin = str(self.params.get("python_bin", "python3"))
        timeout_seconds = float(self.params.get("timeout_seconds", 540))
        expected_helper_exit = int(self.params.get("expected_helper_exit", 0))
        mode = "copied-state-fetch"

        handle.log(
            phase="load",
            primitive="runtime_profile_copied_state_postremediation_blockfetch",
            level="info",
            event="started",
            payload={
                "helper_script": helper_script,
                "python_bin": python_bin,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
                "expected_helper_exit": expected_helper_exit,
            },
        )

        env = os.environ.copy()
        run_dir = getattr(handle, "run_dir", None)
        if run_dir is not None:
            run_dir = Path(run_dir)
            events_dir = run_dir / "events"
            metrics_dir = run_dir / "metrics"
            runtime_metrics_dir = metrics_dir / "runtime"
            target_event_log = events_dir / "target-hooks.ndjson"
            env.update(
                {
                    "ADA2_DWARF_RUN_DIR": str(run_dir),
                    "ADA2_DWARF_EVENTS_DIR": str(events_dir),
                    "ADA2_DWARF_METRICS_DIR": str(metrics_dir),
                    "ADA2_DWARF_RUNTIME_METRICS_DIR": str(runtime_metrics_dir),
                    "ADA2_DWARF_TARGET_EVENT_LOG": str(target_event_log),
                }
            )

        timed_out = False
        try:
            proc = subprocess.run(
                [python_bin, helper_script, mode],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=env,
            )
            helper_exit_code = int(proc.returncode)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            helper_exit_code = -1
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")

        outcome = "ok" if helper_exit_code == expected_helper_exit else ("timeout" if timed_out else "unexpected_exit")
        payload = {
            "outcome": outcome,
            "helper_script": helper_script,
            "python_bin": python_bin,
            "mode": mode,
            "timeout_seconds": timeout_seconds,
            "expected_helper_exit": expected_helper_exit,
            "helper_exit_code": helper_exit_code,
            "timed_out": timed_out,
            "stdout": stdout[:4096],
            "stderr": stderr[:1024],
        }

        if run_dir is not None:
            output_dir = Path(run_dir) / "outputs" / "runtime-profile-copied-state-postremediation-blockfetch"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_target_hook_event(
                handle,
                primitive="runtime_profile_copied_state_postremediation_blockfetch",
                event="profile_copied_state_postremediation_blockfetch_result",
                payload=payload,
                level="info" if outcome == "ok" else "error",
            )

        handle.log(
            phase="load",
            primitive="runtime_profile_copied_state_postremediation_blockfetch",
            level="info" if outcome == "ok" else "error",
            event="completed",
            payload=payload,
        )


class CborFuzzTarget(LoadPrimitive):
    """Feed generated bytes to a shim binary, classify each outcome, log per-iteration events.

    Parameters:
      target_id        — manifest id under manifests_dir
      manifests_dir    — directory holding <target_id>.yaml
      iterations       — count of inputs to feed (default 100)
      min_bytes        — minimum generated-input length (default 1)
      max_bytes        — maximum generated-input length (default 256)
      per_input_timeout_seconds — per-iteration timeout (default 5)
    """

    def run(self, handle, rng):
        target_id = self.params["target_id"]
        manifests_dir = Path(self.params["manifests_dir"])
        iterations = int(self.params.get("iterations", 100))
        min_bytes = int(self.params.get("min_bytes", 1))
        max_bytes = int(self.params.get("max_bytes", 256))
        per_input_timeout = float(self.params.get("per_input_timeout_seconds", 5))

        manifest = load_target_manifest(target_id, manifests_dir=manifests_dir)
        binary = str(_resolve_runtime_path(manifest.binary))

        handle.log(
            phase="load", primitive="cbor_fuzz_target", level="info", event="started",
            payload={"target_id": target_id, "iterations": iterations, "binary": binary, "upstream_commit": manifest.upstream_commit},
        )

        for i in range(iterations):
            size = rng.randint(min_bytes, max_bytes)
            data = bytes(rng.getrandbits(8) for _ in range(size))
            outcome, stdout = _run_stdin_target(binary=binary, data=data, timeout_seconds=per_input_timeout)
            handle.log(
                phase="load", primitive="cbor_fuzz_target", level="info", event="iteration",
                payload={"i": i, "outcome": outcome, "size": size, "stdout_head": stdout[:200]},
            )

        handle.log(
            phase="load", primitive="cbor_fuzz_target", level="info", event="completed",
            payload={"iterations": iterations},
        )


class CborReplayTarget(LoadPrimitive):
    """Feed one exact testcase input to a shim binary and log the result.

    Parameters:
      target_id        — manifest id under manifests_dir
      manifests_dir    — directory holding <target_id>.yaml
      input_path       — exact input file to replay
      per_input_timeout_seconds — timeout for the replayed input (default 5)
    """

    def run(self, handle, rng):
        target_id = self.params["target_id"]
        manifests_dir = Path(self.params["manifests_dir"])
        input_path = _resolve_runtime_path(self.params["input_path"])
        per_input_timeout = float(self.params.get("per_input_timeout_seconds", 5))

        manifest = load_target_manifest(target_id, manifests_dir=manifests_dir)
        binary = str(_resolve_runtime_path(manifest.binary))
        data = Path(input_path).read_bytes()

        handle.log(
            phase="load", primitive="cbor_replay_target", level="info", event="started",
            payload={"target_id": target_id, "binary": binary, "input_path": str(input_path), "size": len(data)},
        )
        outcome, stdout = _run_stdin_target(binary=binary, data=data, timeout_seconds=per_input_timeout)
        handle.log(
            phase="load", primitive="cbor_replay_target", level="info", event="iteration",
            payload={"i": 0, "outcome": outcome, "size": len(data), "stdout_head": stdout[:200], "input_path": str(input_path)},
        )
        handle.log(
            phase="load", primitive="cbor_replay_target", level="info", event="completed",
            payload={"iterations": 1, "outcome": outcome},
        )


# ---------------------------------------------------------------------------
# Probes and assertions for the cbor_fuzz_target outcome stream
# ---------------------------------------------------------------------------


class ParserExitStatus(ProbePrimitive):
    """Per-input probe that records each iteration's outcome to probes/parser_exit_status.ndjson."""

    def sample_for_input(self, handle, input_id, outcome):
        handle.probe_sample("parser_exit_status", value=dict(outcome), meta={"input_id": input_id})


class ParseSucceedsOrCleanError(AssertionPrimitive):
    """Pass iff every iteration's outcome is 'ok' or 'clean_error'. Any 'crash' fails the assertion."""

    def evaluate_outcomes(self, outcomes):
        counts = {"ok": 0, "clean_error": 0, "crash": 0}
        crashing = []
        for entry in outcomes:
            kind = entry.get("outcome")
            if kind in counts:
                counts[kind] += 1
            if kind == "crash":
                crashing.append(entry)
        minimum_outcomes = int(self.params.get("min_outcomes_count", 1))
        total_outcomes = counts["ok"] + counts["clean_error"] + counts["crash"]
        return {
            "primitive": "parse_succeeds_or_clean_error",
            "params": dict(self.params),
            "evaluated_value": {**counts, "min_outcomes_count": minimum_outcomes, "total_outcomes": total_outcomes},
            "data_points_used": [{"input_id": e.get("input_id"), "size": e.get("size")} for e in crashing],
            "result": "pass" if counts["crash"] == 0 and total_outcomes >= minimum_outcomes else "fail",
        }


class LoadEventsAreOk(AssertionPrimitive):
    """Pass iff all load-phase completed events report outcome=ok."""

    def evaluate(self, handle):
        completed = _events_from_handle(handle, phase="load", event="completed")
        outcomes = []
        non_ok = []
        for entry in completed:
            payload = entry.get("payload") or {}
            if "outcome" not in payload:
                continue
            outcomes.append(payload)
            if payload.get("outcome") != "ok":
                non_ok.append(payload)
        min_completed = int(self.params.get("min_completed", 1))
        min_event_count = int(self.params.get("min_event_count", 2))
        required_completed = max(min_completed, min_event_count)
        enough = len(outcomes) >= required_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else (
            f"expected at least {required_completed} load completed events with outcomes"
        )
        return {
            "primitive": "load_events_are_ok",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(outcomes),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
                "min_event_count": min_event_count,
                "required_completed": required_completed,
            },
            "data_points_used": [
                {"outcome": payload.get("outcome"), "exit_code": payload.get("exit_code")}
                for payload in non_ok
            ],
            "result": result,
            "note": note,
        }


class AmaruPreviewProofOfLife(AssertionPrimitive):
    """Pass iff the preview proof emitted real progress and a live listener signal."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_amaru_preview_proof",
        )
        proof_events = _target_hook_events_from_handle(
            handle,
            event="preview_proof_of_life",
            primitive="runtime_amaru_preview_check",
        )
        listener_samples = _runtime_metric_samples_from_handle(handle, "amaru_preview_listener_ok")
        min_completed = int(self.params.get("min_completed", 1))
        min_chain_delta_bytes = int(self.params.get("min_chain_delta_bytes", 100000))
        min_log_delta_bytes = int(self.params.get("min_log_delta_bytes", 1))
        min_listener_ok = int(self.params.get("min_listener_ok", 1))

        latest_completed = completed[-1] if completed else None
        latest_proof = proof_events[-1] if proof_events else None
        latest_listener = listener_samples[-1] if listener_samples else None

        payload = (latest_completed or {}).get("payload") or {}
        proof_payload = (latest_proof or {}).get("payload") or {}
        listener_value = int((latest_listener or {}).get("value", 0) or 0)

        enough = (
            len(completed) >= min_completed
            and payload.get("outcome") == "ok"
            and int(payload.get("helper_exit_code", -1)) == int(payload.get("expected_helper_exit", 0))
            and not bool(payload.get("timed_out", False))
            and bool(proof_payload.get("progress_ok", False))
            and int(proof_payload.get("chain_bytes_delta", 0) or 0) >= min_chain_delta_bytes
            and int(proof_payload.get("log_bytes_delta", 0) or 0) >= min_log_delta_bytes
            and listener_value >= min_listener_ok
        )
        result = "pass" if enough else "fail"
        note = (
            None
            if enough
            else "expected preview proof to show real chain/log growth plus a live listener signal"
        )
        return {
            "primitive": "amaru_preview_proof_of_life",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "proof_events": len(proof_events),
                "listener_samples": len(listener_samples),
                "chain_delta_bytes": proof_payload.get("chain_bytes_delta", 0),
                "log_delta_bytes": proof_payload.get("log_bytes_delta", 0),
                "progress_ok": proof_payload.get("progress_ok", False),
                "listener_ok": listener_value,
                "min_chain_delta_bytes": min_chain_delta_bytes,
                "min_log_delta_bytes": min_log_delta_bytes,
                "min_listener_ok": min_listener_ok,
            },
            "data_points_used": [item for item in [payload, proof_payload, latest_listener] if item],
            "result": result,
            "note": note,
        }


class CorpusSynthesizeSeedsDecodeClean(AssertionPrimitive):
    """Pass iff synthesized seeds decode as ok or clean_error under the replay shim."""

    def evaluate(self, handle):
        corpus_dir = _resolve_output_path(handle, self.params["corpus_dir"])
        manifests_dir = self.params.get("manifests_dir", "dwarf/targets/manifests")
        target_id = str(self.params["target_id"])
        timeout_seconds = float(self.params.get("timeout_seconds", 5))
        min_seed_count = int(self.params.get("min_seed_count", 1))

        manifest = load_target_manifest(target_id, manifests_dir=manifests_dir)
        binary = str(_resolve_runtime_path(manifest.binary))
        seeds = sorted(path for path in corpus_dir.glob("*.cbor") if path.is_file())
        counts = {"ok": 0, "clean_error": 0, "crash": 0}
        data_points = []

        for seed_path in seeds:
            outcome, stdout = _run_stdin_target(
                binary=binary,
                data=seed_path.read_bytes(),
                timeout_seconds=timeout_seconds,
            )
            counts[outcome] += 1
            data_points.append(
                {
                    "seed": seed_path.name,
                    "outcome": outcome,
                    "stdout_head": stdout[:200],
                }
            )

        enough = len(seeds) >= min_seed_count
        result = "pass" if enough and counts["crash"] == 0 else "fail"
        note = None if enough else f"expected at least {min_seed_count} synthesized seeds"
        return {
            "primitive": "corpus_synthesize_seeds_decode_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "seed_count": len(seeds),
                "ok": counts["ok"],
                "clean_error": counts["clean_error"],
                "crash": counts["crash"],
                "min_seed_count": min_seed_count,
            },
            "data_points_used": data_points,
            "result": result,
            "note": note,
        }


class BundleReplayMatchesOriginal(AssertionPrimitive):
    """Pass iff the latest bundle replay completed cleanly with comparison_verdict=match."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_bundle_replay",
        )
        latest = completed[-1] if completed else None
        payload = (latest or {}).get("payload") or {}
        enough = latest is not None
        result = (
            "pass"
            if enough and payload.get("outcome") == "ok" and payload.get("comparison_verdict") == "match"
            else "fail"
        )
        note = None if enough else "expected a completed runtime_bundle_replay event"
        return {
            "primitive": "bundle_replay_matches_original",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if enough else 0,
                "outcome": payload.get("outcome"),
                "comparison_verdict": payload.get("comparison_verdict"),
            },
            "data_points_used": [payload] if enough else [],
            "result": result,
            "note": note,
        }


class BundleDiffCompletedClean(AssertionPrimitive):
    """Pass iff bundle diff emitted a non-empty comparison set without structural error."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_bundle_diff",
        )
        latest = completed[-1] if completed else None
        payload = (latest or {}).get("payload") or {}
        result_body = payload.get("result") or {}
        enough = latest is not None and payload.get("outcome") == "ok" and bool(result_body.get("comparisons"))
        result = "pass" if enough else "fail"
        note = None if enough else "expected runtime_bundle_diff to emit a non-empty diff result"
        return {
            "primitive": "bundle_diff_completed_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "outcome": payload.get("outcome"),
                "comparison_verdict": payload.get("comparison_verdict"),
                "comparison_count": len(result_body.get("comparisons") or []),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class BundleSarifExportValid(AssertionPrimitive):
    """Pass iff bundle SARIF export emitted a schema-valid SARIF log."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_bundle_export_sarif",
        )
        latest = completed[-1] if completed else None
        payload = (latest or {}).get("payload") or {}
        enough = latest is not None and payload.get("outcome") == "ok" and payload.get("schema_valid") is True
        result = "pass" if enough else "fail"
        note = None if enough else "expected runtime_bundle_export_sarif to emit a schema-valid SARIF result"
        return {
            "primitive": "bundle_sarif_export_valid",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "outcome": payload.get("outcome"),
                "schema_valid": payload.get("schema_valid"),
                "sarif_result_count": payload.get("sarif_result_count"),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class AflppSmokeExitClean(AssertionPrimitive):
    """Pass iff the AFL++ campaign exits cleanly and emits the expected artifacts."""

    @staticmethod
    def _parse_bitmap_cvg(raw_value) -> float:
        if raw_value in (None, ""):
            return 0.0
        if isinstance(raw_value, (int, float)):
            return float(raw_value)
        value_text = str(raw_value).strip()
        if value_text.endswith("%"):
            value_text = value_text[:-1].strip()
        return float(value_text or 0.0)

    def evaluate(self, handle):
        completed = _events_from_handle(handle, phase="load", event="completed", primitive="runtime_aflpp_campaign")
        min_completed = int(self.params.get("min_completed", 1))
        min_queue_count = int(self.params.get("min_queue_count", 1))
        min_execs_done = int(self.params.get("min_execs_done", 100))
        min_cycles_done = int(self.params.get("min_cycles_done", 1))
        min_bitmap_cvg = float(self.params.get("min_bitmap_cvg", 3.0))
        required_flags = (
            "has_queue_dir",
            "has_crashes_dir",
            "has_hangs_dir",
            "has_fuzzer_stats",
            "has_plot_data",
        )
        non_ok = []
        queue_count = 0
        missing_required_artifacts = 0
        max_execs_done = 0
        max_cycles_done = 0
        max_bitmap_cvg = 0.0
        parse_errors = []
        bundle_path = str(getattr(handle, "run_dir", "unknown-bundle"))

        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            stats = payload.get("stats") or {}
            queue_count = max(queue_count, int(artifact_summary.get("queue_count", 0)))
            execs_done = int(stats.get("execs_done", 0) or 0)
            cycles_done = int(stats.get("cycles_done", 0) or 0)
            raw_bitmap_cvg = stats.get("bitmap_cvg", 0.0)
            try:
                bitmap_cvg = self._parse_bitmap_cvg(raw_bitmap_cvg)
            except (TypeError, ValueError):
                parse_errors.append({
                    "bundle_path": bundle_path,
                    "raw_bitmap_cvg": raw_bitmap_cvg,
                })
                bitmap_cvg = 0.0
            max_execs_done = max(max_execs_done, execs_done)
            max_cycles_done = max(max_cycles_done, cycles_done)
            max_bitmap_cvg = max(max_bitmap_cvg, bitmap_cvg)
            missing = sum(1 for key in required_flags if not artifact_summary.get(key, False))
            if (
                payload.get("outcome") != "ok"
                or int(artifact_summary.get("queue_count", 0)) < min_queue_count
                or execs_done < min_execs_done
                or cycles_done < min_cycles_done
                or bitmap_cvg < min_bitmap_cvg
                or missing > 0
                or bool(parse_errors)
            ):
                missing_required_artifacts += missing
                non_ok.append({
                    "outcome": payload.get("outcome"),
                    "exit_code": payload.get("exit_code"),
                    "queue_count": artifact_summary.get("queue_count", 0),
                    "artifact_summary": artifact_summary,
                    "execs_done": execs_done,
                    "cycles_done": cycles_done,
                    "bitmap_cvg": bitmap_cvg,
                    "raw_bitmap_cvg": raw_bitmap_cvg,
                })

        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        if parse_errors:
            errors = ", ".join(
                f"{item['bundle_path']} raw bitmap_cvg={item['raw_bitmap_cvg']!r}"
                for item in parse_errors
            )
            note = f"malformed AFL++ bitmap_cvg in {errors}"
        else:
            note = None if enough else f"expected at least {min_completed} completed AFL++ campaign events"
        return {
            "primitive": "aflpp_smoke_exit_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
                "queue_count": queue_count,
                "min_queue_count": min_queue_count,
                "execs_done": max_execs_done,
                "min_execs_done": min_execs_done,
                "cycles_done": max_cycles_done,
                "min_cycles_done": min_cycles_done,
                "bitmap_cvg": max_bitmap_cvg,
                "min_bitmap_cvg": min_bitmap_cvg,
                "missing_required_artifacts": missing_required_artifacts,
                "parse_errors": len(parse_errors),
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class DifferentialRuleParity(AssertionPrimitive):
    """Pass iff the differential rule harness exits cleanly without a diff."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_differential_rule_harness",
        )
        min_completed = int(self.params.get("min_completed", 1))
        non_ok = []
        diff_present = 0

        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            has_diff = artifact_summary.get("has_diff_json", False) and not artifact_summary.get("diff_is_empty", True)
            if has_diff:
                diff_present += 1
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_input_json", False)
                or not artifact_summary.get("has_amaru_result_json", False)
                or not artifact_summary.get("has_reference_result_json", False)
                or has_diff
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "artifact_summary": artifact_summary,
                    }
                )

        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed differential rule harness events"
        return {
            "primitive": "differential_rule_parity",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "diff_present": diff_present,
                "min_completed": min_completed,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class CustomMutatorTemplateThroughputClean(AssertionPrimitive):
    """Pass iff the templated custom mutator run clears throughput and corpus-growth gates."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_custom_mutator_template",
        )
        min_completed = int(self.params.get("min_completed", 1))
        baseline = float(self.params["baseline_execs_per_sec"])
        min_ratio = float(self.params["min_ratio"])
        min_novel_queue_count = int(self.params.get("min_novel_queue_count", 1))
        threshold = baseline * min_ratio
        non_ok = []

        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            throughput = float(report.get("average_exec_per_sec", 0.0))
            novel_queue_count = int(report.get("novel_queue_count", 0))
            len_control = report.get("structural_mutator_len_control")
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_template_report", False)
                or throughput < threshold
                or novel_queue_count < min_novel_queue_count
                or len_control != 100
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "artifact_summary": artifact_summary,
                        "report": report,
                        "threshold": threshold,
                    }
                )

        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_custom_mutator_template events"
        return {
            "primitive": "custom_mutator_template_throughput_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
                "throughput_threshold": threshold,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class FuzzCampaignCompletedClean(AssertionPrimitive):
    """Pass iff the fuzz campaign emitted its report and every subcampaign completed cleanly."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_fuzz_campaign",
        )
        min_completed = int(self.params.get("min_completed", 1))
        non_ok = []

        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_campaign_report", False)
                or not artifact_summary.get("has_aggregated_stats", False)
                or not artifact_summary.get("has_combined_corpus", False)
                or int(report.get("failed_subcampaigns", 0)) != 0
                or int(report.get("total_crash_count", 0)) != 0
                or int(report.get("total_hang_count", 0)) != 0
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "artifact_summary": artifact_summary,
                        "report": report,
                    }
                )

        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_fuzz_campaign events"
        return {
            "primitive": "fuzz_campaign_completed_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class LongCampaignCompletedClean(AssertionPrimitive):
    """Pass iff the long campaign emitted at least one checkpoint and a matching final report."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_long_campaign",
        )
        min_completed = int(self.params.get("min_completed", 1))
        non_ok = []

        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            checkpoint_count = int(artifact_summary.get("checkpoint_count", 0))
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_campaign_report", False)
                or checkpoint_count < 1
                or int(artifact_summary.get("checkpoint_stats_count", 0)) != checkpoint_count
                or int(artifact_summary.get("checkpoint_coverage_count", 0)) != checkpoint_count
                or int(artifact_summary.get("checkpoint_queue_archive_count", 0)) != checkpoint_count
                or int(report.get("checkpoint_count", 0)) != checkpoint_count
                or int(report.get("completed_checkpoints", 0)) < 1
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "artifact_summary": artifact_summary,
                        "report": report,
                    }
                )

        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_long_campaign events"
        return {
            "primitive": "long_campaign_completed_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class PersistentCampaignRecordedClean(AssertionPrimitive):
    """Pass iff a persistent campaign emitted a report and SARIF artifact."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_persistent_campaign",
        )
        min_completed = int(self.params.get("min_completed", 1))
        non_ok = []

        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_campaign_report", False)
                or not artifact_summary.get("has_regressions_sarif", False)
                or int(report.get("run_index", 0)) < 1
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "artifact_summary": artifact_summary,
                        "report": report,
                    }
                )

        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_persistent_campaign events"
        return {
            "primitive": "persistent_campaign_recorded_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class FuzzEnvSetupSatisfied(AssertionPrimitive):
    """Pass iff the provisioning report exists and every required component is satisfied."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_fuzz_env_setup",
        )
        min_completed = int(self.params.get("min_completed", 1))
        non_ok = []

        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            components = report.get("components") or {}
            unsatisfied = sorted(
                name for name, component in components.items() if not bool((component or {}).get("satisfied", False))
            )
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_provisioning_report", False)
                or not artifact_summary.get("has_install_log", False)
                or not bool(report.get("satisfied", False))
                or unsatisfied
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "artifact_summary": artifact_summary,
                        "report_satisfied": report.get("satisfied"),
                        "unsatisfied_components": unsatisfied,
                    }
                )

        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_fuzz_env_setup events"
        return {
            "primitive": "fuzz_env_setup_satisfied",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class MiriCampaignRecordedClean(AssertionPrimitive):
    """Pass iff the MIRI report exists and recorded at least one executed test."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_miri_campaign",
        )
        min_completed = int(self.params.get("min_completed", 1))
        min_tests_run = int(self.params.get("min_tests_run", 1))
        non_ok = []

        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_result_json", False)
                or int(report.get("tests_run", 0)) < min_tests_run
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "artifact_summary": artifact_summary,
                        "report": report,
                    }
                )

        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_miri_campaign events"
        return {
            "primitive": "miri_campaign_recorded_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
                "min_tests_run": min_tests_run,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class ProptestCampaignRecordedClean(AssertionPrimitive):
    """Pass iff the proptest report exists and recorded at least one property execution."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_proptest_campaign",
        )
        min_completed = int(self.params.get("min_completed", 1))
        min_properties_run = int(self.params.get("min_properties_run", 1))
        non_ok = []

        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_result_json", False)
                or int(report.get("properties_run", 0)) < min_properties_run
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "artifact_summary": artifact_summary,
                        "report": report,
                    }
                )

        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_proptest_campaign events"
        return {
            "primitive": "proptest_campaign_recorded_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
                "min_properties_run": min_properties_run,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class CredentialCeremonyRecordedClean(AssertionPrimitive):
    """Pass iff the credential ceremony completed cleanly and generated the requested keysets."""

    def evaluate(self, handle):
        completed = _events_from_handle(handle, phase="load", event="completed", primitive="runtime_credential_ceremony")
        min_completed = int(self.params.get("min_completed", 1))
        min_keys_generated = int(self.params.get("min_keys_generated", 1))
        non_ok = 0
        keys_generated = []
        has_result_json = []
        for event in completed:
            payload = event.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            generated = int(report.get("keys_generated", 0) or 0)
            keys_generated.append(generated)
            has_result_json.append(bool(artifact_summary.get("has_result_json", False)))
            if payload.get("outcome") != "ok" or not artifact_summary.get("has_result_json", False) or generated < min_keys_generated:
                non_ok += 1
        enough = len(completed) >= min_completed and non_ok == 0
        result = "pass" if enough else "fail"
        note = None if enough else (
            f"expected at least {min_completed} completed runtime_credential_ceremony events "
            f"with >= {min_keys_generated} generated keysets"
        )
        return {
            "primitive": "credential_ceremony_recorded_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": non_ok,
                "keys_generated": keys_generated,
                "has_result_json": has_result_json,
            },
            "data_points_used": [event.get("payload") or {} for event in completed],
            "result": result,
            "note": note,
        }


class AmaruProptestOracleRecordedClean(AssertionPrimitive):
    """Pass iff the oracle ran selected fixtures and recorded the corpus directory state."""

    def evaluate(self, handle):
        completed = _events_from_handle(handle, phase="load", event="completed", primitive="runtime_amaru_proptest_oracle")
        min_completed = int(self.params.get("min_completed", 1))
        min_fixtures_run = int(self.params.get("min_fixtures_run", 1))
        min_corpus_inputs = int(self.params.get("min_corpus_inputs", 0))
        non_ok = 0
        fixture_counts = []
        corpus_counts = []
        for event in completed:
            payload = event.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            fixtures_run = report.get("fixtures_run") or []
            corpus_inputs_captured = int(report.get("corpus_inputs_captured", 0) or 0)
            fixture_counts.append(len(fixtures_run))
            corpus_counts.append(corpus_inputs_captured)
            if (
                payload.get("outcome") != "ok"
                or not artifact_summary.get("has_result_json", False)
                or len(fixtures_run) < min_fixtures_run
                or corpus_inputs_captured < min_corpus_inputs
            ):
                non_ok += 1
        enough = len(completed) >= min_completed and non_ok == 0
        result = "pass" if enough else "fail"
        note = None if enough else (
            f"expected at least {min_completed} completed runtime_amaru_proptest_oracle events "
            f"with >= {min_fixtures_run} fixtures and >= {min_corpus_inputs} captured corpus inputs"
        )
        return {
            "primitive": "amaru_proptest_oracle_recorded_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": non_ok,
                "fixture_counts": fixture_counts,
                "corpus_counts": corpus_counts,
            },
            "data_points_used": [event.get("payload") or {} for event in completed],
            "result": result,
            "note": note,
        }


class ExecutionTraceAmaruCardanoNodeEquivalent(AssertionPrimitive):
    """Pass iff the differential decoder trace agreed on every processed corpus input."""

    def evaluate(self, handle):
        completed = _events_from_handle(handle, phase="load", event="completed", primitive="runtime_execution_trace_differential")
        min_completed = int(self.params.get("min_completed", 1))
        min_inputs_processed = int(self.params.get("min_inputs_processed", 1))
        non_ok = []
        for event in completed:
            payload = event.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            if (
                payload.get("outcome") != "ok"
                or not artifact_summary.get("has_result_json", False)
                or not bool(report.get("equivalent"))
                or int(report.get("inputs_processed", 0) or 0) < min_inputs_processed
                or int(report.get("diverged_count", 0) or 0) != 0
                or int(report.get("one_side_crashed_count", 0) or 0) != 0
            ):
                non_ok.append(payload)
        enough = len(completed) >= min_completed and not non_ok
        result = "pass" if enough else "fail"
        note = None if enough else (
            f"expected at least {min_completed} completed runtime_execution_trace_differential events "
            f"with >= {min_inputs_processed} equivalent inputs"
        )
        return {
            "primitive": "execution_trace_amaru_cardano_node_equivalent",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_inputs_processed": min_inputs_processed,
            },
            "data_points_used": [event.get("payload") or {} for event in completed],
            "result": result,
            "note": note,
        }


class AflNetCampaignRecordedClean(AssertionPrimitive):
    """Pass iff the AFLNet report exists and recorded at least one visited state."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_aflnet_campaign",
        )
        min_completed = int(self.params.get("min_completed", 1))
        min_states_visited = int(self.params.get("min_states_visited", 1))
        min_execs_done = int(self.params.get("min_execs_done", 2))
        min_sessions = int(self.params.get("min_sessions", 2))
        min_plot_data_rows = int(self.params.get("min_plot_data_rows", 1))
        non_ok = []

        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_result_json", False)
                or not artifact_summary.get("has_state_report", False)
                or not artifact_summary.get("has_fuzzer_stats", False)
                or int(report.get("states_visited", 0)) < min_states_visited
                or int(report.get("execs_done", 0)) < min_execs_done
                or int(report.get("sessions", 0)) < min_sessions
                or int(report.get("plot_data_rows", 0)) < min_plot_data_rows
                or not bool(report.get("telemetry_validation_passed", False))
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "artifact_summary": artifact_summary,
                        "report": report,
                    }
                )

        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_aflnet_campaign events"
        return {
            "primitive": "aflnet_campaign_recorded_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
                "min_states_visited": min_states_visited,
                "min_execs_done": min_execs_done,
                "min_sessions": min_sessions,
                "min_plot_data_rows": min_plot_data_rows,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class SymbolicExecutionCampaignRecordedClean(AssertionPrimitive):
    """Pass iff the symbolic execution report exists and records at least one explored path."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_symbolic_execution_campaign",
        )
        min_completed = int(self.params.get("min_completed", 1))
        min_paths_explored = int(self.params.get("min_paths_explored", 1))
        non_ok = []

        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_result_json", False)
                or int(report.get("paths_explored", 0)) < min_paths_explored
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "artifact_summary": artifact_summary,
                        "report": report,
                    }
                )

        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_symbolic_execution_campaign events"
        return {
            "primitive": "symbolic_execution_campaign_recorded_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
                "min_paths_explored": min_paths_explored,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class CargoMutantsCampaignRecordedClean(AssertionPrimitive):
    """Pass iff a cargo-mutants campaign emitted a report with at least one candidate mutant."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_cargo_mutants_campaign",
        )
        min_completed = int(self.params.get("min_completed", 1))
        min_candidates = int(self.params.get("min_candidates", 1))
        non_ok = []

        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_campaign_report", False)
                or int(report.get("candidate_count", 0)) < min_candidates
                or int(report.get("tested_count", 0)) < 1
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "artifact_summary": artifact_summary,
                        "report": report,
                    }
                )

        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_cargo_mutants_campaign events"
        return {
            "primitive": "cargo_mutants_campaign_recorded_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
                "min_candidates": min_candidates,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class AllNodesStartedClean(AssertionPrimitive):
    """Pass iff runtime_compose_substrate emitted a healthy multi-node report."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="setup",
            event="completed",
            primitive="runtime_compose_substrate",
        )
        min_completed = int(self.params.get("min_completed", 1))
        min_node_count = int(self.params.get("min_node_count", 1))
        non_ok = []
        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            report_nodes = report.get("nodes") or []
            observed_node_count = max(
                int(artifact_summary.get("node_count", 0) or 0),
                int(report.get("node_count", 0) or 0),
                len(report_nodes),
            )
            unhealthy = sorted(node.get("id") for node in (report.get("nodes") or []) if not bool(node.get("healthy", False)))
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_compose_report", False)
                or not bool(report.get("healthy", False))
                or observed_node_count < min_node_count
                or unhealthy
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "unhealthy_nodes": unhealthy,
                        "node_count": observed_node_count,
                    }
                )
        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_compose_substrate events"
        return {
            "primitive": "all_nodes_started_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
                "min_node_count": min_node_count,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class ByzantinePeerRecordedClean(AssertionPrimitive):
    """Pass iff runtime_byzantine_peer emitted a clean interception report."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="fault",
            event="removed",
            primitive="runtime_byzantine_peer",
        )
        min_completed = int(self.params.get("min_completed", 1))
        non_ok = []
        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            if (
                payload.get("exit_code") not in {None, 0}
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_remove_report", False)
                or int(report.get("intercepted_segments", 0)) < 1
                or not bool(report.get("healthy", False))
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "intercepted_segments": report.get("intercepted_segments"),
                        "mutated_segments": report.get("mutated_segments"),
                        "healthy": report.get("healthy"),
                    }
                )
        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_byzantine_peer removals"
        return {
            "primitive": "byzantine_peer_recorded_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class ByzantineCardanoNodeRecordedClean(AssertionPrimitive):
    """Pass iff runtime_byzantine_cardano_node emitted a clean interception report."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="fault",
            event="removed",
            primitive="runtime_byzantine_cardano_node",
        )
        min_completed = int(self.params.get("min_completed", 1))
        non_ok = []
        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            if (
                payload.get("exit_code") not in {None, 0}
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_remove_report", False)
                or int(report.get("intercepted_segments", 0)) < 1
                or int(report.get("mutated_segments", 0)) < 1
                or not bool(report.get("healthy", False))
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "intercepted_segments": report.get("intercepted_segments"),
                        "mutated_segments": report.get("mutated_segments"),
                        "healthy": report.get("healthy"),
                        "behavior": report.get("behavior"),
                    }
                )
        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_byzantine_cardano_node removals"
        return {
            "primitive": "byzantine_cardano_node_recorded_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class QuorumHoldsDespiteByzantine(AssertionPrimitive):
    """Pass iff a real byzantine event occurred and the observation still shows quorum on one tip."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="fault",
            event="removed",
            primitive="runtime_byzantine_cardano_node",
        )
        min_completed = int(self.params.get("min_completed", 1))
        honest_node_ids = [str(node_id) for node_id in self.params.get("honest_node_ids", [])]
        byzantine_node_ids = [str(node_id) for node_id in self.params.get("byzantine_node_ids", [])]
        minimum_fraction = float(self.params.get("minimum_quorum_fraction", 0.6))
        minimum_honest_consensus_count = int(self.params.get("minimum_honest_consensus_count", 2))
        latest, payload, result_body, summary = _latest_multi_node_observation(handle)
        latest_tips = _multi_node_latest_tips(result_body, summary)
        node_count = int(summary.get("node_count", 0) or 0)
        tip_group_count = int(summary.get("tip_group_count", 0) or 0)
        quorum_fraction = float(summary.get("quorum_fraction", 0.0) or 0.0)
        groups = _honest_tip_groups(latest_tips=latest_tips, honest_node_ids=honest_node_ids)
        honest_quorum_count = max(groups.values()) if groups else 0
        matching_honest_nodes = []
        if groups:
            winning = max(groups, key=groups.get)
            target_hash, target_slot = winning
            matching_honest_nodes = sorted(
                node_id
                for node_id in honest_node_ids
                if (latest_tips.get(node_id) or {}).get("hash") == target_hash
                and int((latest_tips.get(node_id) or {}).get("slot", 0) or 0) == target_slot
            )
        real_tip_count = sum(1 for tip in latest_tips.values() if _tip_has_real_chain_progress(tip))
        missing_honest_real_tips = [
            node_id for node_id in honest_node_ids if not _tip_has_real_chain_progress(latest_tips.get(node_id) or {})
        ]

        non_ok_faults = []
        for entry in completed:
            fault_payload = entry.get("payload") or {}
            artifact_summary = fault_payload.get("artifact_summary") or {}
            report = fault_payload.get("report") or {}
            if (
                fault_payload.get("exit_code") not in {None, 0}
                or fault_payload.get("outcome") != "ok"
                or not artifact_summary.get("has_remove_report", False)
                or int(report.get("intercepted_segments", 0)) < 1
                or int(report.get("mutated_segments", 0)) < 1
                or not bool(report.get("healthy", False))
            ):
                non_ok_faults.append(
                    {
                        "exit_code": fault_payload.get("exit_code"),
                        "outcome": fault_payload.get("outcome"),
                        "intercepted_segments": report.get("intercepted_segments"),
                        "mutated_segments": report.get("mutated_segments"),
                        "healthy": report.get("healthy"),
                    }
                )

        enough = (
            len(completed) >= min_completed
            and not non_ok_faults
            and latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and bool(honest_node_ids)
            and bool(byzantine_node_ids)
            and node_count >= (len(honest_node_ids) + len(byzantine_node_ids))
            and len(latest_tips) == node_count
            and real_tip_count == node_count
            and not missing_honest_real_tips
            and tip_group_count == 1
            and quorum_fraction >= minimum_fraction
            and honest_quorum_count >= minimum_honest_consensus_count
        )
        result = "pass" if enough else "fail"
        note = (
            None
            if enough
            else "expected a real byzantine event plus strict quorum-holding tip convergence during observation"
        )
        return {
            "primitive": "quorum_holds_despite_byzantine",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok_faults": len(non_ok_faults),
                "node_count": node_count,
                "tip_group_count": tip_group_count,
                "quorum_fraction": quorum_fraction,
                "minimum_quorum_fraction": minimum_fraction,
                "honest_quorum_count": honest_quorum_count,
                "minimum_honest_consensus_count": minimum_honest_consensus_count,
                "matching_honest_nodes": matching_honest_nodes,
                "missing_honest_real_tips": missing_honest_real_tips,
                "real_tip_count": real_tip_count,
            },
            "data_points_used": non_ok_faults + ([payload] if latest is not None else []),
            "result": result,
            "note": note,
        }


class ThroughputRegressionFloorClean(AssertionPrimitive):
    """Pass iff the measured throughput clears the configured regression floor."""

    def evaluate(self, handle):
        artifact_path = _resolve_output_path(handle, self.params["bundle_artifact_path"])
        artifact_format = str(self.params.get("artifact_format", "fuzzer_stats"))
        metric_key = str(self.params.get("metric_key", "execs_per_sec"))
        baseline = float(self.params["baseline_execs_per_sec"])
        tolerance_pct = float(self.params["tolerance_pct"])
        output_dir = _resolve_output_path(handle, self.params.get("output_dir", "outputs/throughput-floor"))
        output_dir.mkdir(parents=True, exist_ok=True)
        result_path = output_dir / "result.json"

        threshold = baseline * (1.0 - (tolerance_pct / 100.0))
        measured = None
        error = None
        if artifact_path.is_file():
            try:
                measured = _extract_throughput_metric_from_artifact(
                    artifact_path=artifact_path,
                    artifact_format=artifact_format,
                    metric_key=metric_key,
                )
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                error = str(exc)
        else:
            error = f"artifact missing: {artifact_path}"

        verdict = "pass" if measured is not None and measured >= threshold else "fail"
        report = {
            "bundle_artifact_path": str(artifact_path),
            "artifact_format": artifact_format,
            "metric_key": metric_key,
            "measured_execs_per_sec": measured,
            "baseline_execs_per_sec": baseline,
            "tolerance_pct": tolerance_pct,
            "threshold_execs_per_sec": threshold,
            "verdict": verdict,
        }
        if error is not None:
            report["error"] = error
        result_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        non_ok = []
        if verdict != "pass":
            non_ok.append(report)
        return {
            "primitive": "throughput_regression_floor_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "measured_execs_per_sec": measured,
                "baseline_execs_per_sec": baseline,
                "tolerance_pct": tolerance_pct,
                "threshold_execs_per_sec": threshold,
                "result_path": str(result_path),
                "non_ok": len(non_ok),
            },
            "data_points_used": non_ok,
            "result": verdict,
            "note": error,
        }


class AflStabilityRecordedClean(AssertionPrimitive):
    """Pass iff the AFL++ stability report exists and records at least two successful reruns."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_afl_stability",
        )
        min_completed = int(self.params.get("min_completed", 1))
        min_runs = int(self.params.get("min_runs", 2))
        non_ok = []

        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_stability_report", False)
                or int(report.get("rerun_count", 0)) < min_runs
                or int(report.get("successful_reruns", 0)) < min_runs
                or len(report.get("pairwise") or []) < 1
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "artifact_summary": artifact_summary,
                        "report": report,
                    }
                )

        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_afl_stability events"
        return {
            "primitive": "afl_stability_recorded_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
                "min_runs": min_runs,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class CoverageAggregateCompletedClean(AssertionPrimitive):
    """Pass iff the aggregate coverage report exists and includes at least one entry."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_aggregate_coverage",
        )
        min_completed = int(self.params.get("min_completed", 1))
        non_ok = []

        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_coverage_report", False)
                or int(report.get("entry_count", 0)) < 1
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "artifact_summary": artifact_summary,
                        "report": report,
                    }
                )

        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_aggregate_coverage events"
        return {
            "primitive": "coverage_aggregate_completed_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class CrashTriageEmittedClean(AssertionPrimitive):
    """Pass iff crash triage emitted a report and grouped any present crashes."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_crash_triage",
        )
        min_completed = int(self.params.get("min_completed", 1))
        non_ok = []

        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            crash_count = int(report.get("crash_count", report.get("crashes_total", 0)))
            group_count = int(report.get("group_count", report.get("unique_signatures", 0)))
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_triage_report", False)
                or (crash_count > 0 and group_count < 1)
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "artifact_summary": artifact_summary,
                        "report": report,
                    }
                )

        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_crash_triage events"
        return {
            "primitive": "crash_triage_emitted_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class CrashTriageStablePopulation(AssertionPrimitive):
    """Pass iff unique crash signatures remain at or below a configured threshold."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_crash_triage",
        )
        max_unique_signatures = int(self.params.get("max_unique_signatures", 1))
        min_completed = int(self.params.get("min_completed", 1))
        observed = []
        for entry in completed:
            payload = entry.get("payload") or {}
            report = payload.get("report") or {}
            observed.append(
                {
                    "unique_signatures": int(report.get("unique_signatures", report.get("group_count", 0))),
                    "crashes_total": int(report.get("crashes_total", report.get("crash_count", 0))),
                    "outcome": payload.get("outcome"),
                    "exit_code": payload.get("exit_code"),
                }
            )
        enough = len(completed) >= min_completed
        all_within = enough and all(
            item["outcome"] == "ok"
            and item["exit_code"] == 0
            and item["unique_signatures"] <= max_unique_signatures
            for item in observed
        )
        result = "pass" if all_within else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_crash_triage events"
        return {
            "primitive": "crash_triage_stable_population",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "max_unique_signatures": max_unique_signatures,
                "min_completed": min_completed,
            },
            "data_points_used": observed,
            "result": result,
            "note": note,
        }


class AflCorpusMinCompletedClean(AssertionPrimitive):
    """Pass iff corpus minimization emitted stats and retained at least one testcase."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_afl_corpus_min",
        )
        min_completed = int(self.params.get("min_completed", 1))
        non_ok = []

        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            report = payload.get("report") or {}
            cmin = report.get("cmin") or {}
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_cmin_dir", False)
                or not artifact_summary.get("has_tmin_dir", False)
                or not artifact_summary.get("has_cmin_stats", False)
                or not artifact_summary.get("has_tmin_stats", False)
                or int(cmin.get("output_count", 0)) < 1
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "artifact_summary": artifact_summary,
                        "report": report,
                    }
                )

        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_afl_corpus_min events"
        return {
            "primitive": "afl_corpus_min_completed_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class CoverageReportEmittedClean(AssertionPrimitive):
    """Pass iff the coverage report emitted html, markdown, and summary for at least one target."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_coverage_report",
        )
        min_completed = int(self.params.get("min_completed", 1))
        non_ok = []

        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            result = payload.get("result") or {}
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_coverage_html", False)
                or not artifact_summary.get("has_coverage_markdown", False)
                or not artifact_summary.get("has_coverage_summary", False)
                or int(result.get("target_count", 0)) < 1
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "artifact_summary": artifact_summary,
                        "result": result,
                    }
                )

        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_coverage_report events"
        return {
            "primitive": "coverage_report_emitted_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class CorpusHealthReportEmittedClean(AssertionPrimitive):
    """Pass iff a corpus health report was emitted with at least one timeseries point."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_corpus_health_report",
        )
        min_completed = int(self.params.get("min_completed", 1))
        non_ok = []
        for entry in completed:
            payload = entry.get("payload") or {}
            artifact_summary = payload.get("artifact_summary") or {}
            result = payload.get("result") or {}
            if (
                payload.get("exit_code") != 0
                or payload.get("outcome") != "ok"
                or not artifact_summary.get("has_corpus_health_report", False)
                or int(result.get("run_count", 0)) < 1
            ):
                non_ok.append(
                    {
                        "exit_code": payload.get("exit_code"),
                        "outcome": payload.get("outcome"),
                        "artifact_summary": artifact_summary,
                        "result": result,
                    }
                )
        enough = len(completed) >= min_completed
        result = "pass" if enough and not non_ok else "fail"
        note = None if enough else f"expected at least {min_completed} completed runtime_corpus_health_report events"
        return {
            "primitive": "corpus_health_report_emitted_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": len(completed),
                "non_ok": len(non_ok),
                "min_completed": min_completed,
            },
            "data_points_used": non_ok,
            "result": result,
            "note": note,
        }


class CoverageReportFileLevelAflppCompletedClean(AssertionPrimitive):
    """Pass iff file-level AFL++ coverage report emitted and processed at least one bundle."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_coverage_report",
        )
        latest = completed[-1] if completed else None
        payload = (latest or {}).get("payload") or {}
        artifact_summary = payload.get("artifact_summary") or {}
        result = payload.get("result") or {}
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and payload.get("merge_mode") == "file-level"
            and artifact_summary.get("has_coverage_file_level_report", False)
            and int(result.get("processed_bundle_count", 0)) >= 1
            and (
                int(result.get("inputs_processed", 0)) >= 1
                or int(result.get("processed_libfuzzer_bundle_count", 0)) >= 1
            )
        )
        verdict = "pass" if enough else "fail"
        note = None if enough else "expected runtime_coverage_report file-level mode to process at least one AFL++ bundle"
        return {
            "primitive": "coverage_report_file_level_aflpp_completed_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "outcome": payload.get("outcome"),
                "merge_mode": payload.get("merge_mode"),
                "processed_bundle_count": result.get("processed_bundle_count"),
                "inputs_processed": result.get("inputs_processed"),
                "processed_libfuzzer_bundle_count": result.get("processed_libfuzzer_bundle_count"),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": verdict,
            "note": note,
        }


def _latest_multi_node_observation(handle):
    completed = _events_from_handle(
        handle,
        phase="load",
        event="completed",
        primitive="runtime_multi_node_observation",
    )
    latest = completed[-1] if completed else None
    payload = (latest or {}).get("payload") or {}
    result_body = payload.get("result") or {}
    summary = result_body.get("summary") or {}
    return latest, payload, result_body, summary


def _multi_node_connectivity_map(result_body: dict, summary: dict) -> dict[str, list[str]]:
    explicit = summary.get("per_node_connectivity")
    if isinstance(explicit, dict):
        return {str(node_id): sorted(str(peer) for peer in peers or []) for node_id, peers in explicit.items()}
    per_node = result_body.get("per_node") or {}
    connectivity = {}
    for node_id, body in per_node.items():
        peers = (((body or {}).get("connection_state") or {}).get("peer_nodes_connected")) or []
        connectivity[str(node_id)] = sorted(str(peer) for peer in peers)
    return connectivity


def _multi_node_latest_tips(result_body: dict, summary: dict) -> dict[str, dict]:
    latest = summary.get("latest_tips")
    if isinstance(latest, dict):
        return latest
    per_node = result_body.get("per_node") or {}
    tips = {}
    for node_id, body in per_node.items():
        latest_tip = (((body or {}).get("tip_state") or {}).get("latest_tip")) or {}
        if latest_tip:
            tips[str(node_id)] = latest_tip
    return tips


def _tip_has_real_chain_progress(tip: dict) -> bool:
    hash_value = str((tip or {}).get("hash") or "").strip()
    slot_value = (tip or {}).get("slot")
    try:
        slot_int = int(slot_value)
    except (TypeError, ValueError):
        return False
    return slot_int > 0 and bool(hash_value)


def _tip_state_slot_transition_count(result_body: dict) -> int:
    per_node = result_body.get("per_node") or {}
    transitions = 0
    for body in per_node.values():
        samples = (((body or {}).get("tip_state") or {}).get("samples") or [])
        slots: list[int] = []
        for sample in samples:
            slot_value = (sample or {}).get("slot")
            hash_value = str((sample or {}).get("hash") or "").strip()
            try:
                slot_int = int(slot_value)
            except (TypeError, ValueError):
                continue
            if slot_int > 0 and hash_value:
                slots.append(slot_int)
        if len(set(slots)) >= 2:
            transitions += 1
    return transitions


def _honest_tip_groups(*, latest_tips: dict[str, dict], honest_node_ids: list[str]) -> Counter:
    groups: Counter = Counter()
    for node_id in honest_node_ids:
        tip = latest_tips.get(node_id) or {}
        hash_value = tip.get("hash")
        slot_value = tip.get("slot")
        if hash_value is None or slot_value is None:
            continue
        groups[(str(hash_value), int(slot_value))] += 1
    return groups


class TipConvergenceClean(AssertionPrimitive):
    """Pass iff the latest observation shows all nodes at a single converged tip."""

    def evaluate(self, handle):
        latest, payload, _, summary = _latest_multi_node_observation(handle)
        tolerance_slots = int(self.params.get("tolerance_slots", 0))
        latest_tips = summary.get("latest_tips") or {}
        slots = [int(tip.get("slot", 0)) for tip in latest_tips.values() if tip.get("slot") is not None]
        slot_spread = (max(slots) - min(slots)) if len(slots) >= 2 else 0
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and int(summary.get("node_count", 0)) >= 1
            and int(summary.get("tip_group_count", 0)) == 1
            and len(latest_tips) == int(summary.get("node_count", 0))
            and slot_spread <= tolerance_slots
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected all observed nodes to converge to one tip group"
        return {
            "primitive": "tip_convergence_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "tip_group_count": summary.get("tip_group_count"),
                "node_count": summary.get("node_count"),
                "slot_spread": slot_spread,
                "tolerance_slots": tolerance_slots,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class PeerConnectivityObserved(AssertionPrimitive):
    """Pass iff every expected honest-honest peer edge was observed."""

    def evaluate(self, handle):
        latest, payload, _, summary = _latest_multi_node_observation(handle)
        expected_edges = [
            (str(edge[0]), str(edge[1]))
            for edge in self.params.get("expected_edges", [])
            if isinstance(edge, (list, tuple)) and len(edge) == 2
        ]
        observed_edges = {
            (str(edge[0]), str(edge[1]))
            for edge in summary.get("observed_peer_edges", [])
            if isinstance(edge, (list, tuple)) and len(edge) == 2
        }
        if expected_edges:
            missing_edge_count = sum(1 for edge in expected_edges if edge not in observed_edges)
            expected_edge_count = len(expected_edges)
            observed_edge_count = expected_edge_count - missing_edge_count
        else:
            missing_edge_count = int(summary.get("missing_peer_edge_count", 0))
            expected_edge_count = int(summary.get("expected_peer_edge_count", 0))
            observed_edge_count = int(summary.get("observed_peer_edge_count", 0))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and expected_edge_count >= 1
            and missing_edge_count == 0
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected all honest-honest peer edges to be observed"
        return {
            "primitive": "peer_connectivity_observed",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "expected_peer_edge_count": expected_edge_count,
                "observed_peer_edge_count": observed_edge_count,
                "missing_peer_edge_count": missing_edge_count,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class AllNodesResponsive(AssertionPrimitive):
    """Pass iff every observed node was process-up and listening on its port."""

    def evaluate(self, handle):
        latest, payload, _, summary = _latest_multi_node_observation(handle)
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and int(summary.get("responsive_node_count", 0)) == int(summary.get("node_count", 0))
            and int(summary.get("node_count", 0)) >= 1
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected every observed node to be responsive"
        return {
            "primitive": "all_nodes_responsive",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "responsive_node_count": summary.get("responsive_node_count"),
                "node_count": summary.get("node_count"),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class ChainSelectConsistent(AssertionPrimitive):
    """Pass iff every observed node selected the same chain tip."""

    def evaluate(self, handle):
        latest, payload, _, summary = _latest_multi_node_observation(handle)
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and bool(summary.get("chain_select_consistent"))
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected every observed node to chain-select the same tip"
        return {
            "primitive": "chain_select_consistent",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "chain_select_consistent": summary.get("chain_select_consistent"),
                "tip_group_count": summary.get("tip_group_count"),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class ChainEventuallyConsistent(AssertionPrimitive):
    """Pass iff every observed node shared a fully common tip somewhere within the observation window."""

    def evaluate(self, handle):
        latest, payload, _, summary = _latest_multi_node_observation(handle)
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and bool(summary.get("chain_eventually_consistent"))
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected a fully shared tip somewhere within the observation window"
        return {
            "primitive": "chain_eventually_consistent",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "chain_eventually_consistent": summary.get("chain_eventually_consistent"),
                "tip_group_count": summary.get("tip_group_count"),
                "latest_common_tip": summary.get("latest_common_tip"),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class SubstrateQuorumObserved(AssertionPrimitive):
    """Pass iff a minimum fraction of observed nodes agree on one tip."""

    def evaluate(self, handle):
        latest, payload, result_body, summary = _latest_multi_node_observation(handle)
        minimum_fraction = float(self.params.get("minimum_fraction", 1.0))
        latest_tips = _multi_node_latest_tips(result_body, summary)
        tip_group_count = int(summary.get("tip_group_count", 0) or 0)
        quorum_count = int(summary.get("quorum_count", 0) or 0)
        real_tip_count = sum(1 for tip in latest_tips.values() if _tip_has_real_chain_progress(tip))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and float(summary.get("quorum_fraction", 0.0)) >= minimum_fraction
            and tip_group_count >= 1
            and real_tip_count >= max(1, quorum_count)
        )
        result = "pass" if enough else "fail"
        note = None if enough else f"expected quorum_fraction >= {minimum_fraction} with real non-zero tip evidence"
        return {
            "primitive": "substrate_quorum_observed",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "quorum_fraction": summary.get("quorum_fraction"),
                "quorum_count": quorum_count,
                "minimum_fraction": minimum_fraction,
                "tip_group_count": tip_group_count,
                "real_tip_count": real_tip_count,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class PeerEvictionWithinSeconds(AssertionPrimitive):
    """Pass iff honest nodes no longer list any byzantine peer inside the bounded observation window."""

    def evaluate(self, handle):
        latest, payload, result_body, summary = _latest_multi_node_observation(handle)
        honest_node_ids = [str(node_id) for node_id in self.params.get("honest_node_ids", [])]
        byzantine_node_ids = {str(node_id) for node_id in self.params.get("byzantine_node_ids", [])}
        timeout_seconds = float(self.params.get("timeout_seconds", 0))
        observation_window_seconds = float(result_body.get("observation_window_seconds", 0.0) or 0.0)
        connectivity = _multi_node_connectivity_map(result_body, summary)
        violating = {
            node_id: [peer for peer in connectivity.get(node_id, []) if peer in byzantine_node_ids]
            for node_id in honest_node_ids
        }
        violating = {node_id: peers for node_id, peers in violating.items() if peers}
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and bool(honest_node_ids)
            and bool(byzantine_node_ids)
            and not violating
            and (timeout_seconds <= 0 or observation_window_seconds <= timeout_seconds)
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected honest nodes to evict byzantine peers within the allowed window"
        return {
            "primitive": "peer_eviction_within_seconds",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "observation_window_seconds": observation_window_seconds,
                "timeout_seconds": timeout_seconds,
                "violating_honest_nodes": violating,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class ByzantineIsolationObserved(AssertionPrimitive):
    """Pass iff byzantine nodes are isolated from the configured honest set."""

    def evaluate(self, handle):
        latest, payload, result_body, summary = _latest_multi_node_observation(handle)
        honest_node_ids = {str(node_id) for node_id in self.params.get("honest_node_ids", [])}
        byzantine_node_ids = {str(node_id) for node_id in self.params.get("byzantine_node_ids", [])}
        connectivity = _multi_node_connectivity_map(result_body, summary)
        honest_to_byzantine = {
            node_id: [peer for peer in connectivity.get(node_id, []) if peer in byzantine_node_ids]
            for node_id in sorted(honest_node_ids)
        }
        byzantine_to_honest = {
            node_id: [peer for peer in connectivity.get(node_id, []) if peer in honest_node_ids]
            for node_id in sorted(byzantine_node_ids)
        }
        honest_to_byzantine = {k: v for k, v in honest_to_byzantine.items() if v}
        byzantine_to_honest = {k: v for k, v in byzantine_to_honest.items() if v}
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and bool(honest_node_ids)
            and bool(byzantine_node_ids)
            and not honest_to_byzantine
            and not byzantine_to_honest
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected complete isolation between honest and byzantine nodes"
        return {
            "primitive": "byzantine_isolation_observed",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "honest_to_byzantine": honest_to_byzantine,
                "byzantine_to_honest": byzantine_to_honest,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class HonestQuorumPreserved(AssertionPrimitive):
    """Pass iff the configured honest nodes retain quorum on one chain tip."""

    def evaluate(self, handle):
        latest, payload, result_body, summary = _latest_multi_node_observation(handle)
        honest_node_ids = [str(node_id) for node_id in self.params.get("honest_node_ids", [])]
        minimum_fraction = float(self.params.get("minimum_fraction", 1.0))
        latest_tips = _multi_node_latest_tips(result_body, summary)
        groups = _honest_tip_groups(latest_tips=latest_tips, honest_node_ids=honest_node_ids)
        quorum_count = max(groups.values()) if groups else 0
        honest_node_count = len(honest_node_ids)
        quorum_fraction = (quorum_count / honest_node_count) if honest_node_count else 0.0
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and honest_node_count >= 1
            and quorum_fraction >= minimum_fraction
        )
        result = "pass" if enough else "fail"
        note = None if enough else f"expected honest quorum fraction >= {minimum_fraction}"
        return {
            "primitive": "honest_quorum_preserved",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "honest_node_count": honest_node_count,
                "honest_quorum_count": quorum_count,
                "honest_quorum_fraction": quorum_fraction,
                "minimum_fraction": minimum_fraction,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class HonestPeerSetUncompromised(AssertionPrimitive):
    """Pass iff each honest node retains the configured minimum honest-peer count without capture."""

    def evaluate(self, handle):
        latest, payload, result_body, summary = _latest_multi_node_observation(handle)
        topology = summary.get("topology") or result_body.get("topology") or {}
        latest_tips = _multi_node_latest_tips(result_body, summary)
        honest_node_ids = [str(node_id) for node_id in self.params.get("honest_node_ids", [])]
        minimum_honest_peers = int(self.params.get("minimum_honest_peers", 1))
        honest_peer_counts = topology.get("honest_peer_counts") or {}
        peer_set_capture_detected = bool(topology.get("peer_set_capture_detected", False))
        tip_group_count = int(summary.get("tip_group_count", 0) or 0)
        below_threshold = {
            node_id: int(honest_peer_counts.get(node_id, 0))
            for node_id in honest_node_ids
            if int(honest_peer_counts.get(node_id, 0)) < minimum_honest_peers
        }
        missing_real_tip_nodes = [
            node_id for node_id in honest_node_ids if not _tip_has_real_chain_progress(latest_tips.get(node_id) or {})
        ]
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and bool(honest_node_ids)
            and not peer_set_capture_detected
            and not below_threshold
            and tip_group_count >= 1
            and not missing_real_tip_nodes
        )
        result = "pass" if enough else "fail"
        note = (
            None
            if enough
            else "expected each honest node to retain the minimum honest-peer count without capture and with real non-zero tip evidence"
        )
        return {
            "primitive": "honest_peer_set_uncompromised",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "minimum_honest_peers": minimum_honest_peers,
                "peer_set_capture_detected": peer_set_capture_detected,
                "below_threshold": below_threshold,
                "tip_group_count": tip_group_count,
                "missing_real_tip_nodes": missing_real_tip_nodes,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class HotWarmChurnWithinBounds(AssertionPrimitive):
    """Pass iff observed hot/warm churn stays within the configured ceiling."""

    def evaluate(self, handle):
        latest, payload, result_body, summary = _latest_multi_node_observation(handle)
        churn = summary.get("churn") or result_body.get("churn") or {}
        maximum_events_per_hour = float(
            self.params.get("maximum_events_per_hour", churn.get("baseline_ceiling_events_per_hour", 0.0) or 0.0)
        )
        observed_events_per_hour = float(churn.get("observed_events_per_hour", 0.0) or 0.0)
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and observed_events_per_hour <= maximum_events_per_hour
        )
        result = "pass" if enough else "fail"
        note = None if enough else f"expected observed churn <= {maximum_events_per_hour} events/hour"
        return {
            "primitive": "hot_warm_churn_within_bounds",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "observed_events_per_hour": observed_events_per_hour,
                "maximum_events_per_hour": maximum_events_per_hour,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class LedgerPeerStakeWeightPreserved(AssertionPrimitive):
    """Pass iff observed ledger-peer stake weighting remains within the allowed delta."""

    def evaluate(self, handle):
        latest, payload, result_body, summary = _latest_multi_node_observation(handle)
        ledger_peers = summary.get("ledger_peers") or result_body.get("ledger_peers") or {}
        maximum_absolute_delta = float(self.params.get("maximum_absolute_delta", 0.0) or 0.0)
        observed_delta = float(ledger_peers.get("max_absolute_delta", 1.0) or 0.0)
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and observed_delta <= maximum_absolute_delta
        )
        result = "pass" if enough else "fail"
        note = None if enough else f"expected stake-weight delta <= {maximum_absolute_delta}"
        return {
            "primitive": "ledger_peer_stake_weight_preserved",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "max_absolute_delta": observed_delta,
                "maximum_absolute_delta": maximum_absolute_delta,
                "expected_stake_distribution": ledger_peers.get("expected_stake_distribution"),
                "observed_stake_distribution": ledger_peers.get("observed_stake_distribution"),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class BigLedgerPeerQuorumIntact(AssertionPrimitive):
    """Pass iff the observed BLP subset still contains enough expected top peers."""

    def evaluate(self, handle):
        latest, payload, result_body, summary = _latest_multi_node_observation(handle)
        big_ledger_peers = summary.get("big_ledger_peers") or result_body.get("big_ledger_peers") or {}
        expected = [str(peer_id) for peer_id in big_ledger_peers.get("expected_top_peer_ids", [])]
        observed = [str(peer_id) for peer_id in big_ledger_peers.get("observed_top_peer_ids", [])]
        minimum_expected_matches = int(self.params.get("minimum_expected_matches", 1))
        matches = sorted(set(expected) & set(observed))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and len(matches) >= minimum_expected_matches
        )
        result = "pass" if enough else "fail"
        note = None if enough else f"expected at least {minimum_expected_matches} expected BLP matches"
        return {
            "primitive": "big_ledger_peer_quorum_intact",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "minimum_expected_matches": minimum_expected_matches,
                "matched_peer_ids": matches,
                "expected_top_peer_ids": expected,
                "observed_top_peer_ids": observed,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class StakeSnapshotFreezeConsistent(AssertionPrimitive):
    """Pass iff the stake snapshot remains stable across the freeze window."""

    def evaluate(self, handle):
        latest, payload, result_body, summary = _latest_multi_node_observation(handle)
        stake_snapshot = summary.get("stake_snapshot") or result_body.get("stake_snapshot") or {}
        snapshot_hashes = [str(value) for value in stake_snapshot.get("snapshot_hashes", [])]
        freeze_window_stable = bool(stake_snapshot.get("freeze_window_stable", False))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and freeze_window_stable
            and len(set(snapshot_hashes)) <= 1
            and len(snapshot_hashes) >= 1
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected stake snapshot to remain byte-stable across the freeze window"
        return {
            "primitive": "stake_snapshot_freeze_consistent",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "freeze_window_stable": freeze_window_stable,
                "snapshot_hashes": snapshot_hashes,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class EpochBoundaryTimingWithinBounds(AssertionPrimitive):
    """Pass iff the observed epoch boundary falls inside the expected slot window."""

    def evaluate(self, handle):
        latest, payload, result_body, summary = _latest_multi_node_observation(handle)
        epoch_boundary = summary.get("epoch_boundary") or result_body.get("epoch_boundary") or {}
        observed_slot = epoch_boundary.get("observed_boundary_slot")
        window_start = epoch_boundary.get("expected_window_start")
        window_end = epoch_boundary.get("expected_window_end")
        in_range = (
            observed_slot is not None
            and window_start is not None
            and window_end is not None
            and int(window_start) <= int(observed_slot) <= int(window_end)
        )
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and in_range
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected epoch boundary to occur within the configured slot window"
        return {
            "primitive": "epoch_boundary_timing_within_bounds",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "observed_boundary_slot": observed_slot,
                "expected_window_start": window_start,
                "expected_window_end": window_end,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class LeadershipScheduleRecomputesClean(AssertionPrimitive):
    """Pass iff leadership schedule recomputation matches deterministically."""

    def evaluate(self, handle):
        latest, payload, result_body, summary = _latest_multi_node_observation(handle)
        leadership_schedule = summary.get("leadership_schedule") or result_body.get("leadership_schedule") or {}
        deterministic_match = bool(leadership_schedule.get("deterministic_match", False))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and deterministic_match
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected leadership schedule recomputation to match deterministically"
        return {
            "primitive": "leadership_schedule_recomputes_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "deterministic_match": deterministic_match,
                "expected_schedule_hash": leadership_schedule.get("expected_schedule_hash"),
                "recomputed_schedule_hash": leadership_schedule.get("recomputed_schedule_hash"),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class RewardCalculationBoundaryInvariant(AssertionPrimitive):
    """Pass iff reward totals remain invariant across the epoch boundary calculation path."""

    def evaluate(self, handle):
        latest, payload, result_body, summary = _latest_multi_node_observation(handle)
        reward_calculation = summary.get("reward_calculation") or result_body.get("reward_calculation") or {}
        invariant_holds = bool(reward_calculation.get("boundary_invariant_holds", False))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and invariant_holds
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected reward calculation invariants to hold across the epoch boundary"
        return {
            "primitive": "reward_calculation_boundary_invariant",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "boundary_invariant_holds": invariant_holds,
                "expected_total_rewards": reward_calculation.get("expected_total_rewards"),
                "observed_total_rewards": reward_calculation.get("observed_total_rewards"),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class TxsubmissionWindowEnforced(AssertionPrimitive):
    """Pass iff the negotiated TxSubmission txid window is enforced."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_txsubmission_window_pressure")
        result_body = payload.get("result") or {}
        min_txids_processed = int(self.params.get("min_txids_processed", 1) or 0)
        min_messages_observed = int(self.params.get("min_txsubmission_messages_observed", 3) or 0)
        negotiated_window = int(result_body.get("negotiated_window", 0) or 0)
        max_in_flight = int(result_body.get("max_in_flight_txids", 0) or 0)
        overflow_rejected = bool(result_body.get("overflow_rejected", False))
        txids_processed = int(result_body.get("txids_processed", 0) or 0)
        messages_observed = int(result_body.get("txsubmission_messages_observed", 0) or 0)
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and negotiated_window >= 1
            and max_in_flight <= negotiated_window
            and overflow_rejected
            and txids_processed >= min_txids_processed
            and messages_observed >= min_messages_observed
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected negotiated txid window to reject overflow cleanly"
        return {
            "primitive": "txsubmission_window_enforced",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "negotiated_window": negotiated_window,
                "max_in_flight_txids": max_in_flight,
                "overflow_rejected": overflow_rejected,
                "txids_processed": txids_processed,
                "txsubmission_messages_observed": messages_observed,
                "min_txids_processed": min_txids_processed,
                "min_txsubmission_messages_observed": min_messages_observed,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class TxsubmissionBatchEnforced(AssertionPrimitive):
    """Pass iff the negotiated TxSubmission body batch limit is enforced."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_txsubmission_batch_pressure")
        result_body = payload.get("result") or {}
        negotiated_limit = int(result_body.get("negotiated_batch_limit", 0) or 0)
        max_batch = int(result_body.get("max_batch_observed", 0) or 0)
        oversized_batch_rejected = bool(result_body.get("oversized_batch_rejected", False))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and negotiated_limit >= 1
            and max_batch <= negotiated_limit
            and oversized_batch_rejected
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected negotiated tx body batch limit to reject oversize cleanly"
        return {
            "primitive": "txsubmission_batch_enforced",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "negotiated_batch_limit": negotiated_limit,
                "max_batch_observed": max_batch,
                "oversized_batch_rejected": oversized_batch_rejected,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class TxsubmissionUnexpectedBodyRejected(AssertionPrimitive):
    """Pass iff a not-in-flight or unexpected TxSubmission body is rejected."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_txsubmission_unexpected_body")
        result_body = payload.get("result") or {}
        unexpected_body_rejected = bool(result_body.get("unexpected_body_rejected", False))
        rejection_reason = str(result_body.get("rejection_reason", ""))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and unexpected_body_rejected
            and bool(rejection_reason)
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected unexpected TxSubmission body to be rejected with a reason"
        return {
            "primitive": "txsubmission_unexpected_body_rejected",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "unexpected_body_rejected": unexpected_body_rejected,
                "rejection_reason": rejection_reason,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class MempoolFailureContained(AssertionPrimitive):
    """Pass iff a mempool failure path is contained without taking the node down."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_mempool_failure_probe")
        result_body = payload.get("result") or {}
        fatal_error_contained = bool(result_body.get("fatal_error_contained", False))
        node_stayed_up = bool(result_body.get("node_stayed_up", False))
        protocol_session_survived = bool(result_body.get("protocol_session_survived", False))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and fatal_error_contained
            and node_stayed_up
            and protocol_session_survived
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected mempool failure to stay contained without node loss"
        return {
            "primitive": "mempool_failure_contained",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "fatal_error_contained": fatal_error_contained,
                "node_stayed_up": node_stayed_up,
                "protocol_session_survived": protocol_session_survived,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class ChainsyncParentDiscontinuityRejected(AssertionPrimitive):
    """Pass iff a parent-hash discontinuity is rejected without advancing candidate state."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_chainsync_parent_discontinuity")
        result_body = payload.get("result") or {}
        rejected = bool(result_body.get("rejected", False))
        candidate_chain_advanced = bool(result_body.get("candidate_chain_advanced", False))
        enough = latest is not None and payload.get("outcome") == "ok" and rejected and not candidate_chain_advanced
        result = "pass" if enough else "fail"
        note = None if enough else "expected parent-hash discontinuity to be rejected before candidate advancement"
        return {
            "primitive": "chainsync_parent_discontinuity_rejected",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "rejected": rejected,
                "candidate_chain_advanced": candidate_chain_advanced,
                "rejection_reason": result_body.get("rejection_reason"),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class ChainsyncHeightMonotonicityEnforced(AssertionPrimitive):
    """Pass iff non-incrementing ChainSync heights are rejected cleanly."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_chainsync_nonincrementing_height")
        result_body = payload.get("result") or {}
        rejected = bool(result_body.get("rejected", False))
        observed_height_delta = int(result_body.get("observed_height_delta", 1) or 0)
        candidate_chain_advanced = bool(result_body.get("candidate_chain_advanced", False))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and rejected
            and observed_height_delta <= 0
            and not candidate_chain_advanced
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected non-incrementing ChainSync height to be rejected cleanly"
        return {
            "primitive": "chainsync_height_monotonicity_enforced",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "rejected": rejected,
                "observed_height_delta": observed_height_delta,
                "candidate_chain_advanced": candidate_chain_advanced,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class ChainsyncSlotMonotonicityEnforced(AssertionPrimitive):
    """Pass iff non-monotonic ChainSync slots are rejected cleanly."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_chainsync_nonmonotonic_slot")
        result_body = payload.get("result") or {}
        rejected = bool(result_body.get("rejected", False))
        observed_slot_delta = int(result_body.get("observed_slot_delta", 1) or 0)
        candidate_chain_advanced = bool(result_body.get("candidate_chain_advanced", False))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and rejected
            and observed_slot_delta < 0
            and not candidate_chain_advanced
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected non-monotonic ChainSync slot to be rejected cleanly"
        return {
            "primitive": "chainsync_slot_monotonicity_enforced",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "rejected": rejected,
                "observed_slot_delta": observed_slot_delta,
                "candidate_chain_advanced": candidate_chain_advanced,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class BlockfetchInvalidRangeRejected(AssertionPrimitive):
    """Pass iff invalid BlockFetch ranges are rejected without serving blocks."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_blockfetch_invalid_range")
        result_body = payload.get("result") or {}
        invalid_range_rejected = bool(result_body.get("invalid_range_rejected", False))
        served_blocks = int(result_body.get("served_blocks", 1) or 0)
        enough = latest is not None and payload.get("outcome") == "ok" and invalid_range_rejected and served_blocks == 0
        result = "pass" if enough else "fail"
        note = None if enough else "expected invalid BlockFetch range to be rejected without serving blocks"
        return {
            "primitive": "blockfetch_invalid_range_rejected",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "invalid_range_rejected": invalid_range_rejected,
                "served_blocks": served_blocks,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class BlockfetchRangePressureBounded(AssertionPrimitive):
    """Pass iff BlockFetch range pressure stays inside configured resource bounds."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_blockfetch_range_pressure")
        result_body = payload.get("result") or {}
        resource_bound_ok = bool(result_body.get("resource_bound_ok", False))
        observed_peak = int(result_body.get("observed_peak_blocks_in_memory", 0) or 0)
        configured_limit = int(result_body.get("configured_limit", 0) or 0)
        blocks_fetched = int(result_body.get("blocks_fetched", 0) or 0)
        block_range_requests_observed = int(result_body.get("block_range_requests_observed", 0) or 0)
        min_blocks_fetched = int(self.params.get("min_blocks_fetched", 1))
        min_block_range_requests_observed = int(self.params.get("min_block_range_requests_observed", 1))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and resource_bound_ok
            and configured_limit >= 1
            and observed_peak <= configured_limit
            and blocks_fetched >= min_blocks_fetched
            and block_range_requests_observed >= min_block_range_requests_observed
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected BlockFetch range pressure to stay inside configured resource bounds"
        return {
            "primitive": "blockfetch_range_pressure_bounded",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "resource_bound_ok": resource_bound_ok,
                "observed_peak_blocks_in_memory": observed_peak,
                "configured_limit": configured_limit,
                "blocks_fetched": blocks_fetched,
                "block_range_requests_observed": block_range_requests_observed,
                "min_blocks_fetched": min_blocks_fetched,
                "min_block_range_requests_observed": min_block_range_requests_observed,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class BlockfetchInvalidBlockRejected(AssertionPrimitive):
    """Pass iff invalid block CBOR is rejected on the BlockFetch path."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_blockfetch_invalid_block_cbor")
        result_body = payload.get("result") or {}
        invalid_block_rejected = bool(result_body.get("invalid_block_rejected", False))
        enough = latest is not None and payload.get("outcome") == "ok" and invalid_block_rejected
        result = "pass" if enough else "fail"
        note = None if enough else "expected invalid BlockFetch block payload to be rejected"
        return {
            "primitive": "blockfetch_invalid_block_rejected",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "invalid_block_rejected": invalid_block_rejected,
                "decode_path": result_body.get("decode_path"),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class BlockfetchResponseRangeStrict(AssertionPrimitive):
    """Pass iff mismatched response ranges are rejected rather than accepted."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_blockfetch_range_mismatch")
        result_body = payload.get("result") or {}
        range_mismatch_rejected = bool(result_body.get("range_mismatch_rejected", False))
        accepted_mismatched_range = bool(result_body.get("accepted_mismatched_range", False))
        enough = latest is not None and payload.get("outcome") == "ok" and range_mismatch_rejected and not accepted_mismatched_range
        result = "pass" if enough else "fail"
        note = None if enough else "expected mismatched BlockFetch response range to be rejected strictly"
        return {
            "primitive": "blockfetch_response_range_strict",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "range_mismatch_rejected": range_mismatch_rejected,
                "accepted_mismatched_range": accepted_mismatched_range,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class BlockfetchContinuityFailureRejected(AssertionPrimitive):
    """Pass iff fetched continuity failures are rejected before persistence-like advancement."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_blockfetch_continuity_failure")
        result_body = payload.get("result") or {}
        continuity_failure_rejected = bool(result_body.get("continuity_failure_rejected", False))
        downstream_state_advanced = bool(result_body.get("downstream_state_advanced", False))
        enough = latest is not None and payload.get("outcome") == "ok" and continuity_failure_rejected and not downstream_state_advanced
        result = "pass" if enough else "fail"
        note = None if enough else "expected continuity failure to be rejected before downstream advancement"
        return {
            "primitive": "blockfetch_continuity_failure_rejected",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "continuity_failure_rejected": continuity_failure_rejected,
                "downstream_state_advanced": downstream_state_advanced,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class ChainsyncResponderRollbackThenForwardClean(AssertionPrimitive):
    """Pass iff a producer-side fork switch yields rollback-then-forward follower behavior."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_chainsync_responder_fork_switch")
        result_body = payload.get("result") or {}
        min_rollback_then_forward_count = int(self.params.get("min_rollback_then_forward_count", 1))
        min_chainsync_messages_observed = int(self.params.get("min_chainsync_messages_observed", 3))
        rollback_then_forward_sequence_observed = bool(result_body.get("rollback_then_forward_sequence_observed", False))
        follower_state_rewritten = bool(result_body.get("follower_state_rewritten", False))
        rollback_then_forward_count = int(result_body.get("rollback_then_forward_count", 0) or 0)
        chainsync_messages_observed = int(result_body.get("chainsync_messages_observed", 0) or 0)
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and rollback_then_forward_sequence_observed
            and follower_state_rewritten
            and rollback_then_forward_count >= min_rollback_then_forward_count
            and chainsync_messages_observed >= min_chainsync_messages_observed
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected producer fork-switch to yield rollback-then-forward follower behavior"
        effective_params = dict(self.params)
        effective_params.setdefault("min_rollback_then_forward_count", min_rollback_then_forward_count)
        effective_params.setdefault("min_chainsync_messages_observed", min_chainsync_messages_observed)
        return {
            "primitive": "chainsync_responder_rollback_then_forward_clean",
            "params": effective_params,
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "rollback_then_forward_sequence_observed": rollback_then_forward_sequence_observed,
                "follower_state_rewritten": follower_state_rewritten,
                "rollback_then_forward_count": rollback_then_forward_count,
                "chainsync_messages_observed": chainsync_messages_observed,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class TopologyBootstrapDiversityPreserved(AssertionPrimitive):
    """Pass iff bootstrap topology retains the configured honest diversity floor."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_bootstrap_topology_concentration")
        result_body = payload.get("result") or {}
        minimum_peer_diversity_met = bool(result_body.get("minimum_peer_diversity_met", False))
        trustable_peer_count = int(result_body.get("trustable_peer_count", 0) or 0)
        minimum_required = int(result_body.get("minimum_required_trustable_peers", 0) or 0)
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and minimum_peer_diversity_met
            and trustable_peer_count >= minimum_required
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected bootstrap topology to preserve the minimum honest peer diversity floor"
        return {
            "primitive": "topology_bootstrap_diversity_preserved",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "minimum_peer_diversity_met": minimum_peer_diversity_met,
                "trustable_peer_count": trustable_peer_count,
                "minimum_required_trustable_peers": minimum_required,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class LocalQueryAmplificationBounded(AssertionPrimitive):
    """Pass iff local-query amplification is rate-limited without blocking critical work."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_local_query_stress")
        result_body = payload.get("result") or {}
        rate_limit_triggered = bool(result_body.get("rate_limit_triggered", False))
        critical_work_blocked = bool(result_body.get("critical_work_blocked", True))
        peak_cpu_pct = float(result_body.get("peak_cpu_pct", 100.0) or 100.0)
        cpu_ceiling_pct = float(result_body.get("cpu_ceiling_pct", 0.0) or 0.0)
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and rate_limit_triggered
            and not critical_work_blocked
            and peak_cpu_pct <= cpu_ceiling_pct
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected local-query amplification to stay below the configured CPU ceiling"
        return {
            "primitive": "local_query_amplification_bounded",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "rate_limit_triggered": rate_limit_triggered,
                "critical_work_blocked": critical_work_blocked,
                "peak_cpu_pct": peak_cpu_pct,
                "cpu_ceiling_pct": cpu_ceiling_pct,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class LocalSubmitAvailabilityPreserved(AssertionPrimitive):
    """Pass iff local-submit stress does not take the node down or overflow its submit queue budget."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_local_submit_stress")
        result_body = payload.get("result") or {}
        request_limits_enforced = bool(result_body.get("request_limits_enforced", False))
        node_stayed_up = bool(result_body.get("node_stayed_up", False))
        submit_queue_depth_peak = int(result_body.get("submit_queue_depth_peak", 2**31 - 1) or 0)
        submit_queue_depth_limit = int(result_body.get("submit_queue_depth_limit", 0) or 0)
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and request_limits_enforced
            and node_stayed_up
            and submit_queue_depth_peak <= submit_queue_depth_limit
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected local-submit stress to preserve node availability and queue limits"
        return {
            "primitive": "local_submit_availability_preserved",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "request_limits_enforced": request_limits_enforced,
                "node_stayed_up": node_stayed_up,
                "submit_queue_depth_peak": submit_queue_depth_peak,
                "submit_queue_depth_limit": submit_queue_depth_limit,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class BootstrapAssumptionsSafe(AssertionPrimitive):
    """Pass iff bootstrap assumptions stay explicit and no unsafe downgrade/default path is accepted."""

    def evaluate(self, handle):
        source_primitive = str(self.params.get("source_primitive") or "runtime_bootstrap_assumption_probe")
        latest, payload = _latest_completed_payload(handle, phase="load", primitive=source_primitive)
        result_body = payload.get("result") or {}
        unsafe_defaults_detected = bool(result_body.get("unsafe_defaults_detected", True))
        expected_genesis_hash_verified = bool(result_body.get("expected_genesis_hash_verified", False))
        version_negotiation_downgrade_seen = bool(result_body.get("version_negotiation_downgrade_seen", True))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and not unsafe_defaults_detected
            and expected_genesis_hash_verified
            and not version_negotiation_downgrade_seen
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected bootstrap assumptions to remain explicit and downgrade-free"
        return {
            "primitive": "bootstrap_assumptions_safe",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "unsafe_defaults_detected": unsafe_defaults_detected,
                "expected_genesis_hash_verified": expected_genesis_hash_verified,
                "version_negotiation_downgrade_seen": version_negotiation_downgrade_seen,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class MuxIngressOverrunScoped(AssertionPrimitive):
    """Pass iff a mux ingress overrun is scoped to the offending bearer."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_mux_ingress_overrun")
        result_body = payload.get("result") or {}
        offending_bearer_disconnected = bool(result_body.get("offending_bearer_disconnected", False))
        non_offending_bearers_preserved = bool(result_body.get("non_offending_bearers_preserved", False))
        queue_budget_respected = bool(result_body.get("queue_budget_respected", False))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and offending_bearer_disconnected
            and non_offending_bearers_preserved
            and queue_budget_respected
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected mux ingress overrun to be scoped to the offending bearer"
        return {
            "primitive": "mux_ingress_overrun_scoped",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "offending_bearer_disconnected": offending_bearer_disconnected,
                "non_offending_bearers_preserved": non_offending_bearers_preserved,
                "queue_budget_respected": queue_budget_respected,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class DuplexPromotionSlotLimitEnforced(AssertionPrimitive):
    """Pass iff duplex promotion pressure respects the configured hard slot limit."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_duplex_promotion_pressure")
        result_body = payload.get("result") or {}
        hard_limit_exceeded = bool(result_body.get("hard_limit_exceeded", True))
        inbound_preferred_reset_applied = bool(result_body.get("inbound_preferred_reset_applied", False))
        accepted_connection_count_peak = int(result_body.get("accepted_connection_count_peak", 2**31 - 1) or 0)
        hard_limit = int(result_body.get("hard_limit", 0) or 0)
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and not hard_limit_exceeded
            and inbound_preferred_reset_applied
            and accepted_connection_count_peak <= hard_limit
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected duplex promotion pressure to respect the configured hard limit"
        return {
            "primitive": "duplex_promotion_slot_limit_enforced",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "hard_limit_exceeded": hard_limit_exceeded,
                "inbound_preferred_reset_applied": inbound_preferred_reset_applied,
                "accepted_connection_count_peak": accepted_connection_count_peak,
                "hard_limit": hard_limit,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class KeepaliveFailureBudgetBounded(AssertionPrimitive):
    """Pass iff keepalive failures stay within the configured retry budget without cascade."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_keepalive_failure_cascade")
        result_body = payload.get("result") or {}
        retry_budget_exhausted = bool(result_body.get("retry_budget_exhausted", True))
        cooling_cascade_bounded = bool(result_body.get("cooling_cascade_bounded", False))
        keepalive_failures_observed = int(result_body.get("keepalive_failures_observed", 2**31 - 1) or 0)
        max_keepalive_failures = int(result_body.get("max_keepalive_failures", 0) or 0)
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and not retry_budget_exhausted
            and cooling_cascade_bounded
            and keepalive_failures_observed <= max_keepalive_failures
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected keepalive failures to stay within the configured retry budget"
        return {
            "primitive": "keepalive_failure_budget_bounded",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "retry_budget_exhausted": retry_budget_exhausted,
                "cooling_cascade_bounded": cooling_cascade_bounded,
                "keepalive_failures_observed": keepalive_failures_observed,
                "max_keepalive_failures": max_keepalive_failures,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class PraosHeaderAssertionRejected(AssertionPrimitive):
    """Pass iff an invalid Praos header is rejected at the intended assertion boundary."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_praos_header_assertion_probe")
        result_body = payload.get("result") or {}
        header_rejected = bool(result_body.get("header_rejected", False))
        assertion_boundary_preserved = bool(result_body.get("assertion_boundary_preserved", False))
        enough = latest is not None and payload.get("outcome") == "ok" and header_rejected and assertion_boundary_preserved
        result = "pass" if enough else "fail"
        note = None if enough else "expected invalid Praos header to be rejected at the assertion boundary"
        return {
            "primitive": "praos_header_assertion_rejected",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "header_rejected": header_rejected,
                "assertion_boundary_preserved": assertion_boundary_preserved,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class MalformedInputParityPreserved(AssertionPrimitive):
    """Pass iff malformed-input handling matches the reference behavior."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_malformed_input_differential")
        result_body = payload.get("result") or {}
        parity_match = bool(result_body.get("parity_match", False))
        observed_divergence = bool(result_body.get("observed_divergence", True))
        enough = latest is not None and payload.get("outcome") == "ok" and parity_match and not observed_divergence
        result = "pass" if enough else "fail"
        note = None if enough else "expected malformed-input handling to preserve reference parity"
        return {
            "primitive": "malformed_input_parity_preserved",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "parity_match": parity_match,
                "observed_divergence": observed_divergence,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class ValidationPathParityPreserved(AssertionPrimitive):
    """Pass iff validation-path behavior matches the Haskell reference."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_validation_path_differential")
        result_body = payload.get("result") or {}
        parity_match = bool(result_body.get("parity_match", False))
        mismatched_validation_steps = int(result_body.get("mismatched_validation_steps", 2**31 - 1) or 0)
        enough = latest is not None and payload.get("outcome") == "ok" and parity_match and mismatched_validation_steps == 0
        result = "pass" if enough else "fail"
        note = None if enough else "expected validation-path behavior to preserve reference parity"
        return {
            "primitive": "validation_path_parity_preserved",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "parity_match": parity_match,
                "mismatched_validation_steps": mismatched_validation_steps,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class PlutusPhase2IsValidMismatchRejected(AssertionPrimitive):
    """Pass iff a mis-flagged IsValid witness is rejected with ValidationTagMismatch semantics."""

    def evaluate(self, handle):
        source_primitive = str(self.params.get("source_primitive") or "runtime_plutus_phase2_submit_probe")
        latest, payload = _latest_completed_payload(handle, phase="load", primitive=source_primitive)
        result_body = payload.get("result") or {}
        admission_decision = str(result_body.get("admission_decision") or "").lower()
        rejection_reason = str(result_body.get("rejection_reason") or "")
        validation_tag_mismatch_detected = bool(result_body.get("validation_tag_mismatch_detected", False))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and admission_decision == "rejected"
            and rejection_reason == "ValidationTagMismatch"
            and validation_tag_mismatch_detected
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected phase-2 IsValid mismatch to be rejected with ValidationTagMismatch"
        return {
            "primitive": "plutus_phase2_isvalid_mismatch_rejected",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "admission_decision": admission_decision,
                "rejection_reason": rejection_reason,
                "validation_tag_mismatch_detected": validation_tag_mismatch_detected,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class PlutusPhase2ExUnitsOverrunRejected(AssertionPrimitive):
    """Pass iff phase-2 admission rejects transactions whose ExUnits exceed the configured limit."""

    def evaluate(self, handle):
        source_primitive = str(self.params.get("source_primitive") or "runtime_plutus_phase2_submit_probe")
        latest, payload = _latest_completed_payload(handle, phase="load", primitive=source_primitive)
        result_body = payload.get("result") or {}
        admission_decision = str(result_body.get("admission_decision") or "").lower()
        exunits_limit_enforced = bool(result_body.get("exunits_limit_enforced", False))
        observed_exunits = int(result_body.get("observed_exunits", 0) or 0)
        max_tx_exunits = int(result_body.get("max_tx_exunits", 0) or 0)
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and admission_decision == "rejected"
            and exunits_limit_enforced
            and observed_exunits > max_tx_exunits >= 1
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected phase-2 ExUnits overrun to be rejected on mempool admission"
        return {
            "primitive": "plutus_phase2_exunits_overrun_rejected",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "admission_decision": admission_decision,
                "exunits_limit_enforced": exunits_limit_enforced,
                "observed_exunits": observed_exunits,
                "max_tx_exunits": max_tx_exunits,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class PlutusPhase2DoNotInterveneRetryClean(AssertionPrimitive):
    """Pass iff the DoNotIntervene retry path stays inside the configured retry budget and matches spec."""

    def evaluate(self, handle):
        source_primitive = str(self.params.get("source_primitive") or "runtime_plutus_phase2_submit_probe")
        latest, payload = _latest_completed_payload(handle, phase="load", primitive=source_primitive)
        result_body = payload.get("result") or {}
        retry_behavior_matches_spec = bool(result_body.get("retry_behavior_matches_spec", False))
        retry_count_observed = int(result_body.get("retry_count_observed", 2**31 - 1) or 0)
        retry_budget = int(result_body.get("retry_budget", 0) or 0)
        terminal_outcome_recorded = bool(result_body.get("terminal_outcome_recorded", False))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and retry_behavior_matches_spec
            and terminal_outcome_recorded
            and retry_count_observed <= retry_budget
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected DoNotIntervene retry behavior to remain within the configured budget"
        return {
            "primitive": "plutus_phase2_donotintervene_retry_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "retry_behavior_matches_spec": retry_behavior_matches_spec,
                "retry_count_observed": retry_count_observed,
                "retry_budget": retry_budget,
                "terminal_outcome_recorded": terminal_outcome_recorded,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class PlutusPhase2DifferentialEquivalent(AssertionPrimitive):
    """Pass iff Amaru and cardano-node agree on phase-2 admission/rejection behavior for the same Plutus transaction."""

    def evaluate(self, handle):
        source_primitive = str(self.params.get("source_primitive") or "runtime_plutus_phase2_differential_observation")
        latest, payload = _latest_completed_payload(handle, phase="load", primitive=source_primitive)
        result_body = payload.get("result") or {}
        equivalent = bool(result_body.get("equivalent", False))
        amaru_decision = str(result_body.get("amaru_decision") or "")
        cardano_node_decision = str(result_body.get("cardano_node_decision") or "")
        reason_equivalent = bool(result_body.get("reason_equivalent", False))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and equivalent
            and reason_equivalent
            and bool(amaru_decision)
            and bool(cardano_node_decision)
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected Amaru and cardano-node to agree on phase-2 Plutus admission behavior"
        return {
            "primitive": "plutus_phase2_differential_equivalent",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "equivalent": equivalent,
                "amaru_decision": amaru_decision,
                "cardano_node_decision": cardano_node_decision,
                "reason_equivalent": reason_equivalent,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class MempoolRelayPressureBounded(AssertionPrimitive):
    """Pass iff mempool-relay pressure remains inside the configured work or memory budget."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_mempool_relay_pressure")
        result_body = payload.get("result") or {}
        work_bounded = bool(result_body.get("work_bounded", False))
        peak_memory_mb = float(result_body.get("peak_memory_mb", float("inf")) or 0.0)
        memory_ceiling_mb = float(result_body.get("memory_ceiling_mb", 0.0) or 0.0)
        enough = latest is not None and payload.get("outcome") == "ok" and work_bounded and peak_memory_mb <= memory_ceiling_mb
        result = "pass" if enough else "fail"
        note = None if enough else "expected mempool-relay pressure to remain inside the configured budget"
        return {
            "primitive": "mempool_relay_pressure_bounded",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "work_bounded": work_bounded,
                "peak_memory_mb": peak_memory_mb,
                "memory_ceiling_mb": memory_ceiling_mb,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class ParserBoundsEnforced(AssertionPrimitive):
    """Pass iff parser and deserialization bounds are enforced before unbounded work occurs."""

    def evaluate(self, handle):
        source_primitive = str(self.params.get("source_primitive") or "runtime_parser_bounds_probe")
        latest, payload = _latest_completed_payload(handle, phase="load", primitive=source_primitive)
        result_body = payload.get("result") or {}
        bounds_enforced = bool(result_body.get("bounds_enforced", False))
        unbounded_work_observed = bool(result_body.get("unbounded_work_observed", True))
        enough = latest is not None and payload.get("outcome") == "ok" and bounds_enforced and not unbounded_work_observed
        result = "pass" if enough else "fail"
        note = None if enough else "expected parser and deserialization bounds to be enforced before unbounded work"
        return {
            "primitive": "parser_bounds_enforced",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "bounds_enforced": bounds_enforced,
                "unbounded_work_observed": unbounded_work_observed,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class RuntimeStarvationBounded(AssertionPrimitive):
    """Pass iff blocking work does not induce observable runtime starvation."""

    def evaluate(self, handle):
        source_primitive = str(self.params.get("source_primitive") or "runtime_blocking_work_starvation")
        latest, payload = _latest_completed_payload(handle, phase="load", primitive=source_primitive)
        result_body = payload.get("result") or {}
        starvation_detected = bool(result_body.get("starvation_detected", True))
        liveness_preserved = bool(result_body.get("liveness_preserved", False))
        enough = latest is not None and payload.get("outcome") == "ok" and not starvation_detected and liveness_preserved
        result = "pass" if enough else "fail"
        note = None if enough else "expected blocking work to preserve runtime liveness without starvation"
        return {
            "primitive": "runtime_starvation_bounded",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "starvation_detected": starvation_detected,
                "liveness_preserved": liveness_preserved,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class ContainerRuntimeHardeningObserved(AssertionPrimitive):
    """Pass iff captured docker-inspect artifacts show the expected runtime hardening shape."""

    _ALLOWED_USERS = {"1000:1000", "dwarf"}

    @staticmethod
    def _load_inspect_entry(path: Path) -> dict:
        body = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(body, list):
            return dict(body[0] or {}) if body else {}
        if isinstance(body, dict):
            return body
        return {}

    @staticmethod
    def _tmpfs_non_empty(value: object) -> bool:
        if isinstance(value, dict):
            return bool(value)
        if isinstance(value, list):
            return any(str(item).strip() for item in value)
        return False

    def evaluate(self, handle):
        source_primitive = str(self.params.get("source_primitive") or "runtime_container_runtime_inspect")
        latest, payload = _latest_completed_payload(handle, phase="load", primitive=source_primitive)
        result_body = payload.get("result") or {}
        containers = list(result_body.get("containers") or [])
        expected_node_ids = [str(node_id) for node_id in list(self.params.get("expected_node_ids") or [])]
        minimum_container_count = int(self.params.get("minimum_container_count", max(1, len(expected_node_ids) or 1)))

        observed_node_ids: list[str] = []
        missing_artifacts: list[str] = []
        non_hardened: list[dict] = []

        for container in containers:
            node_id = str(container.get("node_id") or "")
            observed_node_ids.append(node_id)
            inspect_path_text = str(container.get("inspect_path") or "").strip()
            if not inspect_path_text:
                missing_artifacts.append(node_id or str(container.get("container_name") or "unknown"))
                continue
            inspect_path = _resolve_output_path(handle, inspect_path_text)
            if not inspect_path.is_file():
                missing_artifacts.append(node_id or str(container.get("container_name") or inspect_path_text))
                continue
            entry = self._load_inspect_entry(inspect_path)
            host_config = dict((entry or {}).get("HostConfig") or {})
            config = dict((entry or {}).get("Config") or {})
            readonly_rootfs = bool(host_config.get("ReadonlyRootfs", False))
            cap_drop = [str(item) for item in list(host_config.get("CapDrop") or [])]
            security_opt = [str(item) for item in list(host_config.get("SecurityOpt") or [])]
            tmpfs = host_config.get("Tmpfs") or {}
            user = str(config.get("User") or "")
            hardened = (
                readonly_rootfs
                and "ALL" in cap_drop
                and "no-new-privileges:true" in security_opt
                and self._tmpfs_non_empty(tmpfs)
                and user in self._ALLOWED_USERS
            )
            if not hardened:
                non_hardened.append(
                    {
                        "node_id": node_id,
                        "container_name": str(container.get("container_name") or ""),
                        "readonly_rootfs": readonly_rootfs,
                        "cap_drop": cap_drop,
                        "security_opt": security_opt,
                        "tmpfs": sorted(str(key) for key in tmpfs.keys()) if isinstance(tmpfs, dict) else list(tmpfs or []),
                        "user": user,
                    }
                )

        missing_expected_node_ids = [node_id for node_id in expected_node_ids if node_id not in set(observed_node_ids)]
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and len(containers) >= minimum_container_count
            and not missing_artifacts
            and not missing_expected_node_ids
            and not non_hardened
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected captured docker inspect artifacts to prove runtime hardening on every required container"
        return {
            "primitive": "container_runtime_hardening_observed",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "container_count": len(containers),
                "minimum_container_count": minimum_container_count,
                "observed_node_ids": observed_node_ids,
                "missing_expected_node_ids": missing_expected_node_ids,
                "missing_artifacts": missing_artifacts,
                "non_hardened_container_count": len(non_hardened),
                "non_hardened": non_hardened,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class PanicPathContained(AssertionPrimitive):
    """Pass iff crash-triggering input stays on a contained error path and the node remains up."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_panic_path_probe")
        result_body = payload.get("result") or {}
        panic_avoided = bool(result_body.get("panic_avoided", False))
        node_stayed_up = bool(result_body.get("node_stayed_up", False))
        enough = latest is not None and payload.get("outcome") == "ok" and panic_avoided and node_stayed_up
        result = "pass" if enough else "fail"
        note = None if enough else "expected crash-triggering input to remain on a contained non-panic path"
        return {
            "primitive": "panic_path_contained",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "panic_avoided": panic_avoided,
                "node_stayed_up": node_stayed_up,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class OverlaySlotForgingRejected(AssertionPrimitive):
    """Pass iff TPraos overlay-slot forging attempts are rejected without adoption."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_overlay_slot_forging")
        result_body = payload.get("result") or {}
        forgery_rejected = bool(result_body.get("forgery_rejected", False))
        forged_block_adopted = bool(result_body.get("forged_block_adopted", True))
        enough = latest is not None and payload.get("outcome") == "ok" and forgery_rejected and not forged_block_adopted
        result = "pass" if enough else "fail"
        note = None if enough else "expected overlay-slot forging attempt to be rejected without adoption"
        return {
            "primitive": "overlay_slot_forging_rejected",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "forgery_rejected": forgery_rejected,
                "forged_block_adopted": forged_block_adopted,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


def _latest_completed_payload(handle, *, phase: str, primitive: str) -> tuple[dict | None, dict]:
    completed = _events_from_handle(handle, phase=phase, event="completed", primitive=primitive)
    latest = completed[-1] if completed else None
    payload = (latest or {}).get("payload") or {}
    return latest, payload


class KBoundRollbackRecovered(AssertionPrimitive):
    """Pass iff a requested rollback stayed within k and the post-rollback state re-converged."""

    def evaluate(self, handle):
        rollback_latest, rollback_payload = _latest_completed_payload(
            handle, phase="load", primitive="runtime_force_rollback"
        )
        _, _, result_body, summary = _latest_multi_node_observation(handle)
        rollback_result = rollback_payload.get("result") or {}
        requested_slots = int(rollback_result.get("requested_rollback_slots", rollback_payload.get("requested_rollback_slots", 0)) or 0)
        security_parameter_k = int(rollback_result.get("security_parameter_k", rollback_payload.get("security_parameter_k", 0)) or 0)
        rollback_status = str(rollback_result.get("rollback_status", rollback_payload.get("rollback_status", "")) or "")
        ledger_state_consistent = bool(
            rollback_result.get(
                "ledger_state_consistent_post_rollback",
                rollback_payload.get("ledger_state_consistent_post_rollback", False),
            )
        )
        observed_peer_edge_count = int(summary.get("observed_peer_edge_count", 0) or 0)
        slot_transition_nodes = _tip_state_slot_transition_count(result_body)
        enough = (
            rollback_latest is not None
            and rollback_payload.get("outcome") == "ok"
            and requested_slots > 0
            and security_parameter_k >= 1
            and requested_slots <= security_parameter_k
            and rollback_status in {"applied", "succeeded", "recovered"}
            and ledger_state_consistent
            and observed_peer_edge_count >= 2
            and slot_transition_nodes >= 1
            # Recovery scenarios claim end-of-window convergence after the within-k rollback completes.
            and bool(summary.get("chain_select_consistent"))
        )
        result = "pass" if enough else "fail"
        note = (
            None
            if enough
            else "expected a within-k rollback to recover consistently with non-trivial connectivity and observed slot transitions"
        )
        return {
            "primitive": "k_bound_rollback_recovered",
            "params": dict(self.params),
            "evaluated_value": {
                "rollback_seen": rollback_latest is not None,
                "requested_rollback_slots": requested_slots,
                "security_parameter_k": security_parameter_k,
                "rollback_status": rollback_status,
                "ledger_state_consistent_post_rollback": ledger_state_consistent,
                "observed_peer_edge_count": observed_peer_edge_count,
                "slot_transition_nodes": slot_transition_nodes,
                "chain_select_consistent": summary.get("chain_select_consistent"),
            },
            "data_points_used": [rollback_payload] if rollback_latest is not None else [],
            "result": result,
            "note": note,
        }


class ExceededRollbackRejected(AssertionPrimitive):
    """Pass iff a rollback request beyond k was rejected safely."""

    def evaluate(self, handle):
        rollback_latest, rollback_payload = _latest_completed_payload(
            handle, phase="load", primitive="runtime_force_rollback"
        )
        rollback_result = rollback_payload.get("result") or {}
        requested_slots = int(rollback_result.get("requested_rollback_slots", rollback_payload.get("requested_rollback_slots", 0)) or 0)
        security_parameter_k = int(rollback_result.get("security_parameter_k", rollback_payload.get("security_parameter_k", 0)) or 0)
        rollback_status = str(rollback_result.get("rollback_status", rollback_payload.get("rollback_status", "")) or "")
        rejection_reason = str(rollback_result.get("rejection_reason", rollback_payload.get("rejection_reason", "")) or "")
        enough = (
            rollback_latest is not None
            and rollback_payload.get("outcome") == "ok"
            and security_parameter_k >= 1
            and requested_slots > security_parameter_k
            and rollback_status in {"rejected", "failed-safely"}
            and bool(rejection_reason)
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected a beyond-k rollback to be rejected safely"
        return {
            "primitive": "exceeded_rollback_rejected",
            "params": dict(self.params),
            "evaluated_value": {
                "rollback_seen": rollback_latest is not None,
                "requested_rollback_slots": requested_slots,
                "security_parameter_k": security_parameter_k,
                "rollback_status": rollback_status,
                "rejection_reason": rejection_reason,
            },
            "data_points_used": [rollback_payload] if rollback_latest is not None else [],
            "result": result,
            "note": note,
        }


class ChainSwitchConsistent(AssertionPrimitive):
    """Pass iff a chain-switch event completes and observers converge to the injected target tip."""

    def evaluate(self, handle):
        switch_latest, switch_payload = _latest_completed_payload(
            handle, phase="load", primitive="runtime_chain_switch_inject"
        )
        _, payload, result_body, summary = _latest_multi_node_observation(handle)
        latest_tips = _multi_node_latest_tips(result_body, summary)
        observed_peer_edge_count = int(summary.get("observed_peer_edge_count", 0) or 0)
        slot_transition_nodes = _tip_state_slot_transition_count(result_body)
        switch_result = switch_payload.get("result") or {}
        target_hash = str(switch_result.get("target_tip_hash", switch_payload.get("target_tip_hash", "")) or "")
        target_slot = switch_result.get("target_tip_slot", switch_payload.get("target_tip_slot"))
        target_slot = int(target_slot) if target_slot is not None else None
        honest_node_ids = [str(node_id) for node_id in self.params.get("honest_node_ids", [])] or sorted(latest_tips.keys())
        matching_honest_nodes = sorted(
            node_id
            for node_id in honest_node_ids
            if (latest_tips.get(node_id) or {}).get("hash") == target_hash
            and (target_slot is None or int((latest_tips.get(node_id) or {}).get("slot", -1)) == target_slot)
        )
        enough = (
            switch_latest is not None
            and switch_payload.get("outcome") == "ok"
            and payload.get("outcome") == "ok"
            # Chain-switch scenarios claim the latest honest tips reached the injected target, not merely a shared historical tip.
            and bool(summary.get("chain_select_consistent"))
            and observed_peer_edge_count >= 2
            and slot_transition_nodes >= 1
            and bool(target_hash)
            and len(matching_honest_nodes) == len(honest_node_ids)
        )
        result = "pass" if enough else "fail"
        note = (
            None
            if enough
            else "expected all observed honest nodes to reach the injected chain-switch tip with non-trivial connectivity and observed slot transitions"
        )
        return {
            "primitive": "chain_switch_consistent",
            "params": dict(self.params),
            "evaluated_value": {
                "chain_switch_seen": switch_latest is not None,
                "target_tip_hash": target_hash,
                "target_tip_slot": target_slot,
                "matching_honest_node_count": len(matching_honest_nodes),
                "honest_node_count": len(honest_node_ids),
                "observed_peer_edge_count": observed_peer_edge_count,
                "slot_transition_nodes": slot_transition_nodes,
                "chain_select_consistent": summary.get("chain_select_consistent"),
            },
            "data_points_used": [switch_payload] if switch_latest is not None else [],
            "result": result,
            "note": note,
        }


class ReconnectionClean(AssertionPrimitive):
    """Pass iff a restarted or reconnected node rejoins and reaches the honest quorum tip."""

    def evaluate(self, handle):
        restart_latest, restart_payload = _latest_completed_payload(
            handle, phase="load", primitive="runtime_restart_node"
        )
        _, payload, result_body, summary = _latest_multi_node_observation(handle)
        latest_tips = _multi_node_latest_tips(result_body, summary)
        connectivity = _multi_node_connectivity_map(result_body, summary)
        observed_peer_edge_count = int(summary.get("observed_peer_edge_count", 0) or 0)
        reconnected_node_id = str(
            self.params.get("reconnected_node_id")
            or (restart_payload.get("result") or {}).get("target_node")
            or restart_payload.get("target_node")
            or ""
        )
        honest_node_ids = [str(node_id) for node_id in self.params.get("honest_node_ids", [])]
        groups = _honest_tip_groups(latest_tips=latest_tips, honest_node_ids=honest_node_ids)
        quorum_key = next(iter(groups.most_common(1)), None)
        quorum_hash = quorum_key[0][0] if quorum_key else None
        quorum_slot = quorum_key[0][1] if quorum_key else None
        reconnect_tip = latest_tips.get(reconnected_node_id) or {}
        reconnect_peers = set(connectivity.get(reconnected_node_id, []))
        reconnected_body = ((result_body.get("per_node") or {}).get(reconnected_node_id) or {})
        reconnect_connection_state = (reconnected_body.get("connection_state") or {})
        reconnect_success_count = int(reconnect_connection_state.get("connect_successes", 0) or 0)
        reconnect_tip_samples = (((reconnected_body.get("tip_state") or {}).get("samples")) or [])
        reconnect_real_sample_count = 0
        for sample in reconnect_tip_samples:
            if _tip_has_real_chain_progress(sample or {}):
                reconnect_real_sample_count += 1
        enough = (
            restart_latest is not None
            and restart_payload.get("outcome") == "ok"
            and payload.get("outcome") == "ok"
            and bool(reconnected_node_id)
            and bool(summary.get("responsive_node_count", 0))
            and observed_peer_edge_count >= 2
            and reconnect_success_count > 0
            and reconnect_real_sample_count > 0
            and reconnect_tip.get("hash") == quorum_hash
            and (quorum_slot is None or int(reconnect_tip.get("slot", -1)) == int(quorum_slot))
            and set(honest_node_ids).issubset(reconnect_peers)
        )
        result = "pass" if enough else "fail"
        note = (
            None
            if enough
            else "expected the reconnected node to rejoin and match the honest quorum tip with non-trivial connectivity and real reconnect telemetry"
        )
        return {
            "primitive": "reconnection_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "restart_seen": restart_latest is not None,
                "reconnected_node_id": reconnected_node_id,
                "quorum_hash": quorum_hash,
                "quorum_slot": quorum_slot,
                "reconnected_tip": reconnect_tip,
                "reconnected_peer_count": len(reconnect_peers),
                "observed_peer_edge_count": observed_peer_edge_count,
                "reconnect_success_count": reconnect_success_count,
                "reconnect_real_sample_count": reconnect_real_sample_count,
            },
            "data_points_used": [restart_payload] if restart_latest is not None else [],
            "result": result,
            "note": note,
        }


class SnapshotCapturedClean(AssertionPrimitive):
    """Pass iff runtime_snapshot_capture emitted a non-empty deterministic snapshot bundle."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_snapshot_capture")
        result_body = payload.get("result") or {}
        artifact_summary = payload.get("artifact_summary") or {}
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and payload.get("exit_code") == 0
            and artifact_summary.get("has_snapshot_tar", False)
            and artifact_summary.get("has_snapshot_manifest", False)
            and int(result_body.get("snapshot_size", 0) or 0) > 0
            and int(result_body.get("snapshot_entry_count", 0) or 0) >= 1
            and bool(result_body.get("snapshot_sha256"))
            and bool(result_body.get("node_healthy_after_restart", False))
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected runtime_snapshot_capture to emit a non-empty snapshot and restart the node cleanly"
        return {
            "primitive": "snapshot_captured_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "captured": latest is not None,
                "snapshot_size": int(result_body.get("snapshot_size", 0) or 0),
                "snapshot_entry_count": int(result_body.get("snapshot_entry_count", 0) or 0),
                "node_healthy_after_restart": bool(result_body.get("node_healthy_after_restart", False)),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class SnapshotCorruptionDetected(AssertionPrimitive):
    """Pass iff runtime_snapshot_corrupt changed the snapshot digest as requested."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_snapshot_corrupt")
        result_body = payload.get("result") or {}
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and payload.get("exit_code") == 0
            and bool(result_body.get("corruption_detected", False))
            and bool(result_body.get("corrupted_snapshot_path"))
            and str(result_body.get("original_snapshot_sha256") or "") != str(result_body.get("corrupted_snapshot_sha256") or "")
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected runtime_snapshot_corrupt to emit a changed snapshot digest"
        return {
            "primitive": "snapshot_corruption_detected",
            "params": dict(self.params),
            "evaluated_value": {
                "corruption_seen": latest is not None,
                "corruption_mode": result_body.get("corruption_mode"),
                "corruption_detected": bool(result_body.get("corruption_detected", False)),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class SnapshotRestoreSucceeded(AssertionPrimitive):
    """Pass iff runtime_snapshot_restore restarted the target node and health recovered."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_snapshot_restore")
        result_body = payload.get("result") or {}
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and payload.get("exit_code") == 0
            and bool(result_body.get("restore_succeeded", False))
            and bool(result_body.get("node_healthy_after_restore", False))
            and len(list(result_body.get("restored_paths") or [])) >= 1
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected runtime_snapshot_restore to restart the target node and recover health"
        return {
            "primitive": "snapshot_restore_succeeded",
            "params": dict(self.params),
            "evaluated_value": {
                "restore_seen": latest is not None,
                "restore_succeeded": bool(result_body.get("restore_succeeded", False)),
                "node_healthy_after_restore": bool(result_body.get("node_healthy_after_restore", False)),
                "restored_path_count": len(list(result_body.get("restored_paths") or [])),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class SubstrateCheckpointRecordedClean(AssertionPrimitive):
    """Pass iff runtime_substrate_checkpoint emitted a non-empty checkpoint and restarted the substrate cleanly."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_substrate_checkpoint")
        result_body = payload.get("result") or {}
        artifact_summary = payload.get("artifact_summary") or {}
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and payload.get("exit_code") == 0
            and artifact_summary.get("has_checkpoint_tar", False)
            and artifact_summary.get("has_checkpoint_manifest", False)
            and int(result_body.get("checkpoint_size", 0) or 0) > 0
            and int(result_body.get("checkpoint_entry_count", 0) or 0) >= 1
            and bool(result_body.get("checkpoint_sha256"))
            and bool(result_body.get("nodes_healthy_after_checkpoint", False))
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected runtime_substrate_checkpoint to emit a non-empty checkpoint and restart the substrate cleanly"
        return {
            "primitive": "substrate_checkpoint_recorded_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "checkpoint_recorded": latest is not None,
                "checkpoint_size": int(result_body.get("checkpoint_size", 0) or 0),
                "checkpoint_entry_count": int(result_body.get("checkpoint_entry_count", 0) or 0),
                "nodes_healthy_after_checkpoint": bool(result_body.get("nodes_healthy_after_checkpoint", False)),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class SubstrateResumeSucceeded(AssertionPrimitive):
    """Pass iff runtime_substrate_resume restored the checkpoint and the substrate recovered health."""

    def evaluate(self, handle):
        latest, payload = _latest_completed_payload(handle, phase="load", primitive="runtime_substrate_resume")
        result_body = payload.get("result") or {}
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and payload.get("exit_code") == 0
            and bool(result_body.get("resume_succeeded", False))
            and bool(result_body.get("nodes_healthy_after_resume", False))
            and len(list(result_body.get("restored_paths") or [])) >= 1
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected runtime_substrate_resume to restore the checkpoint and recover substrate health"
        return {
            "primitive": "substrate_resume_succeeded",
            "params": dict(self.params),
            "evaluated_value": {
                "resume_seen": latest is not None,
                "resume_succeeded": bool(result_body.get("resume_succeeded", False)),
                "nodes_healthy_after_resume": bool(result_body.get("nodes_healthy_after_resume", False)),
                "restored_path_count": len(list(result_body.get("restored_paths") or [])),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class HfBoundaryRuleConsistent(AssertionPrimitive):
    """Pass iff all observed nodes report the same protocol version application at the HF boundary."""

    def evaluate(self, handle):
        latest, payload, result_body, summary = _latest_multi_node_observation(handle)
        hf_boundary = summary.get("hf_boundary") or result_body.get("hf_boundary") or {}
        node_versions = hf_boundary.get("node_protocol_versions") or {}
        distinct_versions = sorted({str(value) for value in node_versions.values() if value is not None})
        target_tx_id = hf_boundary.get("target_tx_id")
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and bool(target_tx_id)
            and len(distinct_versions) == 1
            and len(node_versions) >= 1
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected all observed nodes to apply one protocol version at the HF boundary"
        return {
            "primitive": "hf_boundary_rule_consistent",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "target_tx_id": target_tx_id,
                "node_count": len(node_versions),
                "distinct_protocol_versions": distinct_versions,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class TransitionWindowValidated(AssertionPrimitive):
    """Pass iff pre/post-HF submissions were validated under the expected rule windows."""

    def evaluate(self, handle):
        latest, payload, result_body, summary = _latest_multi_node_observation(handle)
        window = summary.get("transition_window") or result_body.get("transition_window") or {}
        pre_hf = window.get("pre_hf_validation") or {}
        post_hf = window.get("post_hf_validation") or {}
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and bool(pre_hf.get("rules_expected"))
            and bool(post_hf.get("rules_expected"))
            and pre_hf.get("rules_expected") == pre_hf.get("rules_observed")
            and post_hf.get("rules_expected") == post_hf.get("rules_observed")
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected pre/post-HF submissions to validate under the matching rule windows"
        return {
            "primitive": "transition_window_validated",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "pre_hf_expected": pre_hf.get("rules_expected"),
                "pre_hf_observed": pre_hf.get("rules_observed"),
                "post_hf_expected": post_hf.get("rules_expected"),
                "post_hf_observed": post_hf.get("rules_observed"),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class ModeSwitchGenesisObserved(AssertionPrimitive):
    """Pass iff a genesis-mode node transitions cleanly without peer-set capture."""

    def evaluate(self, handle):
        latest, payload, result_body, summary = _latest_multi_node_observation(handle)
        mode_switch = summary.get("genesis_mode") or result_body.get("genesis_mode") or {}
        mode_path = mode_switch.get("mode_path") or []
        peer_capture = bool(mode_switch.get("peer_set_capture_detected", False))
        final_mode = mode_switch.get("final_mode")
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_observation_summary", False)
            and "sync" in mode_path
            and "caught-up" in mode_path
            and final_mode == "caught-up"
            and not peer_capture
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected genesis-mode transition to reach caught-up mode without peer-set capture"
        return {
            "primitive": "mode_switch_genesis_observed",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "mode_path": mode_path,
                "final_mode": final_mode,
                "peer_set_capture_detected": peer_capture,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class EraSummaryRecordedClean(AssertionPrimitive):
    """Pass iff LSQ era-summary capture emitted and matched the expected era label/value."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_cardano_lsq_extract",
        )
        latest = completed[-1] if completed else None
        payload = (latest or {}).get("payload") or {}
        result_body = payload.get("result") or {}
        expected_era = self.params.get("expected_era")
        observed_era = result_body.get("era")
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and payload.get("exit_code") == 0
            and result_body.get("snapshot_size", 0) > 0
            and (expected_era is None or str(observed_era) == str(expected_era))
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected LSQ era-summary capture to record the requested era cleanly"
        return {
            "primitive": "era_summary_recorded_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "observed_era": observed_era,
                "expected_era": expected_era,
                "snapshot_size": result_body.get("snapshot_size"),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class BundleTagRecordedClean(AssertionPrimitive):
    """Pass iff bundle tag emitted tags.json with at least one tag."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_bundle_tag",
        )
        latest = completed[-1] if completed else None
        payload = (latest or {}).get("payload") or {}
        artifact_summary = payload.get("artifact_summary") or {}
        result_body = payload.get("result") or {}
        tags_added = result_body.get("tags_added", payload.get("tags_added", [])) or []
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and payload.get("helper_exit_code") == 0
            and artifact_summary.get("has_tags_json", False)
            and len(tags_added) >= 1
        )
        verdict = "pass" if enough else "fail"
        note = None if enough else "expected runtime_bundle_tag to emit tags.json with at least one tag"
        return {
            "primitive": "bundle_tag_recorded_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "outcome": payload.get("outcome"),
                "tags_count": len(tags_added),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": verdict,
            "note": note,
        }


class ForensicSnapshotEmittedClean(AssertionPrimitive):
    """Pass iff forensic snapshot emitted tarball, manifest, README, and included bundles."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_forensic_snapshot",
        )
        latest = completed[-1] if completed else None
        payload = (latest or {}).get("payload") or {}
        artifact_summary = payload.get("artifact_summary") or {}
        result_body = payload.get("result") or {}
        included_bundle_count = int(result_body.get("included_bundle_count", payload.get("included_bundle_count", 0)) or 0)
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and payload.get("helper_exit_code") == 0
            and artifact_summary.get("has_snapshot_tarball", False)
            and artifact_summary.get("has_snapshot_manifest", False)
            and artifact_summary.get("has_snapshot_readme", False)
            and included_bundle_count >= 1
        )
        verdict = "pass" if enough else "fail"
        note = None if enough else "expected runtime_forensic_snapshot to emit a non-empty snapshot bundle"
        return {
            "primitive": "forensic_snapshot_emitted_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "outcome": payload.get("outcome"),
                "included_bundle_count": included_bundle_count,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": verdict,
            "note": note,
        }


class BundleSummaryComposeCompletedClean(AssertionPrimitive):
    """Pass iff bundle summary emitted json/html/markdown with at least one bundle row."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_bundle_summary_compose",
        )
        latest = completed[-1] if completed else None
        payload = (latest or {}).get("payload") or {}
        artifact_summary = payload.get("artifact_summary") or {}
        result_body = payload.get("result") or {}
        bundle_count = int((result_body.get("summary") or {}).get("total_bundle_count", payload.get("bundle_count", 0)) or 0)
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and payload.get("helper_exit_code") == 0
            and artifact_summary.get("has_summary_json", False)
            and artifact_summary.get("has_summary_md", False)
            and artifact_summary.get("has_summary_html", False)
            and bundle_count >= 1
        )
        verdict = "pass" if enough else "fail"
        note = None if enough else "expected runtime_bundle_summary_compose to emit a non-empty summary bundle"
        return {
            "primitive": "bundle_summary_compose_completed_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "outcome": payload.get("outcome"),
                "bundle_count": bundle_count,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": verdict,
            "note": note,
        }


class BundleTimelineEmittedClean(AssertionPrimitive):
    """Pass iff bundle timeline emitted json/markdown and at least one signature record."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_bundle_timeline",
        )
        latest = completed[-1] if completed else None
        payload = (latest or {}).get("payload") or {}
        artifact_summary = payload.get("artifact_summary") or {}
        result = payload.get("result") or {}
        enough = (
            latest is not None
            and payload.get("exit_code") == 0
            and payload.get("outcome") == "ok"
            and artifact_summary.get("has_timeline_json", False)
            and artifact_summary.get("has_timeline_markdown", False)
            and int(result.get("signature_count", 0)) >= 1
        )
        verdict = "pass" if enough else "fail"
        note = None if enough else "expected runtime_bundle_timeline to emit at least one signature record"
        return {
            "primitive": "bundle_timeline_emitted_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "outcome": payload.get("outcome"),
                "signature_count": result.get("signature_count"),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": verdict,
            "note": note,
        }


class BundleAttestationSignatureValid(AssertionPrimitive):
    """Pass iff bundle attestation emitted and its embedded signature verified."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_bundle_attestation",
        )
        latest = completed[-1] if completed else None
        payload = (latest or {}).get("payload") or {}
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_attestation", False)
            and payload.get("verification_verdict") == "verified"
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected a verified runtime_bundle_attestation result"
        return {
            "primitive": "bundle_attestation_signature_valid",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "outcome": payload.get("outcome"),
                "verification_verdict": payload.get("verification_verdict"),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class BundleChainVerifyClean(AssertionPrimitive):
    """Pass iff chain verify emitted a report and every step verified."""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive="runtime_bundle_chain_verify",
        )
        latest = completed[-1] if completed else None
        payload = (latest or {}).get("payload") or {}
        result_body = payload.get("result") or {}
        chain_verdict = result_body.get("chain_verdict", payload.get("chain_verdict"))
        chain_length = result_body.get("chain_length", payload.get("chain_length", 0))
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_chain_verify_report", False)
            and chain_verdict == "all-verified"
            and int(chain_length or 0) >= 1
        )
        result = "pass" if enough else "fail"
        note = None if enough else "expected runtime_bundle_chain_verify to report an all-verified chain"
        return {
            "primitive": "bundle_chain_verify_clean",
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "outcome": payload.get("outcome"),
                "chain_verdict": chain_verdict,
                "chain_length": chain_length,
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class _StaticAnalysisCompletedCleanBase(AssertionPrimitive):
    _PRIMITIVE = ""
    _ASSERTION = ""

    def evaluate(self, handle):
        completed = _events_from_handle(
            handle,
            phase="load",
            event="completed",
            primitive=self._PRIMITIVE,
        )
        latest = completed[-1] if completed else None
        payload = (latest or {}).get("payload") or {}
        tool_status = payload.get("tool_status")
        enough = (
            latest is not None
            and payload.get("outcome") == "ok"
            and (payload.get("artifact_summary") or {}).get("has_findings", False)
            and tool_status in {"clean", "findings"}
        )
        result = "pass" if enough else "fail"
        note = None if enough else f"expected {self._PRIMITIVE} to complete with clean or findings status"
        return {
            "primitive": self._ASSERTION,
            "params": dict(self.params),
            "evaluated_value": {
                "completed": 1 if latest is not None else 0,
                "outcome": payload.get("outcome"),
                "tool_status": tool_status,
                "tool_exit_code": payload.get("tool_exit_code"),
                "findings_count": payload.get("findings_count"),
            },
            "data_points_used": [payload] if latest is not None else [],
            "result": result,
            "note": note,
        }


class ClippyStaticAnalysisCompletedClean(_StaticAnalysisCompletedCleanBase):
    _PRIMITIVE = "runtime_static_analysis_clippy"
    _ASSERTION = "clippy_static_analysis_completed_clean"


class AuditStaticAnalysisCompletedClean(_StaticAnalysisCompletedCleanBase):
    _PRIMITIVE = "runtime_static_analysis_audit"
    _ASSERTION = "audit_static_analysis_completed_clean"


class DenyStaticAnalysisCompletedClean(_StaticAnalysisCompletedCleanBase):
    _PRIMITIVE = "runtime_static_analysis_deny"
    _ASSERTION = "deny_static_analysis_completed_clean"


class StateMachineTraceValid(AssertionPrimitive):
    """Pass iff every logged state-machine transition is valid and expected."""

    def evaluate(self, handle):
        transitions = _events_from_handle(
            handle, phase="load", event="transition", primitive="mini_protocol_state_machine"
        )
        min_transitions = int(self.params.get("min_transitions", 1))
        invalid = []
        for entry in transitions:
            payload = entry.get("payload") or {}
            if not payload.get("transition_ok", False):
                invalid.append(payload)
        enough = len(transitions) >= min_transitions
        result = "pass" if enough and not invalid else "fail"
        note = None if enough else f"expected at least {min_transitions} state-machine transitions"
        return {
            "primitive": "state_machine_trace_valid",
            "params": dict(self.params),
            "evaluated_value": {
                "transitions": len(transitions),
                "invalid": len(invalid),
                "min_transitions": min_transitions,
            },
            "data_points_used": [
                {
                    "sequence_id": payload.get("sequence_id"),
                    "transition_index": payload.get("transition_index"),
                    "from_state": payload.get("from_state"),
                    "to_state": payload.get("to_state"),
                    "expected_outcome": payload.get("expected_outcome"),
                    "outcome": payload.get("outcome"),
                }
                for payload in invalid
            ],
            "result": result,
            "note": note,
        }


class RoundtripEqualsOriginal(AssertionPrimitive):
    """Pass iff for every 'ok' outcome the re-encoded bytes equal the input bytes.

    Skips outcomes that didn't parse cleanly (clean_error / crash); requires at
    least a configured minimum number of successfully parsed inputs.
    """

    def evaluate_outcomes(self, outcomes):
        parsed = [e for e in outcomes if e.get("outcome") == "ok"]
        min_inputs_parsed = int(self.params.get("min_inputs_parsed", 1))
        if not parsed:
            return {
                "primitive": "roundtrip_equals_original",
                "params": dict(self.params),
                "evaluated_value": {
                    "ok": 0,
                    "matched": 0,
                    "mismatched": 0,
                    "min_inputs_parsed": min_inputs_parsed,
                },
                "data_points_used": [],
                "result": "fail",
                "note": f"expected at least {min_inputs_parsed} parsed inputs to compare",
            }
        mismatches = [
            e for e in parsed
            if e.get("input_hex") != e.get("reencoded_hex")
        ]
        enough = len(parsed) >= min_inputs_parsed
        return {
            "primitive": "roundtrip_equals_original",
            "params": dict(self.params),
            "evaluated_value": {
                "ok": len(parsed),
                "matched": len(parsed) - len(mismatches),
                "mismatched": len(mismatches),
                "min_inputs_parsed": min_inputs_parsed,
            },
            "data_points_used": [
                {"input_hex": e.get("input_hex"), "reencoded_hex": e.get("reencoded_hex"), "input_id": e.get("input_id")}
                for e in mismatches
            ],
            "result": "pass" if enough and not mismatches else "fail",
            "note": None if enough else f"expected at least {min_inputs_parsed} parsed inputs to compare",
        }


# ---------------------------------------------------------------------------
# Single-node runtime primitives
# ---------------------------------------------------------------------------


@dataclass
class _NodeProcessState:
    pid: Optional[int]
    proc: Any
    binary: str


# Module-level shared state for node processes started inside one scenario run.
# Set by the runner per-run; primitives read/write via `bound_state`.
class _NodeStateContainer:
    def __init__(self):
        self.state = {}

    def __contains__(self, key):
        return key in self.state

    def __getitem__(self, key):
        return self.state[key]

    def __setitem__(self, key, value):
        self.state[key] = value

    def get(self, key, default=None):
        return self.state.get(key, default)


class StartNodeProcess(LoadPrimitive):
    """Launch a node binary as a subprocess and record its pid in shared state.

    Parameters:
      binary       — path to the node executable
      args         — optional list of extra CLI args
      env          — optional dict of env vars
      process_key  — key under which to store the process state (default: 'node')
    """

    state = _NodeStateContainer()  # default shared container; runner injects per-run

    def run(self, handle, rng):
        import subprocess as _sub
        binary = self.params["binary"]
        args = list(self.params.get("args", []))
        env_overrides = self.params.get("env") or {}
        process_key = self.params.get("process_key", "node")
        env = None
        if env_overrides:
            import os as _os
            env = _os.environ.copy()
            env.update({k: str(v) for k, v in env_overrides.items()})
        proc = _sub.Popen([binary, *args], env=env, stdout=_sub.DEVNULL, stderr=_sub.DEVNULL)
        self.state[process_key] = _NodeProcessState(pid=proc.pid, proc=proc, binary=binary)
        handle.log(phase="setup", primitive="start_node_process", level="info", event="started",
                   payload={"binary": binary, "pid": proc.pid, "process_key": process_key})


class StopNodeProcess(LoadPrimitive):
    """Terminate a previously-started node process and reap it."""

    bound_state = None  # injected by runner; falls back to StartNodeProcess.state

    def run(self, handle, rng):
        process_key = self.params.get("process_key", "node")
        state = self.bound_state if self.bound_state is not None else StartNodeProcess.state
        entry = state.get(process_key)
        if entry is None or entry.proc is None:
            handle.log(phase="teardown", primitive="stop_node_process", level="warn",
                       event="not_running", payload={"process_key": process_key})
            return
        proc = entry.proc
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        handle.log(phase="teardown", primitive="stop_node_process", level="info",
                   event="stopped", payload={"process_key": process_key, "pid": entry.pid,
                                             "exit_code": proc.returncode})


class ProcessRss(ProbePrimitive):
    """Sample resident-set-size for a node process started in this scenario."""

    bound_state = None

    def sample(self, handle):
        process_key = self.params.get("process_key", "node")
        state = self.bound_state if self.bound_state is not None else StartNodeProcess.state
        entry = state.get(process_key)
        if entry is None or entry.pid is None:
            handle.probe_sample("process_rss", value=None,
                                meta={"process_key": process_key, "reason": "not_running"})
            return
        from profile_manager.forensic import _ps_rss_bytes
        rss = _ps_rss_bytes(entry.pid)
        handle.probe_sample("process_rss", value=rss,
                            meta={"process_key": process_key, "pid": entry.pid})


# ---------------------------------------------------------------------------
# Devnet runtime primitives (wrap existing SSH-driven flow)
# ---------------------------------------------------------------------------


def _resolve_executor(params):
    """Return a callable (command, *, timeout, dry_run) -> CommandResult.

    Tests inject a stub via params['executor']; production wiring uses
    profile_manager.remote.ssh_command bound to the loaded config.
    """
    if "executor" in params and callable(params["executor"]):
        return params["executor"]
    from profile_manager.config import load_config
    from profile_manager.remote import ssh_command
    config = load_config()
    def _exec(command, *, timeout=None, dry_run=False):
        return ssh_command(config, command, timeout=timeout, dry_run=dry_run)
    return _exec


def _resolve_local_executor(params):
    if "executor" in params and callable(params["executor"]):
        return params["executor"]

    def _exec(command, *, timeout=None, dry_run=False):
        import subprocess
        rendered = command
        if dry_run:
            return type("R", (), {
                "returncode": 0, "stdout": "", "stderr": "", "rendered_command": rendered,
            })()
        proc = subprocess.run(
            ["/bin/sh", "-c", command],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return type("R", (), {
            "returncode": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", errors="replace"),
            "stderr": proc.stderr.decode("utf-8", errors="replace"),
            "rendered_command": rendered,
        })()

    return _exec


class DeployProfile(LoadPrimitive):
    """Setup primitive: deploy a devnet profile via SSH.

    Parameters: profile_id, [timeout_seconds=300], [dry_run=False], [executor=callable for tests]
    """

    def run(self, handle, rng):
        from profile_manager.profiles import find_profile, deploy_command
        profile_id = self.params["profile_id"]
        timeout = int(self.params.get("timeout_seconds", 300))
        dry_run = bool(self.params.get("dry_run", False))
        executor = _resolve_executor(self.params)
        profile = find_profile(profile_id)
        cmd = deploy_command(profile)
        result = executor(cmd, timeout=timeout, dry_run=dry_run)
        if result.returncode != 0:
            handle.log(phase="setup", primitive="deploy_profile", level="error",
                       event="failed", payload={"profile_id": profile_id, "exit": result.returncode,
                                                "stderr_head": (result.stderr or "")[:200]})
            raise RuntimeError(f"deploy_profile failed for {profile_id} (exit {result.returncode})")
        handle.log(phase="setup", primitive="deploy_profile", level="info",
                   event="deployed", payload={"profile_id": profile_id})


# ---------------------------------------------------------------------------
# Fault primitives (devnet runtime, dockerized)
# ---------------------------------------------------------------------------


class FaultPrimitive(Primitive):
    """A fault applies before the load phase and removes after.

    Subclasses implement apply(handle) and remove(handle). The runner calls
    apply for each fault (in declared order) before load, and remove in reverse
    order after load completes, so faults nest cleanly.
    """

    def apply(self, handle):
        raise NotImplementedError

    def remove(self, handle):
        raise NotImplementedError


class RuntimeByzantinePeer(FaultPrimitive):
    """Inject a mux-aware byzantine proxy in front of a composed substrate node."""

    bound_state = None

    def _run_mode(self, handle, *, mode: str, phase_event: str) -> None:
        state = self.bound_state or {}
        runtime_metadata_path = str(self.params.get("runtime_metadata_path") or state.get("substrate_runtime_metadata_path") or "")
        if not runtime_metadata_path:
            raise ValueError("runtime_byzantine_peer requires substrate runtime metadata")
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 240))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / f"runtime-byzantine-peer-{mode}.json"
        config_body = {
            "runtime_metadata_path": runtime_metadata_path,
            "output_dir": str(output_dir),
            "target_node_id": self.params["target_node_id"],
            "upstream_node_id": self.params.get("upstream_node_id"),
            "upstream_address": self.params.get("upstream_address"),
            "mutation_mode": self.params.get("mutation_mode", "flip_payload_byte"),
            "mutation_direction": self.params.get("mutation_direction", "outbound"),
            "mutation_protocol": self.params.get("mutation_protocol", "any"),
            "mutate_after_segments": int(self.params.get("mutate_after_segments", 1)),
            "healthy_timeout_seconds": float(self.params.get("healthy_timeout_seconds", 90)),
        }
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_byzantine_peer_command(config_path=config_path, mode=mode)
        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report_name = "apply-report.json" if mode == "apply" else "remove-report.json"
        report_path = output_dir / report_name
        report = {}
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="fault",
            primitive="runtime_byzantine_peer",
            level="info" if outcome == "ok" else "error",
            event=phase_event,
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {
                    "has_apply_report": (output_dir / "apply-report.json").is_file(),
                    "has_remove_report": (output_dir / "remove-report.json").is_file(),
                },
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )

    def apply(self, handle):
        self._run_mode(handle, mode="apply", phase_event="applied")

    def remove(self, handle):
        self._run_mode(handle, mode="remove", phase_event="removed")


class RuntimeByzantineCardanoNode(FaultPrimitive):
    """Inject a mux-aware handshake downgrade proxy in front of a cardano-node peer."""

    bound_state = None

    def _run_mode(self, handle, *, mode: str, phase_event: str) -> None:
        state = self.bound_state or {}
        runtime_metadata_path = str(self.params.get("runtime_metadata_path") or state.get("substrate_runtime_metadata_path") or "")
        if not runtime_metadata_path:
            raise ValueError("runtime_byzantine_cardano_node requires substrate runtime metadata")
        output_dir = _resolve_output_path(handle, self.params["output_dir"])
        timeout = float(self.params.get("timeout_seconds", 240))
        expect_exit = int(self.params.get("expect_exit", 0))
        output_dir.mkdir(parents=True, exist_ok=True)
        config_path = output_dir / f"runtime-byzantine-cardano-node-{mode}.json"
        config_body = {
            "runtime_metadata_path": runtime_metadata_path,
            "output_dir": str(output_dir),
            "target_node_id": self.params["target_node_id"],
            "upstream_node_id": self.params.get("upstream_node_id"),
            "upstream_address": self.params.get("upstream_address"),
            "behavior": self.params.get("behavior", "handshake_version_downgrade_attempt"),
            "mutation_mode": self.params.get("mutation_mode", "flip_payload_byte"),
            "mutation_direction": self.params.get("mutation_direction", "outbound"),
            "mutation_protocol": int(self.params.get("mutation_protocol", 0)),
            "mutate_after_segments": int(self.params.get("mutate_after_segments", 1)),
            "healthy_timeout_seconds": float(self.params.get("healthy_timeout_seconds", 90)),
        }
        config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = build_runtime_byzantine_cardano_node_command(config_path=config_path, mode=mode)
        proc = subprocess.run(
            command,
            cwd=DWARF_ROOT,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_ensure_cargo_path(_build_dwarf_telemetry_env(handle)),
        )
        stdout = _decode_process_output(proc.stdout)
        stderr = _decode_process_output(proc.stderr)
        report_name = "apply-report.json" if mode == "apply" else "remove-report.json"
        report_path = output_dir / report_name
        report = {}
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        outcome = "ok" if proc.returncode == expect_exit else "unexpected_exit"
        handle.log(
            phase="fault",
            primitive="runtime_byzantine_cardano_node",
            level="info" if outcome == "ok" else "error",
            event=phase_event,
            payload={
                "exit_code": proc.returncode,
                "outcome": outcome,
                "artifact_summary": {
                    "has_apply_report": (output_dir / "apply-report.json").is_file(),
                    "has_remove_report": (output_dir / "remove-report.json").is_file(),
                },
                "report": report,
                "stdout": stdout[-4096:],
                "stderr": stderr[-2048:],
            },
        )

    def apply(self, handle):
        self._run_mode(handle, mode="apply", phase_event="applied")

    def remove(self, handle):
        self._run_mode(handle, mode="remove", phase_event="removed")


def _pumba_image(params):
    return params.get("pumba_image", "gaiaadm/pumba:latest")


class FaultDelay(FaultPrimitive):
    """Inject network latency on one container via a pumba netem sidecar.

    Parameters: target_container, delay_ms, duration_seconds, [jitter_ms=0],
                [interface=eth0], [pumba_image=gaiaadm/pumba:latest], [executor=]
    """

    def apply(self, handle):
        import shlex as _shlex
        target = self.params["target_container"]
        delay_ms = int(self.params.get("delay_ms", 100))
        jitter_ms = int(self.params.get("jitter_ms", 0))
        duration = int(self.params.get("duration_seconds", 30))
        interface = self.params.get("interface", "eth0")
        sidecar_name = f"dwarf-fault-delay-{target}"
        image = _pumba_image(self.params)
        cmd = (
            f"docker run -d --rm --name {_shlex.quote(sidecar_name)} "
            f"--pid=container:{_shlex.quote(target)} "
            f"--network=container:{_shlex.quote(target)} "
            f"--cap-add=NET_ADMIN {_shlex.quote(image)} "
            f"netem --duration {duration}s --tc-image {_shlex.quote(image)} "
            f"--interface {_shlex.quote(interface)} "
            f"delay --time {delay_ms} --jitter {jitter_ms} "
            f"{_shlex.quote(target)}"
        )
        executor = _resolve_executor(self.params)
        result = executor(cmd, timeout=60, dry_run=False)
        self._sidecar_name = sidecar_name
        handle.log(phase="fault", primitive="fault_delay", level="info",
                   event="applied",
                   payload={"target": target, "delay_ms": delay_ms, "jitter_ms": jitter_ms,
                            "duration_seconds": duration, "exit": result.returncode})

    def remove(self, handle):
        import shlex as _shlex
        name = getattr(self, "_sidecar_name", None)
        if name is None:
            return
        executor = _resolve_executor(self.params)
        cmd = f"docker rm -f {_shlex.quote(name)} 2>/dev/null || true"
        executor(cmd, timeout=30, dry_run=False)
        handle.log(phase="fault", primitive="fault_delay", level="info",
                   event="removed", payload={"sidecar": name})


class FaultDrop(FaultPrimitive):
    """Inject packet loss on one container via pumba netem loss.

    Parameters: target_container, loss_percent, duration_seconds,
                [correlation=0], [interface=eth0], [pumba_image=], [executor=]
    """

    def apply(self, handle):
        import shlex as _shlex
        target = self.params["target_container"]
        loss = int(self.params["loss_percent"])
        correlation = int(self.params.get("correlation", 0))
        duration = int(self.params.get("duration_seconds", 30))
        interface = self.params.get("interface", "eth0")
        sidecar_name = f"dwarf-fault-drop-{target}"
        image = _pumba_image(self.params)
        cmd = (
            f"docker run -d --rm --name {_shlex.quote(sidecar_name)} "
            f"--pid=container:{_shlex.quote(target)} "
            f"--network=container:{_shlex.quote(target)} "
            f"--cap-add=NET_ADMIN {_shlex.quote(image)} "
            f"netem --duration {duration}s --tc-image {_shlex.quote(image)} "
            f"--interface {_shlex.quote(interface)} "
            f"loss --percent {loss} --correlation {correlation} "
            f"{_shlex.quote(target)}"
        )
        executor = _resolve_executor(self.params)
        result = executor(cmd, timeout=60, dry_run=False)
        self._sidecar_name = sidecar_name
        handle.log(phase="fault", primitive="fault_drop", level="info",
                   event="applied",
                   payload={"target": target, "loss_percent": loss,
                            "duration_seconds": duration, "exit": result.returncode})

    def remove(self, handle):
        import shlex as _shlex
        name = getattr(self, "_sidecar_name", None)
        if name is None:
            return
        executor = _resolve_executor(self.params)
        executor(f"docker rm -f {_shlex.quote(name)} 2>/dev/null || true",
                 timeout=30, dry_run=False)


class FaultPartition(FaultPrimitive):
    """Disconnect one container from a docker network for the duration of load.

    Parameters: target_container, network, [executor=]
    """

    def apply(self, handle):
        import shlex as _shlex
        target = self.params["target_container"]
        network = self.params["network"]
        executor = _resolve_executor(self.params)
        cmd = f"docker network disconnect {_shlex.quote(network)} {_shlex.quote(target)}"
        result = executor(cmd, timeout=30, dry_run=False)
        self._active = result.returncode == 0
        handle.log(phase="fault", primitive="fault_partition", level="info",
                   event="applied",
                   payload={"target": target, "network": network, "exit": result.returncode})

    def remove(self, handle):
        import shlex as _shlex
        if not getattr(self, "_active", False):
            return
        target = self.params["target_container"]
        network = self.params["network"]
        executor = _resolve_executor(self.params)
        cmd = f"docker network connect {_shlex.quote(network)} {_shlex.quote(target)}"
        executor(cmd, timeout=30, dry_run=False)
        handle.log(phase="fault", primitive="fault_partition", level="info",
                   event="removed", payload={"target": target, "network": network})


class FaultLocalPortDrop(FaultPrimitive):
    """Drop host-local traffic to a specific TCP port with iptables for load duration.

    Parameters: target_port or target_port_file or (runtime_metadata_path + target_node),
                [host=127.0.0.1], [protocol=tcp], [drop_output=true],
                [drop_input=true], [executor=]
    """

    def _target_port(self):
        if "target_port" in self.params:
            return int(self.params["target_port"])
        if "target_port_file" in self.params:
            return int(Path(self.params["target_port_file"]).read_text(encoding="utf-8").strip())
        if "runtime_metadata_path" in self.params and "target_node" in self.params:
            metadata_path = Path(self.params["runtime_metadata_path"])
            if not metadata_path.exists():
                raise ValueError(f"fault_local_port_drop runtime metadata missing: {metadata_path}")
            body = json.loads(metadata_path.read_text(encoding="utf-8"))
            nodes = body.get("haskell_nodes") or body.get("amaru_nodes") or []
            target_node = str(self.params["target_node"])
            for node in nodes:
                if str(node.get("name")) == target_node:
                    try:
                        return int(node["port"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ValueError(
                            f"fault_local_port_drop node {target_node!r} in {metadata_path} lacks a valid port"
                        ) from exc
            raise ValueError(
                f"fault_local_port_drop could not find target_node {target_node!r} in {metadata_path}"
            )
        raise ValueError(
            "fault_local_port_drop requires target_port, target_port_file, or runtime_metadata_path + target_node"
        )

    def _rules(self):
        import shlex as _shlex
        host = self.params.get("host", "127.0.0.1")
        protocol = self.params.get("protocol", "tcp")
        port = self._target_port()
        rules = []
        if bool(self.params.get("drop_output", True)):
            rules.append(
                ("OUTPUT", f"-p {_shlex.quote(protocol)} -d {_shlex.quote(host)} --dport {port} -j DROP")
            )
        if bool(self.params.get("drop_input", True)):
            rules.append(
                ("INPUT", f"-p {_shlex.quote(protocol)} -s {_shlex.quote(host)} --sport {port} -j DROP")
            )
        if not rules:
            raise ValueError("fault_local_port_drop requires drop_output or drop_input")
        return port, host, protocol, rules

    def apply(self, handle):
        executor = _resolve_local_executor(self.params)
        port, host, protocol, rules = self._rules()
        applied = []
        for chain, spec in rules:
            cmd = f"sudo iptables -w -I {chain} {spec}"
            result = executor(cmd, timeout=30, dry_run=False)
            if result.returncode != 0:
                raise RuntimeError(
                    f"fault_local_port_drop apply failed for {chain} port {port} "
                    f"(exit {result.returncode})"
                )
            applied.append((chain, spec))
        self._rules_applied = applied
        payload = {"host": host, "port": port, "protocol": protocol, "chains": [chain for chain, _ in applied]}
        if "target_node" in self.params:
            payload["target_node"] = self.params["target_node"]
        if "runtime_metadata_path" in self.params:
            payload["runtime_metadata_path"] = self.params["runtime_metadata_path"]
        handle.log(
            phase="fault", primitive="fault_local_port_drop", level="info", event="applied",
            payload=payload,
        )

    def remove(self, handle):
        executor = _resolve_local_executor(self.params)
        applied = getattr(self, "_rules_applied", [])
        if not applied:
            return
        for chain, spec in reversed(applied):
            executor(f"sudo iptables -w -D {chain} {spec}", timeout=30, dry_run=False)
        handle.log(
            phase="fault", primitive="fault_local_port_drop", level="info", event="removed",
            payload={"chains": [chain for chain, _ in applied]},
        )


class FaultLocalPortDelay(FaultPrimitive):
    """Delay host-local traffic to a specific TCP port with tc netem on loopback.

    Parameters: target_port or target_port_file or (runtime_metadata_path + target_node),
                [host=127.0.0.1], [protocol=tcp], delay_ms, [jitter_ms=0]
    """

    def _target_port(self):
        if "target_port" in self.params:
            return int(self.params["target_port"])
        if "target_port_file" in self.params:
            return int(Path(self.params["target_port_file"]).read_text(encoding="utf-8").strip())
        if "runtime_metadata_path" in self.params and "target_node" in self.params:
            metadata_path = Path(self.params["runtime_metadata_path"])
            if not metadata_path.exists():
                raise ValueError(f"fault_local_port_delay runtime metadata missing: {metadata_path}")
            body = json.loads(metadata_path.read_text(encoding="utf-8"))
            nodes = body.get("haskell_nodes") or body.get("amaru_nodes") or []
            target_node = str(self.params["target_node"])
            for node in nodes:
                if str(node.get("name")) == target_node:
                    try:
                        return int(node["port"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ValueError(
                            f"fault_local_port_delay node {target_node!r} in {metadata_path} lacks a valid port"
                        ) from exc
            raise ValueError(
                f"fault_local_port_delay could not find target_node {target_node!r} in {metadata_path}"
            )
        raise ValueError(
            "fault_local_port_delay requires target_port, target_port_file, or runtime_metadata_path + target_node"
        )

    def _executor(self):
        return _resolve_local_executor(self.params)

    def apply(self, handle):
        port = self._target_port()
        host = self.params.get("host", "127.0.0.1")
        protocol = self.params.get("protocol", "tcp")
        delay_ms = int(self.params["delay_ms"])
        jitter_ms = int(self.params.get("jitter_ms", 0))
        executor = self._executor()

        check = executor("tc qdisc show dev lo", timeout=10, dry_run=False)
        if check.returncode != 0:
            raise RuntimeError("fault_local_port_delay could not inspect loopback qdisc")
        qdisc_text = check.stdout.strip()
        if qdisc_text and "noqueue" not in qdisc_text:
            raise RuntimeError(f"fault_local_port_delay expected noqueue root on lo, saw: {qdisc_text}")

        commands = [
            "sudo tc qdisc replace dev lo root handle 1: prio bands 3",
            f"sudo tc qdisc add dev lo parent 1:3 handle 30: netem delay {delay_ms}ms {jitter_ms}ms",
            (
                "sudo tc filter add dev lo protocol ip parent 1:0 prio 3 u32 "
                f"match ip dst {host}/32 match ip protocol 6 0xff match ip dport {port} 0xffff flowid 1:3"
            ),
            (
                "sudo tc filter add dev lo protocol ip parent 1:0 prio 3 u32 "
                f"match ip src {host}/32 match ip protocol 6 0xff match ip sport {port} 0xffff flowid 1:3"
            ),
        ]
        for cmd in commands:
            result = executor(cmd, timeout=30, dry_run=False)
            if result.returncode != 0:
                executor("sudo tc qdisc del dev lo root 2>/dev/null || true", timeout=10, dry_run=False)
                raise RuntimeError(
                    f"fault_local_port_delay apply failed for port {port} (exit {result.returncode})"
                )
        self._active = True
        handle.log(
            phase="fault", primitive="fault_local_port_delay", level="info", event="applied",
            payload={
                "host": host,
                "port": port,
                "protocol": protocol,
                "delay_ms": delay_ms,
                "jitter_ms": jitter_ms,
                **({"target_node": self.params["target_node"]} if "target_node" in self.params else {}),
                **({"runtime_metadata_path": self.params["runtime_metadata_path"]} if "runtime_metadata_path" in self.params else {}),
            },
        )

    def remove(self, handle):
        if not getattr(self, "_active", False):
            return
        executor = self._executor()
        executor("sudo tc qdisc del dev lo root 2>/dev/null || true", timeout=10, dry_run=False)
        handle.log(
            phase="fault", primitive="fault_local_port_delay", level="info", event="removed",
            payload={"device": "lo"},
        )


class FaultNodeFreeze(FaultPrimitive):
    """Freeze one runtime node with SIGSTOP during the load window and SIGCONT on removal.

    Parameters: runtime_metadata_path, target_node,
                [freeze_timeout_seconds=5], [resume_timeout_seconds=5], [executor=]
    """

    def _runtime_node(self):
        metadata_path = Path(self.params["runtime_metadata_path"])
        if not metadata_path.exists():
            raise ValueError(f"fault_node_freeze runtime metadata missing: {metadata_path}")
        body = json.loads(metadata_path.read_text(encoding="utf-8"))
        target_node = str(self.params["target_node"])
        nodes = body.get("haskell_nodes") or body.get("amaru_nodes") or []
        for node in nodes:
            if str(node.get("name")) == target_node:
                return metadata_path, target_node, node
        raise ValueError(f"fault_node_freeze could not find target_node {target_node!r} in {metadata_path}")

    def _ps(self, *args):
        import subprocess

        return subprocess.run(
            ["ps", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )

    def _process_comm_and_args(self, pid: int):
        result = self._ps("-o", "comm=,args=", "-p", str(pid))
        if result.returncode != 0:
            return None, None
        line = result.stdout.strip()
        if not line:
            return None, None
        parts = line.split(None, 1)
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], parts[1]

    def _candidate_pid_matches(self, *, pid: int, node: dict) -> bool:
        comm, args = self._process_comm_and_args(pid)
        if comm != "cardano-node":
            return False
        socket_path = str(node.get("socket_path") or "")
        port = node.get("port")
        if socket_path and socket_path in args:
            return True
        if port is not None and f"--port {int(port)}" in args:
            return True
        return False

    def _scan_for_runtime_pid(self, node: dict):
        result = self._ps("-eo", "pid=,comm=,args=")
        if result.returncode != 0:
            raise RuntimeError("fault_node_freeze could not inspect process table")
        socket_path = str(node.get("socket_path") or "")
        port = node.get("port")
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            pid_text, comm, args = parts
            if comm != "cardano-node":
                continue
            if socket_path and socket_path in args:
                return int(pid_text)
            if port is not None and f"--port {int(port)}" in args:
                return int(pid_text)
        raise RuntimeError(f"fault_node_freeze could not resolve runtime pid for node {node.get('name')!r}")

    def _resolve_target_pid(self):
        _metadata_path, _target_node, node = self._runtime_node()
        pid_file = node.get("pid_file")
        if isinstance(pid_file, str) and pid_file:
            try:
                pid = int(Path(pid_file).read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                pid = None
            if pid is not None and self._candidate_pid_matches(pid=pid, node=node):
                return pid
        return self._scan_for_runtime_pid(node)

    def _process_state(self, pid: int):
        result = self._ps("-o", "stat=", "-p", str(pid))
        if result.returncode != 0:
            raise RuntimeError(f"fault_node_freeze could not inspect process state for pid {pid}")
        state = result.stdout.strip()
        if not state:
            raise RuntimeError(f"fault_node_freeze got empty process state for pid {pid}")
        return state

    def _wait_for_stopped_state(self, pid: int):
        import time

        deadline = time.monotonic() + float(self.params.get("freeze_timeout_seconds", 5))
        last = None
        while time.monotonic() < deadline:
            state = self._process_state(pid)
            last = state
            if "T" in state:
                return state
            time.sleep(0.1)
        raise RuntimeError(f"fault_node_freeze target pid {pid} did not enter stopped state; last_state={last!r}")

    def _wait_for_resumed_state(self, pid: int):
        import time

        deadline = time.monotonic() + float(self.params.get("resume_timeout_seconds", 5))
        last = None
        while time.monotonic() < deadline:
            state = self._process_state(pid)
            last = state
            if "T" not in state:
                return state
            time.sleep(0.1)
        raise RuntimeError(f"fault_node_freeze target pid {pid} did not resume; last_state={last!r}")

    def apply(self, handle):
        executor = _resolve_local_executor(self.params)
        metadata_path, target_node, node = self._runtime_node()
        pid = self._resolve_target_pid()
        result = executor(f"sudo kill -STOP {pid}", timeout=10, dry_run=False)
        if result.returncode != 0:
            raise RuntimeError(f"fault_node_freeze apply failed for pid {pid} (exit {result.returncode})")
        state = self._wait_for_stopped_state(pid)
        self._target_pid = pid
        self._target_node = target_node
        handle.log(
            phase="fault",
            primitive="fault_node_freeze",
            level="info",
            event="applied",
            payload={
                "runtime_metadata_path": str(metadata_path),
                "target_node": target_node,
                "pid": pid,
                "port": node.get("port"),
                "state": state,
            },
        )

    def remove(self, handle):
        executor = _resolve_local_executor(self.params)
        pid = getattr(self, "_target_pid", None)
        if pid is None:
            return
        result = executor(f"sudo kill -CONT {pid}", timeout=10, dry_run=False)
        if result.returncode != 0:
            raise RuntimeError(f"fault_node_freeze remove failed for pid {pid} (exit {result.returncode})")
        state = self._wait_for_resumed_state(pid)
        handle.log(
            phase="fault",
            primitive="fault_node_freeze",
            level="info",
            event="removed",
            payload={
                "target_node": getattr(self, "_target_node", None),
                "pid": pid,
                "resumed": True,
                "state": state,
            },
        )


# ---------------------------------------------------------------------------
# Resource-abuse loads
# ---------------------------------------------------------------------------


class DiskFill(LoadPrimitive):
    """Fill disk space inside a container toward the cgroup/volume limit.

    Parameters: target_container, target_path, size_mb, [executor=]
    """

    def run(self, handle, rng):
        import shlex as _shlex
        target = self.params["target_container"]
        path = self.params["target_path"]
        size_mb = int(self.params["size_mb"])
        cmd = (
            f"docker exec {_shlex.quote(target)} "
            f"dd if=/dev/zero of={_shlex.quote(path)} bs=1M count={size_mb} 2>&1"
        )
        executor = _resolve_executor(self.params)
        result = executor(cmd, timeout=300, dry_run=False)
        handle.log(phase="load", primitive="disk_fill", level="info",
                   event="completed",
                   payload={"target": target, "path": path, "size_mb": size_mb,
                            "exit": result.returncode, "stdout_head": (result.stdout or "")[:200]})


class SyncReplay(LoadPrimitive):
    """Force a node to re-sync from its peers by wiping its local DB and restarting.

    Stops the target container, deletes its DB directory, restarts it — the
    node must then replay the chain from peers.

    Parameters: target_container, db_path, [executor=]
    """

    def run(self, handle, rng):
        import shlex as _shlex
        target = self.params["target_container"]
        db_path = self.params["db_path"]
        executor = _resolve_executor(self.params)
        stop = executor(f"docker stop {_shlex.quote(target)}", timeout=60, dry_run=False)
        handle.log(phase="load", primitive="sync_replay", level="info",
                   event="stopped", payload={"target": target, "exit": stop.returncode})
        rm = executor(
            f"docker exec {_shlex.quote(target)} rm -rf {_shlex.quote(db_path)} 2>/dev/null || "
            f"rm -rf {_shlex.quote(db_path)}",
            timeout=60, dry_run=False,
        )
        handle.log(phase="load", primitive="sync_replay", level="info",
                   event="db_wiped", payload={"db_path": db_path, "exit": rm.returncode})
        start = executor(f"docker start {_shlex.quote(target)}", timeout=60, dry_run=False)
        handle.log(phase="load", primitive="sync_replay", level="info",
                   event="restarted", payload={"target": target, "exit": start.returncode})


class WaitForTip(LoadPrimitive):
    """Setup primitive: poll the node tip until it returns a block; raise on timeout.

    Parameters: profile_id, [interval_seconds=5], [timeout_seconds=120], [executor=]
    """

    def run(self, handle, rng):
        import time
        from profile_manager.inspect import inspect_health_command
        profile_id = self.params["profile_id"]
        interval = float(self.params.get("interval_seconds", 5))
        timeout = float(self.params.get("timeout_seconds", 120))
        executor = _resolve_executor(self.params)
        deadline = time.monotonic() + timeout
        attempts = 0
        while True:
            attempts += 1
            cmd = inspect_health_command(profile_id)
            result = executor(cmd, timeout=30, dry_run=False)
            stdout = (result.stdout or "")
            if '"block"' in stdout or "syncProgress" in stdout:
                handle.log(phase="setup", primitive="wait_for_tip", level="info",
                           event="ready", payload={"profile_id": profile_id, "attempts": attempts})
                return
            handle.log(phase="setup", primitive="wait_for_tip", level="info",
                       event="poll", payload={"profile_id": profile_id, "attempts": attempts})
            if time.monotonic() >= deadline:
                raise TimeoutError(f"wait_for_tip timed out after {timeout}s for {profile_id}")
            time.sleep(interval)
