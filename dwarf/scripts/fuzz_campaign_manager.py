#!/usr/bin/env python3

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from profile_manager.fuzz import find_fuzz_test, fuzz_v1_scenario_bytes


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def campaign_id_for(fuzz_id: str) -> str:
    return f"{utc_now().strftime('%Y%m%dT%H%M%SZ')}-{fuzz_id}"


def read_ndjson_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def parse_run_id(text: str) -> str | None:
    match = re.search(r"run_id:\s+([A-Za-z0-9T\-Z]+)", text or "")
    return match.group(1) if match else None


def lifecycle_state_summary(state_root: Path) -> dict:
    testcase_root = state_root / "testcases"
    return {
        "testcase_count": len(read_ndjson_rows(testcase_root / "index.ndjson")),
        "bucket_count": len(read_ndjson_rows(testcase_root / "buckets.ndjson")),
        "replay_queue_count": len(read_ndjson_rows(testcase_root / "replay-queue.ndjson")),
        "compare_queue_count": len(read_ndjson_rows(testcase_root / "compare-queue.ndjson")),
    }


def child_run_summary(remote_root: Path, run_id: str) -> dict:
    bundle = remote_root / "runs" / run_id
    summary_path = bundle / "outputs" / "cargo-fuzz" / "summary.json"
    summary = {}
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "run_id": run_id,
        "bundle_path": str(bundle),
        "summary_path": str(summary_path) if summary_path.is_file() else None,
        "queue_count": int(summary.get("queue_count", 0)),
        "crash_count": int(summary.get("crash_count", 0)),
        "hang_count": int(summary.get("hang_count", 0)),
        "executed_units": summary.get("libfuzzer_stats", {}).get("number_of_executed_units")
        or summary.get("libfuzzer_stats", {}).get("number_of_executed_units_estimate"),
        "interesting_case_count": summary.get("triage", {}).get("interesting_case_count"),
    }


