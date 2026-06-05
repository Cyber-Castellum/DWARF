#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
from itertools import combinations
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import aflpp_campaign


def build_subcampaign_command(spec: dict, *, seconds: int, rng_seed: int | None) -> list[str]:
    command = [
        "python3",
        str(SCRIPT_DIR / "aflpp_campaign.py"),
        "--working-dir",
        spec["working_dir"],
        "--bin",
        spec["bin"],
    ]
    for seed_dir in spec["seed_dirs"]:
        command.extend(["--seed-dir", seed_dir])
    command.extend(
        [
            "--output-dir",
            spec["output_dir"],
            "--seconds",
            str(seconds),
            "--target-implementation",
            spec.get("target_implementation", "amaru"),
            "--replay-harness",
            spec["replay_harness"],
            "--replay-target-id",
            spec["replay_target_id"],
        ]
    )
    for replay_target in spec["replay_targets"]:
        command.extend(["--replay-target", replay_target])
    if spec.get("dict_path"):
        command.extend(["--dict-path", spec["dict_path"]])
    if spec.get("sanitizer"):
        command.extend(["--sanitizer", spec["sanitizer"]])
    if spec.get("target_triple"):
        command.extend(["--target-triple", spec["target_triple"]])
    if rng_seed is not None:
        command.extend(["--rng-seed", str(rng_seed)])
    return command


def _child_env(spec: dict) -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "ADA2_DWARF_RUN_DIR",
        "ADA2_DWARF_TARGET_EVENT_LOG",
        "ADA2_DWARF_RUNTIME_METRICS_DIR",
        "ADA2_DWARF_METRICS_DIR",
        "ADA2_DWARF_EVENTS_DIR",
    ):
        env.pop(key, None)
    cargo_bin = str(Path.home() / ".cargo" / "bin")
    path_parts = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    if cargo_bin not in path_parts:
        path_parts.insert(0, cargo_bin)
        env["PATH"] = os.pathsep.join(path_parts)
    if spec.get("rustup_toolchain"):
        env["RUSTUP_TOOLCHAIN"] = str(spec["rustup_toolchain"])
    return env


def _queue_testcase_hashes(summary: dict) -> set[str]:
    hashes = set()
    for entry in summary.get("queue_entries", []):
        relative_path = entry.get("relative_path", "")
        if relative_path.startswith(".state/"):
            continue
        digest = entry.get("sha256")
        if digest:
            hashes.add(digest)
    return hashes


def _as_float(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.endswith("%"):
        text = text[:-1]
    return float(text)


def _bitmap_similarity_ratio(a: dict, b: dict) -> float:
    a_cvg = _as_float(a.get("bitmap_cvg", 0.0))
    b_cvg = _as_float(b.get("bitmap_cvg", 0.0))
    top = max(a_cvg, b_cvg)
    if top <= 0:
        return 1.0
    return min(a_cvg, b_cvg) / top


def _queue_overlap_ratio(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def run_subcampaign(spec: dict, *, seconds: int, rng_seed: int | None) -> dict:
    output_dir = Path(spec["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    command = build_subcampaign_command(spec, seconds=seconds, rng_seed=rng_seed)
    proc = subprocess.run(
        command,
        cwd=SCRIPT_DIR.parent,
        text=True,
        capture_output=True,
        check=False,
        env=_child_env(spec),
    )
    summary = aflpp_campaign.summarize_campaign_output(output_dir)
    stats = aflpp_campaign._read_stats(output_dir)
    result = {
        "id": spec["id"],
        "seconds_budget": seconds,
        "rng_seed": rng_seed,
        "exit_code": proc.returncode,
        "summary": summary,
        "stats": stats,
        "stdout_tail": (proc.stdout or "")[-4096:],
        "stderr_tail": (proc.stderr or "")[-4096:],
        "sub_output_dir": str(output_dir),
    }
    (output_dir / "stability-run-result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def _pairwise_report(results: list[dict]) -> list[dict]:
    pairwise = []
    for left, right in combinations(results, 2):
        left_hashes = _queue_testcase_hashes(left.get("summary") or {})
        right_hashes = _queue_testcase_hashes(right.get("summary") or {})
        pairwise.append(
            {
                "left_run_id": left["id"],
                "right_run_id": right["id"],
                "bitmap_similarity_ratio": _bitmap_similarity_ratio(left.get("stats") or {}, right.get("stats") or {}),
                "queue_overlap_ratio": _queue_overlap_ratio(left_hashes, right_hashes),
                "shared_queue_inputs": len(left_hashes & right_hashes),
                "union_queue_inputs": len(left_hashes | right_hashes),
            }
        )
    return pairwise


def run_stability(config: dict, runner=None) -> Path:
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = output_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    rerun_count = int(config["rerun_count"])
    seconds_per_run = int(config["seconds_per_run"])
    rng_seed = config.get("rng_seed")
    runner = runner or run_subcampaign

    results = []
    for index in range(rerun_count):
        spec = dict(config["campaign"])
        spec["id"] = f"run-{index + 1}"
        spec["output_dir"] = str(runs_dir / spec["id"])
        result = dict(runner(spec, seconds=seconds_per_run, rng_seed=rng_seed))
        result.setdefault("id", spec["id"])
        result.setdefault("seconds_budget", seconds_per_run)
        result.setdefault("rng_seed", rng_seed)
        result.setdefault("sub_output_dir", spec["output_dir"])
        results.append(result)

    pairwise = _pairwise_report(results)
    report = {
        "stability_id": config["stability_id"],
        "rerun_count": rerun_count,
        "seconds_per_run": seconds_per_run,
        "rng_seed": rng_seed,
        "successful_reruns": sum(1 for result in results if int(result.get("exit_code", 1)) == 0),
        "runs": [
            {
                "id": result["id"],
                "exit_code": result["exit_code"],
                "seconds_budget": result["seconds_budget"],
                "rng_seed": result.get("rng_seed"),
                "sub_output_dir": result["sub_output_dir"],
                "queue_count": int((result.get("summary") or {}).get("queue_count", 0)),
                "execs_done": int((result.get("stats") or {}).get("execs_done", 0)),
                "execs_per_sec": _as_float((result.get("stats") or {}).get("execs_per_sec", 0.0)),
                "bitmap_cvg": _as_float((result.get("stats") or {}).get("bitmap_cvg", 0.0)),
            }
            for result in results
        ],
        "pairwise": pairwise,
        "average_bitmap_similarity_ratio": (
            sum(item["bitmap_similarity_ratio"] for item in pairwise) / len(pairwise) if pairwise else 1.0
        ),
        "average_queue_overlap_ratio": (
            sum(item["queue_overlap_ratio"] for item in pairwise) / len(pairwise) if pairwise else 1.0
        ),
    }
    report_path = output_dir / "stability-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report_path = run_stability(config)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(
        " ".join(
            [
                "stability_completed=true",
                f"rerun_count={report['rerun_count']}",
                f"successful_reruns={report['successful_reruns']}",
            ]
        )
    )
    return 0 if report["successful_reruns"] >= 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
