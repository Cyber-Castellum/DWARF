#!/usr/bin/env python3

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import aflpp_campaign
import cargo_fuzz_campaign


def allocate_seconds(*, total_seconds_budget: int, subcampaign_count: int) -> list[int]:
    if subcampaign_count <= 0:
        raise ValueError("subcampaign_count must be positive")
    base = total_seconds_budget // subcampaign_count
    remainder = total_seconds_budget % subcampaign_count
    return [base + (1 if index < remainder else 0) for index in range(subcampaign_count)]


def build_subcampaign_command(spec: dict, *, seconds: int) -> list[str]:
    engine = spec["engine"]
    if engine == "cargo-fuzz":
        command = [
            "python3",
            str(SCRIPT_DIR / "cargo_fuzz_campaign.py"),
            "--working-dir",
            spec["working_dir"],
            "--fuzz-dir",
            spec["fuzz_dir"],
            "--target-name",
            spec["target_name"],
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
        if spec.get("toolchain"):
            command.extend(["--toolchain", spec["toolchain"]])
        if spec.get("dict_path"):
            command.extend(["--dict-path", spec["dict_path"]])
        for arg in spec.get("extra_libfuzzer_args", []):
            command.append(f"--libfuzzer-arg={arg}")
        return command
    if engine == "aflpp":
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
            ]
        )
        if spec.get("dict_path"):
            command.extend(["--dict-path", spec["dict_path"]])
        if spec.get("sanitizer"):
            command.extend(["--sanitizer", spec["sanitizer"]])
        if spec.get("target_triple"):
            command.extend(["--target-triple", spec["target_triple"]])
        command.extend(
            [
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
        return command
    raise ValueError(f"unsupported engine: {engine}")


def _child_env(spec: dict) -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "ADA2_DWARF_RUN_DIR",
        "ADA2_DWARF_TARGET_EVENT_LOG",
        "ADA2_DWARF_RUNTIME_METRICS_DIR",
    ):
        env.pop(key, None)
    if spec.get("rustup_toolchain"):
        env["RUSTUP_TOOLCHAIN"] = str(spec["rustup_toolchain"])
    return env


def _load_subcampaign_summary(spec: dict) -> tuple[dict, dict]:
    output_dir = Path(spec["output_dir"])
    engine = spec["engine"]
    if engine == "cargo-fuzz":
        summary = cargo_fuzz_campaign.summarize_campaign_output(output_dir)
        execution_path = output_dir / "execution.json"
        stats = {}
        if execution_path.is_file():
            body = json.loads(execution_path.read_text(encoding="utf-8"))
            stats = body.get("libfuzzer_stats") or {}
        return summary, stats
    if engine == "aflpp":
        summary = aflpp_campaign.summarize_campaign_output(output_dir)
        stats = aflpp_campaign._read_stats(output_dir)
        return summary, stats
    raise ValueError(f"unsupported engine: {engine}")


def run_subcampaign(spec: dict, *, seconds: int) -> dict:
    output_dir = Path(spec["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    command = build_subcampaign_command(spec, seconds=seconds)
    proc = subprocess.run(
        command,
        cwd=spec["working_dir"],
        text=True,
        capture_output=True,
        check=False,
        env=_child_env(spec),
    )
    summary, stats = _load_subcampaign_summary(spec)
    result = {
        "id": spec["id"],
        "engine": spec["engine"],
        "seconds_budget": seconds,
        "exit_code": proc.returncode,
        "summary": summary,
        "stats": stats,
        "stdout_tail": (proc.stdout or "")[-4096:],
        "stderr_tail": (proc.stderr or "")[-4096:],
        "sub_output_dir": str(output_dir),
    }
    (output_dir / "subcampaign-result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def _combine_corpus(*, subcampaign_results: list[dict], combined_corpus_dir: Path) -> int:
    if combined_corpus_dir.exists():
        shutil.rmtree(combined_corpus_dir)
    combined_corpus_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for result in subcampaign_results:
        queue_dir = Path(result["sub_output_dir"]) / "default" / "queue"
        if not queue_dir.is_dir():
            continue
        for item in sorted(path for path in queue_dir.rglob("*") if path.is_file()):
            destination = combined_corpus_dir / f"{result['id']}__{item.name}"
            shutil.copy2(item, destination)
            count += 1
    return count


def aggregate_campaign_results(config: dict, subcampaign_results: list[dict]) -> dict:
    total_queue_count = 0
    total_crash_count = 0
    total_hang_count = 0
    total_afl_execs_done = 0
    total_libfuzzer_units = 0
    total_afl_execs_per_sec = 0.0
    aflpp_count = 0

    entries = []
    for result in subcampaign_results:
        summary = result.get("summary") or {}
        stats = result.get("stats") or {}
        total_queue_count += int(summary.get("queue_count", 0))
        total_crash_count += int(summary.get("crash_count", 0))
        total_hang_count += int(summary.get("hang_count", 0))
        if result["engine"] == "aflpp":
            total_afl_execs_done += int(stats.get("execs_done", 0))
            total_afl_execs_per_sec += float(stats.get("execs_per_sec", 0.0))
            aflpp_count += 1
        elif result["engine"] == "cargo-fuzz":
            total_libfuzzer_units += int(
                stats.get("number_of_executed_units", stats.get("number_of_executed_units_estimate", 0))
            )
        entries.append(
            {
                "id": result["id"],
                "engine": result["engine"],
                "seconds_budget": result["seconds_budget"],
                "exit_code": result["exit_code"],
                "queue_count": int(summary.get("queue_count", 0)),
                "crash_count": int(summary.get("crash_count", 0)),
                "hang_count": int(summary.get("hang_count", 0)),
                "stats": stats,
                "sub_output_dir": result["sub_output_dir"],
            }
        )

    return {
        "campaign_id": config["campaign_id"],
        "subcampaign_count": len(subcampaign_results),
        "subcampaigns": entries,
        "total_queue_count": total_queue_count,
        "total_crash_count": total_crash_count,
        "total_hang_count": total_hang_count,
        "total_afl_execs_done": total_afl_execs_done,
        "average_afl_execs_per_sec": (total_afl_execs_per_sec / aflpp_count) if aflpp_count else 0.0,
        "total_libfuzzer_units": total_libfuzzer_units,
    }


def run_campaign(config: dict, runner=None) -> Path:
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    subcampaigns = list(config["subcampaigns"])
    budgets = allocate_seconds(
        total_seconds_budget=int(config["total_seconds_budget"]),
        subcampaign_count=len(subcampaigns),
    )
    runner = runner or run_subcampaign
    results = []

    for spec, seconds in zip(subcampaigns, budgets):
        spec = dict(spec)
        spec["output_dir"] = str(output_dir / "subcampaigns" / spec["id"])
        result = dict(runner(spec, seconds=seconds))
        result.setdefault("id", spec["id"])
        result.setdefault("engine", spec["engine"])
        result.setdefault("seconds_budget", seconds)
        result.setdefault("sub_output_dir", spec["output_dir"])
        results.append(result)

    aggregated = aggregate_campaign_results(config, results)
    combined_corpus_dir = output_dir / "combined-corpus"
    combined_corpus_count = _combine_corpus(
        subcampaign_results=results,
        combined_corpus_dir=combined_corpus_dir,
    )
    aggregated["combined_corpus_count"] = combined_corpus_count
    aggregated_stats_path = output_dir / "aggregated-stats.json"
    aggregated_stats_path.write_text(json.dumps(aggregated, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = {
        "campaign_id": config["campaign_id"],
        "total_seconds_budget": int(config["total_seconds_budget"]),
        "completed_subcampaigns": sum(1 for result in results if result["exit_code"] == 0),
        "failed_subcampaigns": sum(1 for result in results if result["exit_code"] != 0),
        "total_queue_count": aggregated["total_queue_count"],
        "total_crash_count": aggregated["total_crash_count"],
        "total_hang_count": aggregated["total_hang_count"],
        "combined_corpus_count": combined_corpus_count,
        "throughput": {
            "total_afl_execs_done": aggregated["total_afl_execs_done"],
            "average_afl_execs_per_sec": aggregated["average_afl_execs_per_sec"],
            "total_libfuzzer_units": aggregated["total_libfuzzer_units"],
        },
        "subcampaigns": aggregated["subcampaigns"],
    }
    report_path = output_dir / "campaign-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report_path = run_campaign(config)
    print(json.dumps({"campaign_report": str(report_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
