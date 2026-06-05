#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import aflpp_campaign
import cargo_fuzz_campaign


def _normalize_bundle_id(run_dir: Path, runs_root: Path) -> str:
    try:
        return str(run_dir.relative_to(runs_root))
    except ValueError:
        return run_dir.name


def _parse_bitmap_cvg(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def _max_libfuzzer_cov(stderr_log_path: Path) -> tuple[int | None, int | None]:
    cov_max = None
    feature_max = None
    if not stderr_log_path.is_file():
        return cov_max, feature_max
    for line in stderr_log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if " cov: " not in line and "\tcov:" not in line:
            continue
        parts = line.replace("\t", " ").split()
        for index, token in enumerate(parts):
            if token == "cov:" and index + 1 < len(parts):
                try:
                    value = int(parts[index + 1])
                    cov_max = value if cov_max is None or value > cov_max else cov_max
                except ValueError:
                    pass
            if token == "ft:" and index + 1 < len(parts):
                try:
                    value = int(parts[index + 1])
                    feature_max = value if feature_max is None or value > feature_max else feature_max
                except ValueError:
                    pass
    return cov_max, feature_max


def _load_aflpp_bundle(run_dir: Path, *, runs_root: Path) -> dict | None:
    summary_path = run_dir / "outputs" / "aflpp" / "summary.json"
    if not summary_path.is_file():
        return None
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    stats = {}
    fuzzer_stats_path = run_dir / "outputs" / "aflpp" / "default" / "fuzzer_stats"
    if fuzzer_stats_path.is_file():
        stats = aflpp_campaign.parse_fuzzer_stats(fuzzer_stats_path.read_text(encoding="utf-8", errors="replace"))
    queue_entries = summary.get("queue_entries") or []
    return {
        "source_type": "bundle",
        "bundle_id": _normalize_bundle_id(run_dir, runs_root),
        "engine": "aflpp",
        "target": "unknown",
        "queue_count": int(summary.get("queue_count", 0)),
        "crash_count": int(summary.get("crash_count", 0)),
        "hang_count": int(summary.get("hang_count", 0)),
        "exec_count": int(stats.get("execs_done", 0)),
        "exec_rate": float(stats.get("execs_per_sec", 0.0)),
        "bitmap_cvg": _parse_bitmap_cvg(stats.get("bitmap_cvg")),
        "feature_count": None,
        "queue_sha256s": sorted(
            entry["sha256"] for entry in queue_entries if isinstance(entry, dict) and entry.get("sha256")
        ),
        "source_path": str(run_dir),
    }


def _load_cargo_fuzz_bundle(run_dir: Path, *, runs_root: Path) -> dict | None:
    summary_path = run_dir / "outputs" / "cargo-fuzz" / "summary.json"
    if not summary_path.is_file():
        return None
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    stats = summary.get("libfuzzer_stats") or {}
    queue_entries = summary.get("queue_entries") or []
    cov_count, feature_count = _max_libfuzzer_cov(run_dir / "outputs" / "cargo-fuzz" / "stderr.log")
    if feature_count is None and "feature_count" in stats:
        feature_count = int(stats["feature_count"])
    return {
        "source_type": "bundle",
        "bundle_id": _normalize_bundle_id(run_dir, runs_root),
        "engine": "cargo-fuzz",
        "target": "unknown",
        "queue_count": int(summary.get("queue_count", 0)),
        "crash_count": int(summary.get("crash_count", 0)),
        "hang_count": int(summary.get("hang_count", 0)),
        "exec_count": int(stats.get("number_of_executed_units", stats.get("number_of_executed_units_estimate", 0))),
        "exec_rate": float(stats.get("average_exec_per_sec", 0.0)),
        "bitmap_cvg": None,
        "coverage_count": cov_count,
        "feature_count": feature_count,
        "queue_sha256s": sorted(
            entry["sha256"] for entry in queue_entries if isinstance(entry, dict) and entry.get("sha256")
        ),
        "source_path": str(run_dir),
    }


def _load_campaign_bundle(run_dir: Path, *, runs_root: Path) -> list[dict]:
    report_path = run_dir / "outputs" / "fuzz-campaign" / "campaign-report.json"
    if not report_path.is_file():
        return []
    report = json.loads(report_path.read_text(encoding="utf-8"))
    entries = []
    for subcampaign in report.get("subcampaigns") or []:
        queue_sha256s = []
        summary = {}
        sub_output_dir = Path(subcampaign["sub_output_dir"])
        result_path = sub_output_dir / "subcampaign-result.json"
        if result_path.is_file():
            body = json.loads(result_path.read_text(encoding="utf-8"))
            summary = body.get("summary") or {}
            queue_sha256s = sorted(
                entry["sha256"]
                for entry in (summary.get("queue_entries") or [])
                if isinstance(entry, dict) and entry.get("sha256")
            )
        entries.append(
            {
                "source_type": "campaign-subcampaign",
                "bundle_id": _normalize_bundle_id(run_dir, runs_root),
                "campaign_id": report.get("campaign_id"),
                "subcampaign_id": subcampaign["id"],
                "engine": subcampaign["engine"],
                "target": "unknown",
                "queue_count": int(subcampaign.get("queue_count", 0)),
                "crash_count": int(subcampaign.get("crash_count", 0)),
                "hang_count": int(subcampaign.get("hang_count", 0)),
                "exec_count": int(
                    subcampaign.get("stats", {}).get(
                        "execs_done",
                        subcampaign.get("stats", {}).get(
                            "number_of_executed_units",
                            subcampaign.get("stats", {}).get("number_of_executed_units_estimate", 0),
                        ),
                    )
                ),
                "exec_rate": float(
                    subcampaign.get("stats", {}).get(
                        "execs_per_sec",
                        subcampaign.get("stats", {}).get("average_exec_per_sec", 0.0),
                    )
                ),
                "bitmap_cvg": _parse_bitmap_cvg(subcampaign.get("stats", {}).get("bitmap_cvg")),
                "coverage_count": None,
                "feature_count": subcampaign.get("stats", {}).get("feature_count"),
                "queue_sha256s": queue_sha256s,
                "source_path": str(sub_output_dir),
            }
        )
    return entries


def _load_bundle_entries(*, runs_root: Path, bundle_ids: list[str], campaign_bundle_ids: list[str]) -> tuple[list[dict], list[str]]:
    entries = []
    skipped = []

    for bundle_id in bundle_ids:
        run_dir = runs_root / bundle_id
        loaded = _load_aflpp_bundle(run_dir, runs_root=runs_root)
        if loaded is None:
            loaded = _load_cargo_fuzz_bundle(run_dir, runs_root=runs_root)
        if loaded is None:
            skipped.append(bundle_id)
            continue
        entries.append(loaded)

    for bundle_id in campaign_bundle_ids:
        run_dir = runs_root / bundle_id
        loaded = _load_campaign_bundle(run_dir, runs_root=runs_root)
        if not loaded:
            skipped.append(bundle_id)
            continue
        entries.extend(loaded)

    return entries, skipped


def _roll_up(entries: list[dict]) -> dict:
    queue_sha256s = set()
    total_queue_count = 0
    total_exec_count = 0
    max_bitmap_cvg = None
    max_feature_count = None

    for entry in entries:
        total_queue_count += int(entry.get("queue_count", 0))
        total_exec_count += int(entry.get("exec_count", 0))
        bitmap_cvg = entry.get("bitmap_cvg")
        if bitmap_cvg is not None:
            max_bitmap_cvg = bitmap_cvg if max_bitmap_cvg is None or bitmap_cvg > max_bitmap_cvg else max_bitmap_cvg
        feature_count = entry.get("feature_count")
        if feature_count is not None:
            max_feature_count = (
                int(feature_count)
                if max_feature_count is None or int(feature_count) > max_feature_count
                else max_feature_count
            )
        for sha256 in entry.get("queue_sha256s") or []:
            queue_sha256s.add(sha256)

    return {
        "queue_count": total_queue_count,
        "exec_count": total_exec_count,
        "max_bitmap_cvg": max_bitmap_cvg,
        "max_feature_count": max_feature_count,
        "novel_queue_sha256_count": len(queue_sha256s),
    }


def _write_markdown_summary(*, output_dir: Path, report: dict) -> Path:
    lines = [
        "# Aggregate Coverage Summary",
        "",
        f"- Bundles: {report['bundle_count']}",
        f"- Entries: {report['entry_count']}",
        f"- Total queue: {report['totals']['queue_count']}",
        f"- Total execs: {report['totals']['exec_count']}",
        f"- Max bitmap coverage: {report['totals']['max_bitmap_cvg']}",
        f"- Max feature count: {report['totals']['max_feature_count']}",
        f"- Novel queue SHA256 count: {report['totals']['novel_queue_sha256_count']}",
        "",
        "| source | engine | queue | execs | bitmap_cvg | feature_count |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for entry in report["entries"]:
        source = entry["bundle_id"]
        if entry.get("subcampaign_id"):
            source = f"{source}:{entry['subcampaign_id']}"
        lines.append(
            f"| {source} | {entry['engine']} | {entry['queue_count']} | {entry['exec_count']} | "
            f"{entry.get('bitmap_cvg')} | {entry.get('feature_count')} |"
        )
    path = output_dir / "coverage-summary.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def aggregate_coverage(
    *,
    runs_root: Path,
    bundle_ids: list[str],
    campaign_bundle_ids: list[str],
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    entries, skipped = _load_bundle_entries(
        runs_root=runs_root,
        bundle_ids=bundle_ids,
        campaign_bundle_ids=campaign_bundle_ids,
    )
    report = {
        "bundle_count": len(bundle_ids) + len(campaign_bundle_ids),
        "entry_count": len(entries),
        "bundle_ids": list(bundle_ids),
        "campaign_bundle_ids": list(campaign_bundle_ids),
        "entries": entries,
        "skipped_bundle_ids": skipped,
        "totals": _roll_up(entries),
    }
    report_path = output_dir / "coverage-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_markdown_summary(output_dir=output_dir, report=report)
    return report_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--bundle-id", dest="bundle_ids", action="append", default=[])
    parser.add_argument("--campaign-bundle-id", dest="campaign_bundle_ids", action="append", default=[])
    args = parser.parse_args(argv)
    report_path = aggregate_coverage(
        runs_root=Path(args.runs_root),
        bundle_ids=list(args.bundle_ids),
        campaign_bundle_ids=list(args.campaign_bundle_ids),
        output_dir=Path(args.output_dir),
    )
    print(json.dumps({"coverage_report": str(report_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
