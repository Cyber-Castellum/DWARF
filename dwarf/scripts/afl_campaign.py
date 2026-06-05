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


PRIMITIVE = "afl_campaign"
CASE_NAME_RE = re.compile(r"(?P<key>[^:,]+):(?P<value>[^,]+)")


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


def build_afl_fuzz_command(*, seed_dir: Path, output_dir: Path, target_binary: Path, seconds: int) -> list[str]:
    return [
        "cargo",
        "afl",
        "fuzz",
        "-i",
        str(seed_dir),
        "-o",
        str(output_dir),
        "-V",
        str(seconds),
        str(target_binary),
    ]


def build_afl_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    fuzz_env = dict(base_env or os.environ.copy())
    fuzz_env.setdefault("AFL_NO_UI", "1")
    fuzz_env.setdefault("AFL_SKIP_CPUFREQ", "1")
    fuzz_env.setdefault("AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES", "1")
    return fuzz_env


def _build_command(*, bin_name: str) -> list[str]:
    return ["cargo", "afl", "build", "--release", "--bin", bin_name]


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
        "queue_count": len(queue_entries),
        "crash_count": len(crash_entries),
        "hang_count": len(hang_entries),
        "queue_entries": queue_entries,
        "crash_entries": crash_entries,
        "hang_entries": hang_entries,
    }


def export_campaign_artifacts(output_dir: Path, bundle_run_dir: Path) -> Path:
    return export_campaign_artifacts_with_metadata(
        output_dir=output_dir,
        bundle_run_dir=bundle_run_dir,
        target_implementation="amaru",
        replay_harness="amaru-afl-tx-body",
        replay_target_id="amaru-cbor-decode-tx-body",
        replay_targets=["amaru", "cardano-node"],
    )


