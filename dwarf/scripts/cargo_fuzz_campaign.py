#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
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


PRIMITIVE = "cargo_fuzz_campaign"
DEFAULT_TOOLCHAIN = "nightly-2025-11-21"


def _merge_rustflags(existing: str | None, new_flag: str) -> str:
    flags = (existing or "").strip()
    parts = flags.split()
    if new_flag in parts:
        return flags
    if not flags:
        return new_flag
    return f"{flags} {new_flag}"


def grammar_dict_path_for_fuzz_dir(fuzz_dir: Path | None) -> Path | None:
    if fuzz_dir is None:
        return None
    candidate = REPO_ROOT / "grammars" / fuzz_dir.name / "dict.txt"
    if candidate.is_file():
        return candidate
    return None


def custom_mutator_len_control_for_fuzz_dir(fuzz_dir: Path | None) -> int | None:
    if fuzz_dir is None:
        return None
    fuzz_targets_dir = fuzz_dir / "fuzz_targets"
    if not fuzz_targets_dir.is_dir():
        return None
    for source_path in sorted(fuzz_targets_dir.glob("*.rs")):
        source = source_path.read_text(encoding="utf-8")
        if "fuzz_mutator!" in source or "register_structural_mutator!" in source:
            return 100
    return None


def build_cargo_fuzz_command(
    *,
    target_name: str,
    seconds: int,
    fuzz_dir: Path | None = None,
    dict_path: Path | None = None,
    len_control: int | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    command = [
        "cargo",
        "fuzz",
        "run",
    ]
    if fuzz_dir is not None:
        command.extend(["--fuzz-dir", str(fuzz_dir)])
    command.extend([
        target_name,
        "--",
        f"-max_total_time={seconds}",
        "-print_final_stats=1",
    ])
    if len_control is not None:
        command.append(f"-len_control={len_control}")
    if dict_path is not None:
        command.append(f"-dict={dict_path}")
    if extra_args:
        command.extend(extra_args)
    return command


def build_cargo_fuzz_env(
    base_env: dict[str, str] | None = None,
    *,
    toolchain: str = DEFAULT_TOOLCHAIN,
    coverage_dir: Path | None = None,
) -> dict[str, str]:
    fuzz_env = dict(base_env or os.environ.copy())
    fuzz_env.setdefault("RUSTUP_TOOLCHAIN", toolchain)
    fuzz_env.setdefault("ASAN_OPTIONS", "detect_leaks=0")
    fuzz_env.setdefault("UBSAN_OPTIONS", "print_stacktrace=1")
    if coverage_dir is not None:
        raw_dir = coverage_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        fuzz_env["RUSTFLAGS"] = _merge_rustflags(fuzz_env.get("RUSTFLAGS"), "-Cinstrument-coverage")
        fuzz_env["LLVM_PROFILE_FILE"] = str(raw_dir / "%p-%m.profraw")
    return fuzz_env


def resolve_llvm_tool(tool_name: str, *, toolchain: str = DEFAULT_TOOLCHAIN) -> Path:
    cargo_home = Path.home() / ".cargo" / "bin"
    direct = cargo_home / tool_name
    if direct.is_file():
        return direct

    rustc = shutil.which("rustc")
    if rustc is None:
        raise FileNotFoundError("rustc not found in PATH")
    rustc_cmd = [rustc]
    if toolchain:
        rustc_cmd.append(f"+{toolchain}")
    rustc_cmd.extend(["--print", "sysroot"])
    sysroot_proc = subprocess.run(rustc_cmd, text=True, capture_output=True, check=True)
    sysroot = Path(sysroot_proc.stdout.strip())

    host_proc = subprocess.run(rustc_cmd + ["-vV"], text=True, capture_output=True, check=True)
    host = None
    for line in host_proc.stdout.splitlines():
        if line.startswith("host: "):
            host = line.split(":", 1)[1].strip()
            break
    if host is None:
        raise RuntimeError("unable to determine rust host triple")

    candidate = sysroot / "lib" / "rustlib" / host / "bin" / tool_name
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"{tool_name} not found under {candidate}")


