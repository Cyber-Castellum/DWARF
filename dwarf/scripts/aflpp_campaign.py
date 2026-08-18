#!/usr/bin/env python3

import argparse
import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime_telemetry import emit_runtime_metric, emit_target_event, run_dir
from profile_manager import testcase_lifecycle


PRIMITIVE = "aflpp_campaign"
CASE_NAME_RE = re.compile(r"(?P<key>[^:,]+):(?P<value>[^,]+)")
SANITIZER_AFL_ENV = {
    "address": "AFL_USE_ASAN",
    "thread": "AFL_USE_TSAN",
    "undefined": "AFL_USE_UBSAN",
    "memory": "AFL_USE_MSAN",
}

ARCH_ALIASES = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "aarch64",
    "arm64": "aarch64",
}


def _workspace_root_manifest_path(working_dir: Path) -> Path | None:
    working_dir = working_dir.resolve()
    local_manifest = working_dir / "Cargo.toml"
    if not local_manifest.is_file():
        return None
    for parent in working_dir.parents:
        candidate = parent / "Cargo.toml"
        if candidate == local_manifest:
            continue
        if candidate.is_file():
            return candidate
    return None


@contextlib.contextmanager
def _temporarily_hide_workspace_root_manifest(working_dir: Path):
    root_manifest = _workspace_root_manifest_path(working_dir)
    hidden_manifest = None
    if root_manifest is not None:
        hidden_manifest = root_manifest.with_name(f"{root_manifest.name}.dwarf-hidden")
        os.replace(root_manifest, hidden_manifest)
    try:
        yield hidden_manifest
    finally:
        if hidden_manifest is not None and hidden_manifest.is_file():
            os.replace(hidden_manifest, root_manifest)


def parse_fuzzer_stats(text: str) -> dict:
    stats = {}
    for line in text.splitlines():
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


def merge_seed_directories(*, seed_dirs: list[Path], corpus_dir: Path) -> None:
    if corpus_dir.exists():
        shutil.rmtree(corpus_dir)
    corpus_dir.mkdir(parents=True, exist_ok=True)

    for source_index, seed_dir in enumerate(seed_dirs):
        if not seed_dir.is_dir():
            raise FileNotFoundError(f"seed directory not found: {seed_dir}")
        for item in sorted(path for path in seed_dir.rglob("*") if path.is_file()):
            relative = item.relative_to(seed_dir).as_posix().replace("/", "__")
            destination = corpus_dir / f"{source_index:02d}-{relative}"
            shutil.copy2(item, destination)


def build_aflpp_fuzz_command(
    *,
    seed_dir: Path,
    output_dir: Path,
    target_binary: Path,
    seconds: int,
    dict_path: Path | None = None,
    rng_seed: int | None = None,
    afl_mode: str = "instrumented",
    prebuilt: bool = False,
) -> list[str]:
    # A prebuilt (non-Rust) target invokes afl-fuzz directly; Rust cargo-fuzz
    # targets go through `cargo afl fuzz` (which forwards the same flags).
    # The afl-fuzz binary must match the runtime linked into the instrumented
    # target: the GHC/SanCov harness links afl.rs 4.40c, so a mismatched system
    # afl-fuzz (e.g. 4.09c) fails the forkserver handshake. Allow an explicit
    # override via DWARF_AFL_FUZZ; default to PATH `afl-fuzz`.
    afl_fuzz_bin = os.environ.get("DWARF_AFL_FUZZ", "afl-fuzz")
    command = ([afl_fuzz_bin] if prebuilt else ["cargo", "afl", "fuzz"]) + [
        "-i",
        str(seed_dir),
        "-o",
        str(output_dir),
        "-V",
        str(seconds),
    ]
    if prebuilt:
        # GHC/SanCov harnesses can allocate heavily during a single exec (e.g.
        # the applyblock surface builds a full genesis Conway NewEpochState),
        # which trips AFL's default per-target memory cap and aborts the fork
        # server with "Unable to request new process (OOM?)". Lift the cap.
        command.extend(["-m", "none"])
    if afl_mode == "qemu":
        command.append("-Q")
    if rng_seed is not None:
        command.extend(["-s", str(rng_seed)])
    if dict_path is not None:
        command.extend(["-x", str(dict_path)])
    command.append(str(target_binary))
    if prebuilt:
        # File-arg harness (reads the input path from argv); cargo-fuzz targets
        # read stdin and take no @@.
        command.append("@@")
    return command


