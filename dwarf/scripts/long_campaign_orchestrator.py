#!/usr/bin/env python3

import argparse
import json
import tarfile
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import fuzz_campaign_orchestrator


def allocate_round_seconds(*, total_seconds_budget: int, checkpoint_seconds: int) -> list[int]:
    if total_seconds_budget <= 0:
        raise ValueError("total_seconds_budget must be positive")
    if checkpoint_seconds <= 0:
        raise ValueError("checkpoint_seconds must be positive")

    rounds = []
    remaining = total_seconds_budget
    while remaining > 0:
        rounds.append(min(checkpoint_seconds, remaining))
        remaining -= checkpoint_seconds
    return rounds


def _checkpoint_coverage_from_aggregated(aggregated: dict) -> dict:
    max_bitmap_cvg = 0.0
    max_feature_count = 0
    total_afl_execs_done = int(aggregated.get("total_afl_execs_done", 0))
    total_libfuzzer_units = int(aggregated.get("total_libfuzzer_units", 0))
    total_queue_count = int(aggregated.get("total_queue_count", 0))

    for entry in aggregated.get("subcampaigns", []):
        stats = entry.get("stats") or {}
        bitmap_raw = stats.get("bitmap_cvg")
        if isinstance(bitmap_raw, str) and bitmap_raw.endswith("%"):
            try:
                max_bitmap_cvg = max(max_bitmap_cvg, float(bitmap_raw[:-1]))
            except ValueError:
                pass
        feature_count = stats.get("feature_count")
        if isinstance(feature_count, (int, float)):
            max_feature_count = max(max_feature_count, int(feature_count))

    return {
        "total_queue_count": total_queue_count,
        "total_afl_execs_done": total_afl_execs_done,
        "total_libfuzzer_units": total_libfuzzer_units,
        "max_bitmap_cvg": max_bitmap_cvg,
        "max_feature_count": max_feature_count,
    }


def _write_queue_snapshot(*, source_dir: Path, archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as archive:
        if not source_dir.is_dir():
            return
        for item in sorted(path for path in source_dir.rglob("*") if path.is_file()):
            archive.add(item, arcname=item.relative_to(source_dir).as_posix())


def run_long_campaign(config: dict, runner=None) -> Path:
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoints_dir = output_dir / "checkpoints"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)

    runner = runner or fuzz_campaign_orchestrator.run_campaign
    round_budgets = allocate_round_seconds(
        total_seconds_budget=int(config["total_seconds_budget"]),
        checkpoint_seconds=int(config["checkpoint_seconds"]),
    )

    checkpoint_entries = []
    totals = {
        "total_queue_count": 0,
        "total_crash_count": 0,
        "total_hang_count": 0,
        "total_afl_execs_done": 0,
        "total_libfuzzer_units": 0,
        "max_bitmap_cvg": 0.0,
        "max_feature_count": 0,
    }

    for index, seconds in enumerate(round_budgets, start=1):
        round_output_dir = output_dir / "rounds" / f"round-{index:02d}"
        round_config = {
            "campaign_id": f"{config['campaign_id']}-round-{index:02d}",
            "output_dir": str(round_output_dir),
            "total_seconds_budget": seconds,
            "subcampaigns": list(config["subcampaigns"]),
            "round_index": index,
            "checkpoint_seconds": int(config["checkpoint_seconds"]),
        }
        report_path = Path(runner(round_config))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        aggregated_path = round_output_dir / "aggregated-stats.json"
        aggregated = json.loads(aggregated_path.read_text(encoding="utf-8"))
        checkpoint_dir = checkpoints_dir / str(index)
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        stats_path = checkpoint_dir / "stats.json"
        stats_path.write_text(json.dumps(aggregated, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        coverage = _checkpoint_coverage_from_aggregated(aggregated)
        coverage_path = checkpoint_dir / "coverage.json"
        coverage_path.write_text(json.dumps(coverage, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        queue_archive_path = checkpoint_dir / "queue-snapshot.tar.gz"
        _write_queue_snapshot(source_dir=round_output_dir / "combined-corpus", archive_path=queue_archive_path)

        checkpoint_entries.append(
            {
                "index": index,
                "seconds_budget": seconds,
                "campaign_id": round_config["campaign_id"],
                "report_path": str(report_path),
                "stats_path": str(stats_path),
                "coverage_path": str(coverage_path),
                "queue_snapshot_path": str(queue_archive_path),
                "completed_subcampaigns": int(report.get("completed_subcampaigns", 0)),
                "failed_subcampaigns": int(report.get("failed_subcampaigns", 0)),
                "subcampaign_exit_status": "clean" if int(report.get("failed_subcampaigns", 0)) == 0 else "nonzero_exit",
                "total_queue_count": int(report.get("total_queue_count", 0)),
                "total_crash_count": int(report.get("total_crash_count", 0)),
                "total_hang_count": int(report.get("total_hang_count", 0)),
                "coverage": coverage,
            }
        )

        totals["total_queue_count"] += int(report.get("total_queue_count", 0))
        totals["total_crash_count"] += int(report.get("total_crash_count", 0))
        totals["total_hang_count"] += int(report.get("total_hang_count", 0))
        totals["total_afl_execs_done"] += int(coverage["total_afl_execs_done"])
        totals["total_libfuzzer_units"] += int(coverage["total_libfuzzer_units"])
        totals["max_bitmap_cvg"] = max(totals["max_bitmap_cvg"], float(coverage["max_bitmap_cvg"]))
        totals["max_feature_count"] = max(totals["max_feature_count"], int(coverage["max_feature_count"]))

    report = {
        "campaign_id": config["campaign_id"],
        "total_seconds_budget": int(config["total_seconds_budget"]),
        "checkpoint_seconds": int(config["checkpoint_seconds"]),
        "checkpoint_count": len(checkpoint_entries),
        "completed_checkpoints": len(checkpoint_entries),
        "failed_checkpoints": 0,
        "checkpoints_with_nonzero_subcampaigns": sum(
            1 for entry in checkpoint_entries if entry["failed_subcampaigns"] != 0
        ),
        "checkpoints": checkpoint_entries,
        "totals": totals,
    }
    report_path = output_dir / "campaign-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report_path = run_long_campaign(config)
    print(json.dumps({"campaign_report": str(report_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