def resolve_fuzz_target_binary(
    *,
    working_dir: Path,
    fuzz_dir: Path,
    target_name: str,
    toolchain: str = DEFAULT_TOOLCHAIN,
) -> Path | None:
    candidates: list[Path] = [
        fuzz_dir / "target" / toolchain / "release" / target_name,
    ]
    if (fuzz_dir / "target").is_dir():
        candidates.extend(sorted((fuzz_dir / "target").glob(f"*/release/{target_name}")))
    candidates.extend([
        fuzz_dir / "target" / "release" / target_name,
        working_dir / "target" / toolchain / "release" / target_name,
        working_dir / "target" / "release" / target_name,
    ])
    if (working_dir / "target").is_dir():
        candidates.extend(sorted((working_dir / "target").glob(f"*/release/{target_name}")))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def merge_coverage_profiles(
    *,
    coverage_dir: Path,
    target_binary: Path,
    llvm_profdata: Path,
    llvm_cov: Path,
    command_log: Path | None = None,
) -> dict:
    raw_dir = coverage_dir / "raw"
    profraw_files = sorted(path for path in raw_dir.glob("*.profraw") if path.is_file())
    if not profraw_files:
        return {}

    profdata_path = coverage_dir / "default.profdata"
    merge_command = [str(llvm_profdata)]
    if command_log is not None:
        merge_command.append(str(command_log))
    merge_command.extend(["merge", "-sparse", *[str(path) for path in profraw_files], "-o", str(profdata_path)])
    merge_proc = subprocess.run(merge_command, text=True, capture_output=True, check=False)
    if merge_proc.returncode != 0:
        raise RuntimeError(f"llvm-profdata merge failed with exit {merge_proc.returncode}: {merge_proc.stdout}{merge_proc.stderr}")

    cov_command = [str(llvm_cov)]
    if command_log is not None:
        cov_command.append(str(command_log))
    cov_command.extend(["export", "-summary-only", f"-instr-profile={profdata_path}", str(target_binary)])
    cov_proc = subprocess.run(cov_command, text=True, capture_output=True, check=False)
    if cov_proc.returncode != 0:
        raise RuntimeError(f"llvm-cov export failed with exit {cov_proc.returncode}: {cov_proc.stdout}{cov_proc.stderr}")

    payload = json.loads(cov_proc.stdout)
    totals = ((payload.get("data") or [{}])[0]).get("totals") or {}
    summary = {
        "target_binary": str(target_binary),
        "profraw_count": len(profraw_files),
        "profraw_files": [str(path) for path in profraw_files],
        "profdata_path": str(profdata_path),
        "covered_functions": int((totals.get("functions") or {}).get("covered", 0)),
        "covered_lines": int((totals.get("lines") or {}).get("covered", 0)),
        "covered_regions": int((totals.get("regions") or {}).get("covered", 0)),
        "total_functions": int((totals.get("functions") or {}).get("count", 0)),
        "total_lines": int((totals.get("lines") or {}).get("count", 0)),
        "total_regions": int((totals.get("regions") or {}).get("count", 0)),
    }
    (coverage_dir / "coverage.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


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
        if item.name.startswith("._"):
            continue
        entries.append(
            {
                "relative_path": item.relative_to(path).as_posix(),
                "size_bytes": item.stat().st_size,
                "sha256": _sha256_path(item),
            }
        )
    return entries


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


def normalize_campaign_output(*, corpus_dir: Path, artifacts_dir: Path, output_dir: Path) -> Path:
    default_dir = output_dir / "default"
    queue_dir = default_dir / "queue"
    crashes_dir = default_dir / "crashes"
    queue_dir.mkdir(parents=True, exist_ok=True)
    crashes_dir.mkdir(parents=True, exist_ok=True)

    for src_dir, dst_dir in ((corpus_dir, queue_dir), (artifacts_dir, crashes_dir)):
        if not src_dir.is_dir():
            continue
        for item in sorted(p for p in src_dir.rglob("*") if p.is_file()):
            relative = item.relative_to(src_dir)
            destination = dst_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)

    return output_dir