def _merge_rustflags(existing: str | None, new_flag: str) -> str:
    flags = (existing or "").strip()
    parts = flags.split()
    if new_flag in parts:
        return flags
    if not flags:
        return new_flag
    return f"{flags} {new_flag}"


def _detect_afl_qemu_path() -> str | None:
    qemu_trace = shutil.which("afl-qemu-trace")
    if qemu_trace:
        return str(Path(qemu_trace).resolve().parent)
    default_share_path = Path.home() / ".local" / "share" / "afl.rs" / "AFLplusplus" / "afl-qemu-trace"
    if default_share_path.is_file():
        return str(default_share_path.parent)
    return None


def _normalize_arch(value: str | None) -> str | None:
    if value is None:
        return None
    return ARCH_ALIASES.get(value.strip().lower(), value.strip().lower())


def _host_arch() -> str:
    return _normalize_arch(platform.machine()) or platform.machine()


def _detect_binary_arch(target_binary: Path) -> str | None:
    readelf = shutil.which("readelf")
    if readelf is not None:
        inspect = subprocess.run(
            [readelf, "-h", str(target_binary)],
            text=True,
            capture_output=True,
            check=False,
        )
        if inspect.returncode == 0:
            machine_match = re.search(r"Machine:\s*(.+)", inspect.stdout)
            if machine_match:
                machine = machine_match.group(1).strip().lower()
                if "aarch64" in machine or "arm64" in machine:
                    return "aarch64"
                if "x86-64" in machine or "amd x86-64" in machine or "advanced micro devices x86-64" in machine:
                    return "x86_64"

    file_bin = shutil.which("file")
    if file_bin is not None:
        inspect = subprocess.run(
            [file_bin, "-b", str(target_binary)],
            text=True,
            capture_output=True,
            check=False,
        )
        if inspect.returncode == 0:
            description = inspect.stdout.lower()
            if "aarch64" in description or "arm64" in description:
                return "aarch64"
            if "x86-64" in description or "x86_64" in description:
                return "x86_64"
    return None


def build_aflpp_env(
    base_env: dict[str, str] | None = None,
    *,
    sanitizer: str | None = None,
    afl_mode: str = "instrumented",
) -> dict[str, str]:
    fuzz_env = dict(base_env or os.environ.copy())
    fuzz_env.setdefault("AFL_NO_UI", "1")
    fuzz_env.setdefault("AFL_SKIP_CPUFREQ", "1")
    fuzz_env.setdefault("AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES", "1")
    if afl_mode == "qemu" and "AFL_PATH" not in fuzz_env:
        afl_path = _detect_afl_qemu_path()
        if afl_path is not None:
            fuzz_env["AFL_PATH"] = afl_path
    if sanitizer is not None:
        afl_env_name = SANITIZER_AFL_ENV.get(sanitizer)
        if afl_env_name is None:
            raise ValueError(f"unsupported sanitizer: {sanitizer}")
        fuzz_env[afl_env_name] = "1"
        fuzz_env["RUSTFLAGS"] = _merge_rustflags(fuzz_env.get("RUSTFLAGS"), f"-Zsanitizer={sanitizer}")
    return fuzz_env


def _build_command(
    *,
    bin_name: str,
    target_triple: str | None = None,
    sanitizer: str | None = None,
) -> list[str]:
    command = ["cargo", "afl", "build", "--release"]
    if target_triple is not None:
        command.extend(["--target", target_triple])
    if sanitizer in {"memory", "thread"}:
        command.append("-Zbuild-std")
    command.extend(["--bin", bin_name])
    return command


def _resolve_target_binary(*, working_dir: Path, bin_name: str, target_triple: str | None = None) -> Path:
    if target_triple is None:
        return working_dir / "target" / "release" / bin_name
    return working_dir / "target" / target_triple / "release" / bin_name


def _read_stats(output_dir: Path) -> dict:
    stats_path = output_dir / "default" / "fuzzer_stats"
    if not stats_path.is_file():
        return {}
    return parse_fuzzer_stats(stats_path.read_text(encoding="utf-8", errors="replace"))


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect_case_entries(path: Path) -> list[dict]:
    if not path.is_dir():
        return []
    entries = []
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        entries.append(
            {
                "relative_path": item.relative_to(path).as_posix(),
                "size_bytes": item.stat().st_size,
                "sha256": _sha256_path(item),
            }
        )
    return entries