def write_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_ndjson(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def copy_bucket_snapshot(state_root: Path, destination: Path) -> dict:
    src = state_root / "testcases" / "buckets.ndjson"
    rows = read_ndjson_rows(src)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if src.is_file():
        shutil.copy2(src, destination)
    else:
        destination.write_text("", encoding="utf-8")
    return {"bucket_count": len(rows)}


def run_child_scenario(
    *,
    remote_root: Path,
    scenario_bytes: bytes,
    fuzz_id: str,
    campaign_dir: Path,
    child_index: int,
    simulate_interrupt_after_seconds: int | None = None,
) -> dict:
    scenario_dir = campaign_dir / "generated-scenarios"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    scenario_path = scenario_dir / f"{fuzz_id}-child-{child_index:03d}.json"
    scenario_path.write_bytes(scenario_bytes)
    argv = ["./cardano-profile", "scenario", "run", str(scenario_path)]
    started = time.time()
    proc = subprocess.Popen(
        argv,
        cwd=remote_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    interrupted = False
    if simulate_interrupt_after_seconds is not None:
        time.sleep(simulate_interrupt_after_seconds)
        if proc.poll() is None:
            proc.terminate()
            interrupted = True
    stdout, stderr = proc.communicate()
    finished = time.time()
    return {
        "returncode": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "run_id": parse_run_id(stdout),
        "started_epoch": started,
        "finished_epoch": finished,
        "interrupted": interrupted,
        "duration_seconds": finished - started,
    }


def run_campaign(
    *,
    remote_root: Path,
    fuzz_id: str,
    duration_seconds: int,
    checkpoint_seconds: int,
    child_seconds: int,
    retry_budget: int,
    campaign_root: Path,
    simulate_interrupt_once_after_seconds: int | None = None,
) -> int:
    fuzz = find_fuzz_test(fuzz_id)
    campaign_id = campaign_id_for(fuzz_id)
    campaign_dir = campaign_root / campaign_id
    campaign_dir.mkdir(parents=True, exist_ok=True)
    state_root = remote_root / "state"
    manifest_path = campaign_dir / "campaign-manifest.json"
    checkpoints_path = campaign_dir / "campaign-checkpoints.ndjson"
    summary_path = campaign_dir / "campaign-summary.json"
    buckets_path = campaign_dir / "campaign-buckets.ndjson"
    children_path = campaign_dir / "campaign-child-runs.ndjson"

    write_json(
        manifest_path,
        {
            "campaign_id": campaign_id,
            "fuzz_id": fuzz_id,
            "duration_seconds": duration_seconds,
            "checkpoint_seconds": checkpoint_seconds,
            "child_seconds": child_seconds,
            "retry_budget": retry_budget,
            "remote_root": str(remote_root),
            "campaign_dir": str(campaign_dir),
            "started_at": utc_now().isoformat(),
            "simulate_interrupt_once_after_seconds": simulate_interrupt_once_after_seconds,
        },
    )

    elapsed_seconds = 0.0
    child_index = 0
    retries_used = 0
    total_executed_units = 0
    completed_runs = 0
    failed_runs = 0
    interrupt_budget = simulate_interrupt_once_after_seconds
    checkpoint_count = 0

    while elapsed_seconds < duration_seconds:
        child_index += 1
        remaining = max(1, int(duration_seconds - elapsed_seconds))
        slice_seconds = min(child_seconds, checkpoint_seconds, remaining)
        child = run_child_scenario(
            remote_root=remote_root,
            scenario_bytes=fuzz_v1_scenario_bytes(fuzz, seconds_override=slice_seconds),
            fuzz_id=fuzz_id,
            campaign_dir=campaign_dir,
            child_index=child_index,
            simulate_interrupt_after_seconds=interrupt_budget,
        )
        interrupt_budget = None
        run_summary = child_run_summary(remote_root, child["run_id"]) if child["run_id"] else {}
        executed_units = run_summary.get("executed_units")
        if isinstance(executed_units, int):
            total_executed_units += executed_units
        state_summary = lifecycle_state_summary(state_root)
        status = "ok" if child["returncode"] == 0 else ("interrupted" if child["interrupted"] else "failed")
        if child["returncode"] == 0:
            completed_runs += 1
        else:
            failed_runs += 1
        checkpoint = {
            "campaign_id": campaign_id,
            "checkpoint_index": checkpoint_count + 1,
            "ts": utc_now().isoformat(),
            "status": status,
            "child_index": child_index,
            "child_seconds": slice_seconds,
            "child_returncode": child["returncode"],
            "child_run_id": child.get("run_id"),
            "child_interrupted": child["interrupted"],
            "child_duration_seconds": child["duration_seconds"],
            "queue_count": run_summary.get("queue_count"),
            "crash_count": run_summary.get("crash_count"),
            "executed_units": executed_units,
            "total_executed_units": total_executed_units if total_executed_units else None,
            "lifecycle_state": state_summary,
        }
        append_ndjson(checkpoints_path, checkpoint)
        append_ndjson(children_path, {**checkpoint, "stdout": child["stdout"], "stderr": child["stderr"]})
        checkpoint_count += 1
        elapsed_seconds += max(float(child["duration_seconds"]), 0.0)
        if child["returncode"] != 0:
            if retries_used < retry_budget:
                retries_used += 1
                continue
            break

    bucket_summary = copy_bucket_snapshot(state_root, buckets_path)
    final_state = lifecycle_state_summary(state_root)
    summary = {
        "campaign_id": campaign_id,
        "fuzz_id": fuzz_id,
        "campaign_dir": str(campaign_dir),
        "duration_seconds_requested": duration_seconds,
        "checkpoint_seconds": checkpoint_seconds,
        "child_seconds": child_seconds,
        "retry_budget": retry_budget,
        "retries_used": retries_used,
        "completed_runs": completed_runs,
        "failed_runs": failed_runs,
        "checkpoint_count": checkpoint_count,
        "bucket_summary": bucket_summary,
        "final_lifecycle_state": final_state,
        "total_executed_units": total_executed_units if total_executed_units else None,
        "finished_at": utc_now().isoformat(),
    }
    write_json(summary_path, summary)
    print(json.dumps(summary, sort_keys=True))
    return 0 if completed_runs > 0 and failed_runs <= retry_budget else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fuzz-id", required=True)
    parser.add_argument("--duration-seconds", type=int, required=True)
    parser.add_argument("--checkpoint-seconds", type=int, default=900)
    parser.add_argument("--child-seconds", type=int, default=900)
    parser.add_argument("--retry-budget", type=int, default=1)
    parser.add_argument("--campaign-root", required=True)
    parser.add_argument("--simulate-interrupt-once-after-seconds", type=int)
    args = parser.parse_args(argv)
    return run_campaign(
        remote_root=REPO_ROOT,
        fuzz_id=args.fuzz_id,
        duration_seconds=args.duration_seconds,
        checkpoint_seconds=args.checkpoint_seconds,
        child_seconds=args.child_seconds,
        retry_budget=args.retry_budget,
        campaign_root=Path(args.campaign_root),
        simulate_interrupt_once_after_seconds=args.simulate_interrupt_once_after_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