def summarize_campaign_output(output_dir: Path) -> dict:
    default_dir = output_dir / "default"
    queue_entries = _collect_case_entries(default_dir / "queue")
    crash_entries = _collect_case_entries(default_dir / "crashes")
    return {
        "output_dir": str(output_dir),
        "default_dir": str(default_dir),
        "queue_count": len(queue_entries),
        "crash_count": len(crash_entries),
        "hang_count": 0,
        "queue_entries": queue_entries,
        "crash_entries": crash_entries,
        "hang_entries": [],
    }


def parse_libfuzzer_stats(text: str) -> dict:
    stats = {}
    max_sequence = None
    for line in text.splitlines():
        match = re.match(r"^stat::([A-Za-z0-9_]+):\s*(.+?)\s*$", line)
        if not match:
            seq_match = re.match(r"^#(\d+)\b", line.strip())
            if seq_match:
                value = int(seq_match.group(1))
                if max_sequence is None or value > max_sequence:
                    max_sequence = value
            continue
        key, raw_value = match.groups()
        value = raw_value
        try:
            value = int(raw_value)
        except ValueError:
            try:
                value = float(raw_value)
            except ValueError:
                value = raw_value
        stats[key] = value
    if max_sequence is not None and "number_of_executed_units" not in stats:
        stats["number_of_executed_units_estimate"] = max_sequence
    return stats


def build_triage_summary(summary: dict) -> dict:
    interesting_cases = []
    for entry in summary["crash_entries"]:
        interesting_cases.append(
            {
                "kind": "crash",
                "relative_path": entry["relative_path"],
                "size_bytes": entry["size_bytes"],
                "sha256": entry["sha256"],
                "reason": "saved_crash",
                "metadata": {"source": "cargo-fuzz"},
            }
        )
    for entry in summary["queue_entries"][:100]:
        interesting_cases.append(
            {
                "kind": "queue",
                "relative_path": entry["relative_path"],
                "size_bytes": entry["size_bytes"],
                "sha256": entry["sha256"],
                "reason": "corpus_retained",
                "metadata": {"source": "cargo-fuzz"},
            }
        )
    return {
        "queue_testcase_count": summary["queue_count"],
        "queue_coverage_case_count": summary["queue_count"],
        "queue_state_file_count": 0,
        "crash_count": summary["crash_count"],
        "hang_count": 0,
        "interesting_case_count": len(interesting_cases),
        "interesting_cases": interesting_cases,
    }