def parse_case_metadata(relative_path: str) -> dict:
    metadata = {}
    for match in CASE_NAME_RE.finditer(Path(relative_path).name):
        metadata[match.group("key")] = match.group("value")
    return metadata


def build_triage_summary(summary: dict) -> dict:
    queue_entries = summary["queue_entries"]
    testcase_entries = [entry for entry in queue_entries if not entry["relative_path"].startswith(".state/")]
    coverage_entries = [entry for entry in testcase_entries if "+cov" in Path(entry["relative_path"]).name]

    interesting_cases = []
    for entry in summary["crash_entries"]:
        interesting_cases.append(
            {
                "kind": "crash",
                "relative_path": entry["relative_path"],
                "size_bytes": entry["size_bytes"],
                "sha256": entry["sha256"],
                "reason": "saved_crash",
                "metadata": parse_case_metadata(entry["relative_path"]),
            }
        )
    for entry in summary["hang_entries"]:
        interesting_cases.append(
            {
                "kind": "hang",
                "relative_path": entry["relative_path"],
                "size_bytes": entry["size_bytes"],
                "sha256": entry["sha256"],
                "reason": "saved_hang",
                "metadata": parse_case_metadata(entry["relative_path"]),
            }
        )
    for entry in coverage_entries[:100]:
        interesting_cases.append(
            {
                "kind": "queue",
                "relative_path": entry["relative_path"],
                "size_bytes": entry["size_bytes"],
                "sha256": entry["sha256"],
                "reason": "coverage_increase",
                "metadata": parse_case_metadata(entry["relative_path"]),
            }
        )

    return {
        "queue_file_count": summary["queue_count"],
        "queue_testcase_count": len(testcase_entries),
        "queue_state_file_count": summary["queue_count"] - len(testcase_entries),
        "queue_coverage_case_count": len(coverage_entries),
        "crash_count": summary["crash_count"],
        "hang_count": summary["hang_count"],
        "interesting_case_count": len(interesting_cases),
        "interesting_cases": interesting_cases,
    }


def summarize_campaign_output(output_dir: Path) -> dict:
    default_dir = output_dir / "default"
    queue_entries = _collect_case_entries(default_dir / "queue")
    crash_entries = _collect_case_entries(default_dir / "crashes")
    hang_entries = _collect_case_entries(default_dir / "hangs")
    return {
        "output_dir": str(output_dir),
        "default_dir": str(default_dir),
        "has_fuzzer_stats": (default_dir / "fuzzer_stats").is_file(),
        "has_plot_data": (default_dir / "plot_data").is_file(),
        "queue_count": len(queue_entries),
        "crash_count": len(crash_entries),
        "hang_count": len(hang_entries),
        "queue_entries": queue_entries,
        "crash_entries": crash_entries,
        "hang_entries": hang_entries,
    }