def export_campaign_artifacts_with_metadata(
    *,
    output_dir: Path,
    bundle_run_dir: Path,
    target_implementation: str,
    replay_harness: str,
    replay_target_id: str,
    replay_targets: list[str],
) -> Path:
    destination = bundle_run_dir / "outputs" / "afl"
    default_src = output_dir / "default"
    default_dst = destination / "default"
    destination.mkdir(parents=True, exist_ok=True)
    default_dst.mkdir(parents=True, exist_ok=True)

    stats_path = default_src / "fuzzer_stats"
    if stats_path.is_file():
        shutil.copy2(stats_path, default_dst / "fuzzer_stats")

    for directory in ("queue", "crashes", "hangs"):
        src = default_src / directory
        dst = default_dst / directory
        if dst.exists():
            shutil.rmtree(dst)
        if src.is_dir():
            shutil.copytree(src, dst)

    summary = summarize_campaign_output(output_dir)
    summary["bundle_artifact_dir"] = str(destination)
    summary_path = destination / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    triage = build_triage_summary(summary)
    triage_path = destination / "triage.json"
    triage_path.write_text(json.dumps(triage, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    state_dir = testcase_lifecycle.default_state_dir_for_run(bundle_run_dir)
    testcase_records = testcase_lifecycle.build_testcase_records(
        run_id=bundle_run_dir.name,
        producer="afl",
        target_implementation=target_implementation,
        triage=triage,
        source_root="outputs/afl/default",
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


def _emit_stats(stats: dict) -> None:
    for key in ("execs_done", "execs_per_sec", "saved_crashes", "saved_hangs", "corpus_count", "cycles_done"):
        if key in stats:
            emit_runtime_metric(key, value=stats[key], meta={"collector": "afl_fuzzer_stats"})


def run_campaign(*, working_dir: Path, bin_name: str, seed_dir: Path, output_dir: Path, seconds: int) -> int:
    return run_campaign_with_metadata(
        working_dir=working_dir,
        bin_name=bin_name,
        seed_dir=seed_dir,
        output_dir=output_dir,
        seconds=seconds,
        target_implementation="amaru",
        replay_harness=bin_name,
        replay_target_id="amaru-cbor-decode-tx-body",
        replay_targets=["amaru", "cardano-node"],
    )


def run_campaign_with_metadata(
    *,
    working_dir: Path,
    bin_name: str,
    seed_dir: Path,
    output_dir: Path,
    seconds: int,
    target_implementation: str,
    replay_harness: str,
    replay_target_id: str,
    replay_targets: list[str],
) -> int:
    target_binary = working_dir / "target" / "release" / bin_name
    if shutil.which("cargo") is None:
        raise SystemExit("cargo not found in PATH")
    if shutil.which("afl-fuzz") is None and shutil.which("cargo-afl") is None:
        raise SystemExit("AFL++ tooling not found in PATH")

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
            "seed_dir": str(seed_dir),
            "output_dir": str(output_dir),
            "seconds": seconds,
        },
    )

    build = subprocess.run(
        _build_command(bin_name=bin_name),
        cwd=working_dir,
        text=True,
        capture_output=True,
        check=False,
        env=os.environ.copy(),
    )
    emit_runtime_metric("build_exit_code", value=build.returncode, meta={"bin": bin_name})
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

    output_dir.mkdir(parents=True, exist_ok=True)
    fuzz_env = build_afl_env()
    fuzz = subprocess.run(
        build_afl_fuzz_command(seed_dir=seed_dir, output_dir=output_dir, target_binary=target_binary, seconds=seconds),
        cwd=working_dir,
        text=True,
        capture_output=True,
        check=False,
        env=fuzz_env,
    )

    stats = _read_stats(output_dir)
    artifact_summary = summarize_campaign_output(output_dir)
    _emit_stats(stats)
    for key, value in (
        ("queue_count", artifact_summary["queue_count"]),
        ("crash_count", artifact_summary["crash_count"]),
        ("hang_count", artifact_summary["hang_count"]),
    ):
        emit_runtime_metric(key, value=value, meta={"collector": "afl_artifact_summary"})
    triage_summary = build_triage_summary(artifact_summary)
    for key, value in (
        ("queue_testcase_count", triage_summary["queue_testcase_count"]),
        ("queue_coverage_case_count", triage_summary["queue_coverage_case_count"]),
        ("interesting_case_count", triage_summary["interesting_case_count"]),
    ):
        emit_runtime_metric(key, value=value, meta={"collector": "afl_triage"})

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
                "queue_coverage_case_count": triage_summary["queue_coverage_case_count"],
                "interesting_case_count": triage_summary["interesting_case_count"],
                "summary_path": str(summary_path) if summary_path else None,
            },
        },
        level="info" if fuzz.returncode == 0 else "error",
    )
    print(json.dumps({"bin": bin_name, "stats": stats, "output_dir": str(output_dir)}, sort_keys=True))
    if fuzz.stdout:
        print(fuzz.stdout, end="")
    if fuzz.stderr:
        print(fuzz.stderr, end="", file=os.sys.stderr)
    return fuzz.returncode


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--working-dir", required=True)
    parser.add_argument("--bin", dest="bin_name", required=True)
    parser.add_argument("--seed-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seconds", type=int, required=True)
    parser.add_argument("--target-implementation", default="amaru")
    parser.add_argument("--replay-harness", required=True)
    parser.add_argument("--replay-target-id", required=True)
    parser.add_argument("--replay-target", dest="replay_targets", action="append", required=True)
    args = parser.parse_args(argv)
    return run_campaign_with_metadata(
        working_dir=Path(args.working_dir),
        bin_name=args.bin_name,
        seed_dir=Path(args.seed_dir),
        output_dir=Path(args.output_dir),
        seconds=args.seconds,
        target_implementation=args.target_implementation,
        replay_harness=args.replay_harness,
        replay_target_id=args.replay_target_id,
        replay_targets=args.replay_targets,
    )


if __name__ == "__main__":
    raise SystemExit(main())