def export_campaign_artifacts_with_metadata(
    *,
    output_dir: Path,
    bundle_run_dir: Path,
    target_implementation: str,
    replay_harness: str,
    replay_target_id: str,
    replay_targets: list[str],
) -> Path:
    destination = bundle_run_dir / "outputs" / "cargo-fuzz"
    default_src = output_dir / "default"
    default_dst = destination / "default"
    destination.mkdir(parents=True, exist_ok=True)
    default_dst.mkdir(parents=True, exist_ok=True)

    for directory in ("queue", "crashes"):
        src = default_src / directory
        dst = default_dst / directory
        if dst.exists():
            shutil.rmtree(dst)
        if src.is_dir():
            shutil.copytree(src, dst)

    coverage_src = output_dir / "coverage"
    coverage_dst = destination / "coverage"
    if coverage_dst.exists():
        shutil.rmtree(coverage_dst)
    if coverage_src.is_dir():
        shutil.copytree(coverage_src, coverage_dst)
    coverage_json = output_dir / "coverage" / "coverage.json"
    if coverage_json.is_file():
        shutil.copy2(coverage_json, destination / "coverage.json")

    summary = summarize_campaign_output(output_dir)
    execution_path = output_dir / "execution.json"
    if execution_path.is_file():
        summary["libfuzzer_stats"] = json.loads(execution_path.read_text(encoding="utf-8")).get("libfuzzer_stats", {})
    if coverage_json.is_file():
        summary["coverage"] = json.loads(coverage_json.read_text(encoding="utf-8"))
    summary["bundle_artifact_dir"] = str(destination)
    summary_path = destination / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for log_name in ("stdout.log", "stderr.log"):
        log_path = output_dir / log_name
        if log_path.is_file():
            shutil.copy2(log_path, destination / log_name)

    triage = build_triage_summary(summary)
    triage_path = destination / "triage.json"
    triage_path.write_text(json.dumps(triage, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    state_dir = testcase_lifecycle.default_state_dir_for_run(bundle_run_dir)
    testcase_records = testcase_lifecycle.build_testcase_records(
        run_id=bundle_run_dir.name,
        producer="cargo-fuzz",
        target_implementation=target_implementation,
        triage=triage,
        source_root="outputs/cargo-fuzz/default",
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
    fuzz_dir: Path,
    target_name: str,
    seed_dirs: list[Path],
    output_dir: Path,
    seconds: int,
    target_implementation: str,
    replay_harness: str,
    replay_target_id: str,
    replay_targets: list[str],
    toolchain: str = DEFAULT_TOOLCHAIN,
    dict_path_override: Path | None = None,
    extra_libfuzzer_args: list[str] | None = None,
) -> int:
    if shutil.which("cargo") is None:
        raise SystemExit("cargo not found in PATH")
    cargo_fuzz_available = subprocess.run(
        ["cargo", "fuzz", "--help"],
        cwd=working_dir,
        text=True,
        capture_output=True,
        check=False,
        env=build_cargo_fuzz_env(toolchain=toolchain),
    )
    if cargo_fuzz_available.returncode != 0:
        raise SystemExit("cargo-fuzz tooling not found in PATH; install with `cargo install cargo-fuzz --locked`")

    corpus_dir = fuzz_dir / "corpus" / target_name
    artifacts_dir = fuzz_dir / "artifacts" / target_name
    coverage_dir = output_dir / "coverage"
    corpus_dir.parent.mkdir(parents=True, exist_ok=True)
    merge_seed_directories(seed_dirs=seed_dirs, corpus_dir=corpus_dir)
    if artifacts_dir.exists():
        shutil.rmtree(artifacts_dir)

    dict_path = dict_path_override or grammar_dict_path_for_fuzz_dir(fuzz_dir)
    len_control = custom_mutator_len_control_for_fuzz_dir(fuzz_dir)

    emit_target_event(
        primitive=PRIMITIVE,
        event="campaign_started",
        payload={
            "working_dir": str(working_dir),
            "fuzz_dir": str(fuzz_dir),
            "target_name": target_name,
            "target_implementation": target_implementation,
            "replay_harness": replay_harness,
            "replay_target_id": replay_target_id,
            "replay_targets": replay_targets,
            "seed_dirs": [str(path) for path in seed_dirs],
            "output_dir": str(output_dir),
            "seconds": seconds,
            "toolchain": toolchain,
            "dict_path": str(dict_path) if dict_path else None,
            "len_control": len_control,
            "extra_libfuzzer_args": extra_libfuzzer_args or [],
        },
    )

    fuzz = subprocess.run(
        build_cargo_fuzz_command(
            target_name=target_name,
            seconds=seconds,
            fuzz_dir=fuzz_dir,
            dict_path=dict_path,
            len_control=len_control,
            extra_args=extra_libfuzzer_args,
        ),
        cwd=working_dir,
        text=True,
        capture_output=True,
        check=False,
        env=build_cargo_fuzz_env(toolchain=toolchain, coverage_dir=coverage_dir),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "stdout.log").write_text(fuzz.stdout or "", encoding="utf-8")
    (output_dir / "stderr.log").write_text(fuzz.stderr or "", encoding="utf-8")
    emit_runtime_metric("cargo_fuzz_exit_code", value=fuzz.returncode, meta={"target_name": target_name})

    normalized_dir = normalize_campaign_output(corpus_dir=corpus_dir, artifacts_dir=artifacts_dir, output_dir=output_dir)
    libfuzzer_stats = parse_libfuzzer_stats((fuzz.stdout or "") + "\n" + (fuzz.stderr or ""))
    (normalized_dir / "execution.json").write_text(
        json.dumps(
            {
                "target_name": target_name,
                "exit_code": fuzz.returncode,
                "libfuzzer_stats": libfuzzer_stats,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    coverage_summary = {}
    raw_profraw_files = list((coverage_dir / "raw").glob("*.profraw"))
    if raw_profraw_files:
        try:
            llvm_profdata = resolve_llvm_tool("llvm-profdata", toolchain=toolchain)
            llvm_cov = resolve_llvm_tool("llvm-cov", toolchain=toolchain)
            target_binary = resolve_fuzz_target_binary(
                working_dir=working_dir,
                fuzz_dir=fuzz_dir,
                target_name=target_name,
                toolchain=toolchain,
            )
            if target_binary is not None:
                coverage_summary = merge_coverage_profiles(
                    coverage_dir=coverage_dir,
                    target_binary=target_binary,
                    llvm_profdata=llvm_profdata,
                    llvm_cov=llvm_cov,
                )
        except FileNotFoundError:
            coverage_summary = {}
    artifact_summary = summarize_campaign_output(normalized_dir)
    triage_summary = build_triage_summary(artifact_summary)
    for key, value in (
        ("queue_count", artifact_summary["queue_count"]),
        ("crash_count", artifact_summary["crash_count"]),
        ("interesting_case_count", triage_summary["interesting_case_count"]),
    ):
        emit_runtime_metric(key, value=value, meta={"collector": "cargo_fuzz_artifact_summary"})
    executed_units = libfuzzer_stats.get("number_of_executed_units")
    if executed_units is None:
        executed_units = libfuzzer_stats.get("number_of_executed_units_estimate")
    if executed_units is not None:
        emit_runtime_metric(
            "executed_units",
            value=executed_units,
            meta={"collector": "cargo_fuzz_libfuzzer_stats"},
        )
    if coverage_summary:
        for key in ("covered_functions", "covered_lines", "covered_regions"):
            emit_runtime_metric(key, value=coverage_summary[key], meta={"collector": "cargo_fuzz_coverage"})

    summary_path = None
    bundle_run_dir = run_dir()
    if bundle_run_dir is not None:
        summary_path = export_campaign_artifacts_with_metadata(
            output_dir=normalized_dir,
            bundle_run_dir=bundle_run_dir,
            target_implementation=target_implementation,
            replay_harness=replay_harness,
            replay_target_id=replay_target_id,
            replay_targets=replay_targets,
        )

    emit_target_event(
        primitive=PRIMITIVE,
        event="campaign_completed",
        payload={
            "exit_code": fuzz.returncode,
            "target_name": target_name,
            "artifact_summary": {
                "queue_count": artifact_summary["queue_count"],
                "crash_count": artifact_summary["crash_count"],
                "interesting_case_count": triage_summary["interesting_case_count"],
                "summary_path": str(summary_path) if summary_path else None,
            },
        },
        level="info" if fuzz.returncode == 0 else "warning",
    )
    print(json.dumps({"target_name": target_name, "output_dir": str(output_dir)}, sort_keys=True))
    if fuzz.stdout:
        print(fuzz.stdout, end="")
    if fuzz.stderr:
        print(fuzz.stderr, end="", file=sys.stderr)
    return fuzz.returncode


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--working-dir", required=True)
    parser.add_argument("--fuzz-dir", required=True)
    parser.add_argument("--target-name", required=True)
    parser.add_argument("--seed-dir", dest="seed_dirs", action="append", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seconds", type=int, required=True)
    parser.add_argument("--toolchain", default=DEFAULT_TOOLCHAIN)
    parser.add_argument("--dict-path")
    parser.add_argument("--libfuzzer-arg", dest="extra_libfuzzer_args", action="append", default=[])
    parser.add_argument("--target-implementation", default="amaru")
    parser.add_argument("--replay-harness", required=True)
    parser.add_argument("--replay-target-id", required=True)
    parser.add_argument("--replay-target", dest="replay_targets", action="append", required=True)
    args = parser.parse_args(argv)
    return run_campaign_with_metadata(
        working_dir=Path(args.working_dir),
        fuzz_dir=Path(args.fuzz_dir),
        target_name=args.target_name,
        seed_dirs=[Path(path) for path in args.seed_dirs],
        output_dir=Path(args.output_dir),
        seconds=args.seconds,
        target_implementation=args.target_implementation,
        replay_harness=args.replay_harness,
        replay_target_id=args.replay_target_id,
        replay_targets=args.replay_targets,
        toolchain=args.toolchain,
        dict_path_override=Path(args.dict_path) if args.dict_path else None,
        extra_libfuzzer_args=list(args.extra_libfuzzer_args),
    )


if __name__ == "__main__":
    raise SystemExit(main())