def export_campaign_artifacts_with_metadata(
    *,
    output_dir: Path,
    bundle_run_dir: Path,
    target_implementation: str,
    replay_harness: str,
    replay_target_id: str,
    replay_targets: list[str],
    host_arch: str | None = None,
    target_arch: str | None = None,
) -> Path:
    destination = bundle_run_dir / "outputs" / "aflpp"
    default_src = output_dir / "default"
    default_dst = destination / "default"
    destination.mkdir(parents=True, exist_ok=True)
    default_dst.mkdir(parents=True, exist_ok=True)

    for file_name in ("fuzzer_stats", "plot_data"):
        src = default_src / file_name
        if src.is_file():
            shutil.copy2(src, default_dst / file_name)

    for directory in ("queue", "crashes", "hangs"):
        src = default_src / directory
        dst = default_dst / directory
        if dst.exists():
            shutil.rmtree(dst)
        if src.is_dir():
            shutil.copytree(src, dst)

    for log_name in ("stdout.log", "stderr.log"):
        src = output_dir / log_name
        if src.is_file():
            shutil.copy2(src, destination / log_name)

    summary = summarize_campaign_output(output_dir)
    summary["bundle_artifact_dir"] = str(destination)
    summary["host_arch"] = host_arch
    summary["target_arch"] = target_arch
    summary_path = destination / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    triage = build_triage_summary(summary)
    triage_path = destination / "triage.json"
    triage_path.write_text(json.dumps(triage, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    state_dir = testcase_lifecycle.default_state_dir_for_run(bundle_run_dir)
    testcase_records = testcase_lifecycle.build_testcase_records(
        run_id=bundle_run_dir.name,
        producer="aflpp",
        target_implementation=target_implementation,
        triage=triage,
        source_root="outputs/aflpp/default",
        replay_harness=replay_harness,
        replay_target_id=replay_target_id,
        replay_targets=replay_targets,
    )
    lifecycle_paths = testcase_lifecycle.write_lifecycle_artifacts(
        run_dir=bundle_run_dir,
        state_dir=state_dir,
        records=testcase_records,
    )
    summary["lifecycle"] = lifecycle_paths
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary_path


def run_campaign_with_metadata(
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
    rng_seed: int | None = None,
    afl_mode: str = "instrumented",
    target_binary_path: Path | None = None,
) -> int:
    if target_binary_path is not None:
        target_binary = target_binary_path
    else:
        target_binary = _resolve_target_binary(working_dir=working_dir, bin_name=bin_name, target_triple=target_triple)
    host_arch = _host_arch()
    target_arch = _detect_binary_arch(target_binary)
    # A prebuilt target_binary_path (e.g. the pre-instrumented GHC/SanCov
    # cardano-node coverage harness) needs no cargo/cargo-afl build toolchain;
    # only Rust cargo-fuzz targets (resolved via _resolve_target_binary) do.
    if target_binary_path is None:
        if shutil.which("cargo") is None:
            raise SystemExit("cargo not found in PATH")
        with _temporarily_hide_workspace_root_manifest(working_dir):
            afl_available = subprocess.run(
                ["cargo", "afl", "--help"],
                cwd=working_dir,
                text=True,
                capture_output=True,
                check=False,
                env=os.environ.copy(),
            )
        if afl_available.returncode != 0:
            raise SystemExit("cargo-afl tooling not found in PATH; install with `cargo install cargo-afl`")

    corpus_dir = working_dir / "corpus" / bin_name
    merge_seed_directories(seed_dirs=seed_dirs, corpus_dir=corpus_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)

    emit_target_event(
        primitive=PRIMITIVE,
        event="campaign_started",
        payload={
            "working_dir": str(working_dir),
            "bin_name": bin_name,
            "target_implementation": target_implementation,
            "replay_harness": replay_harness,
            "replay_target_id": replay_target_id,
            "replay_targets": replay_targets,
            "seed_dirs": [str(path) for path in seed_dirs],
            "output_dir": str(output_dir),
            "seconds": seconds,
            "dict_path": str(dict_path) if dict_path else None,
            "sanitizer": sanitizer,
            "target_triple": target_triple,
            "rng_seed": rng_seed,
            "afl_mode": afl_mode,
            "target_binary": str(target_binary),
            "host_arch": host_arch,
            "target_arch": target_arch,
        },
    )

    afl_env = build_aflpp_env(os.environ.copy(), sanitizer=sanitizer, afl_mode=afl_mode)
    if afl_mode == "instrumented" and target_binary_path is None:
        with _temporarily_hide_workspace_root_manifest(working_dir):
            build = subprocess.run(
                _build_command(bin_name=bin_name, target_triple=target_triple, sanitizer=sanitizer),
                cwd=working_dir,
                text=True,
                capture_output=True,
                check=False,
                env=afl_env,
            )
        emit_runtime_metric("build_exit_code", value=build.returncode, meta={"bin": bin_name, "afl_mode": afl_mode})
        if build.returncode != 0:
            emit_target_event(
                primitive=PRIMITIVE,
                event="build_failed",
                level="error",
                payload={"stderr": build.stderr[-4000:], "stdout": build.stdout[-4000:]},
            )
            print(build.stdout, end="")
            print(build.stderr, end="", file=os.sys.stderr)
            return build.returncode
    else:
        if not target_binary.is_file():
            raise SystemExit(f"target binary not found for qemu mode: {target_binary}")
        emit_runtime_metric("build_skipped", value=1, meta={"bin": bin_name, "afl_mode": afl_mode})

    output_dir.mkdir(parents=True, exist_ok=True)
    with _temporarily_hide_workspace_root_manifest(working_dir):
        fuzz = subprocess.run(
            build_aflpp_fuzz_command(
                seed_dir=corpus_dir,
                output_dir=output_dir,
                target_binary=target_binary,
                seconds=seconds,
                dict_path=dict_path,
                rng_seed=rng_seed,
                afl_mode=afl_mode,
                prebuilt=target_binary_path is not None,
            ),
            cwd=working_dir,
            text=True,
            capture_output=True,
            check=False,
            env=afl_env,
        )
    (output_dir / "stdout.log").write_text(fuzz.stdout or "", encoding="utf-8")
    (output_dir / "stderr.log").write_text(fuzz.stderr or "", encoding="utf-8")

    stats = _read_stats(output_dir)
    artifact_summary = summarize_campaign_output(output_dir)
    triage_summary = build_triage_summary(artifact_summary)
    for key in ("execs_done", "execs_per_sec", "saved_crashes", "saved_hangs", "corpus_count", "cycles_done"):
        if key in stats:
            emit_runtime_metric(key, value=stats[key], meta={"collector": "aflpp_fuzzer_stats"})
    for key, value in (
        ("queue_count", artifact_summary["queue_count"]),
        ("crash_count", artifact_summary["crash_count"]),
        ("hang_count", artifact_summary["hang_count"]),
        ("queue_testcase_count", triage_summary["queue_testcase_count"]),
        ("interesting_case_count", triage_summary["interesting_case_count"]),
    ):
        emit_runtime_metric(key, value=value, meta={"collector": "aflpp_artifact_summary"})

    summary_path = None
    bundle_run_dir = run_dir()
    if bundle_run_dir is not None:
        summary_path = export_campaign_artifacts_with_metadata(
            output_dir=output_dir,
            bundle_run_dir=bundle_run_dir,
            target_implementation=target_implementation,
            replay_harness=replay_harness,
            replay_target_id=replay_target_id,
            replay_targets=replay_targets,
            host_arch=host_arch,
            target_arch=target_arch,
        )

    emit_target_event(
        primitive=PRIMITIVE,
        event="campaign_completed",
        payload={
            "exit_code": fuzz.returncode,
            "target_binary": str(target_binary),
            "stats": stats,
            "artifact_summary": {
                "queue_count": artifact_summary["queue_count"],
                "crash_count": artifact_summary["crash_count"],
                "hang_count": artifact_summary["hang_count"],
                "queue_testcase_count": triage_summary["queue_testcase_count"],
                "interesting_case_count": triage_summary["interesting_case_count"],
                "summary_path": str(summary_path) if summary_path else None,
                "afl_mode": afl_mode,
                "host_arch": host_arch,
                "target_arch": target_arch,
            },
        },
        level="info" if fuzz.returncode == 0 else "error",
    )
    print(
        json.dumps(
            {
                "bin": bin_name,
                "output_dir": str(output_dir),
                "sanitizer": sanitizer,
                "stats": stats,
                "target_binary": str(target_binary),
                "target_triple": target_triple,
                "rng_seed": rng_seed,
                "afl_mode": afl_mode,
                "host_arch": host_arch,
                "target_arch": target_arch,
            },
            sort_keys=True,
        )
    )
    if fuzz.stdout:
        print(fuzz.stdout, end="")
    if fuzz.stderr:
        print(fuzz.stderr, end="", file=os.sys.stderr)
    return fuzz.returncode


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--working-dir", required=True)
    parser.add_argument("--bin", dest="bin_name", required=True)
    parser.add_argument("--seed-dir", dest="seed_dirs", action="append", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seconds", type=int, required=True)
    parser.add_argument("--dict-path")
    parser.add_argument("--target-implementation", default="amaru")
    parser.add_argument("--replay-harness", required=True)
    parser.add_argument("--replay-target-id", required=True)
    parser.add_argument("--replay-target", dest="replay_targets", action="append", required=True)
    parser.add_argument("--sanitizer", choices=sorted(SANITIZER_AFL_ENV))
    parser.add_argument("--target-triple")
    parser.add_argument("--rng-seed", type=int)
    parser.add_argument("--afl-mode", choices=["instrumented", "qemu"], default="instrumented")
    parser.add_argument("--target-binary-path")
    args = parser.parse_args(argv)
    return run_campaign_with_metadata(
        working_dir=Path(args.working_dir),
        bin_name=args.bin_name,
        seed_dirs=[Path(path) for path in args.seed_dirs],
        output_dir=Path(args.output_dir),
        seconds=args.seconds,
        dict_path=Path(args.dict_path) if args.dict_path else None,
        target_implementation=args.target_implementation,
        replay_harness=args.replay_harness,
        replay_target_id=args.replay_target_id,
        replay_targets=args.replay_targets,
        sanitizer=args.sanitizer,
        target_triple=args.target_triple,
        rng_seed=args.rng_seed,
        afl_mode=args.afl_mode,
        target_binary_path=Path(args.target_binary_path) if args.target_binary_path else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
