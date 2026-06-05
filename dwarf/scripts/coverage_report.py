#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_telemetry import emit_target_event  # noqa: E402


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def infer_runs_dir(explicit_runs_dir: str | None) -> Path | None:
    if explicit_runs_dir:
        return Path(explicit_runs_dir)
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir).parent
    env_runs_dir = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env_runs_dir:
        return Path(env_runs_dir)
    return None


def _default_output_dir() -> Path:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir) / "outputs" / "coverage-report"
    return Path.cwd() / "outputs" / "coverage-report"


def _relative_artifact_path(artifact_path: Path) -> str:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        try:
            return str(artifact_path.relative_to(Path(run_dir)))
        except ValueError:
            pass
    parts = artifact_path.parts
    if "outputs" in parts:
        index = parts.index("outputs")
        return str(Path(*parts[index:]))
    return str(artifact_path)


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _target_label(entry: dict) -> str:
    if entry.get("subcampaign_id"):
        return f"{entry['bundle_id']}:{entry['subcampaign_id']}"
    return str(entry.get("bundle_id", "<unknown>"))


def _coverage_metric(entry: dict) -> tuple[str, float]:
    if entry.get("coverage_count") is not None:
        return "coverage_count", float(entry.get("coverage_count") or 0.0)
    if entry.get("bitmap_cvg") is not None:
        return "bitmap_cvg", float(entry.get("bitmap_cvg") or 0.0)
    if entry.get("feature_count") is not None:
        return "feature_count", float(entry.get("feature_count") or 0.0)
    return "none", 0.0


def build_coverage_summary(*, aggregate_report: dict, target_run_id: str, aggregate_bundle_id: str) -> dict:
    targets = []
    for entry in aggregate_report.get("entries") or []:
        metric, score = _coverage_metric(entry)
        targets.append(
            {
                "target_label": _target_label(entry),
                "engine": entry.get("engine"),
                "bundle_id": entry.get("bundle_id"),
                "subcampaign_id": entry.get("subcampaign_id"),
                "queue_count": int(entry.get("queue_count", 0)),
                "crash_count": int(entry.get("crash_count", 0)),
                "hang_count": int(entry.get("hang_count", 0)),
                "exec_count": int(entry.get("exec_count", 0)),
                "exec_rate": entry.get("exec_rate"),
                "bitmap_cvg": entry.get("bitmap_cvg"),
                "coverage_count": entry.get("coverage_count"),
                "feature_count": entry.get("feature_count"),
                "coverage_metric": metric,
                "coverage_score": score,
                "source_type": entry.get("source_type"),
                "source_path": entry.get("source_path"),
            }
        )
    targets.sort(key=lambda item: (item["coverage_score"], item["queue_count"], item["exec_count"]), reverse=True)
    return {
        "schema_version": "v1",
        "merge_mode": "stat-only",
        "generated_at_utc": utc_timestamp(),
        "target_run_id": target_run_id,
        "aggregate_bundle_id": aggregate_bundle_id,
        "bundle_count": int(aggregate_report.get("bundle_count", 0)),
        "entry_count": int(aggregate_report.get("entry_count", 0)),
        "target_count": len(targets),
        "totals": aggregate_report.get("totals") or {},
        "targets": targets,
    }


def _render_markdown(summary: dict) -> str:
    lines = [
        "# Coverage Report",
        "",
        f"- Aggregate bundle: {summary['aggregate_bundle_id']}",
        f"- Targets summarized: {summary['target_count']}",
        f"- Total queue entries: {summary['totals'].get('queue_count')}",
        f"- Total execs: {summary['totals'].get('exec_count')}",
        f"- Max bitmap coverage: {summary['totals'].get('max_bitmap_cvg')}",
        f"- Max feature count: {summary['totals'].get('max_feature_count')}",
        f"- Novel queue SHA256 count: {summary['totals'].get('novel_queue_sha256_count')}",
        "",
        "| target | engine | coverage_metric | coverage_score | queue | execs | crashes | hangs |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for target in summary["targets"]:
        lines.append(
            f"| {target['target_label']} | {target['engine']} | {target['coverage_metric']} | "
            f"{target['coverage_score']} | {target['queue_count']} | {target['exec_count']} | "
            f"{target['crash_count']} | {target['hang_count']} |"
        )
    return "\n".join(lines) + "\n"


def _render_html(summary: dict) -> str:
    max_score = max((float(item["coverage_score"]) for item in summary["targets"]), default=0.0)

    rows = []
    for target in summary["targets"]:
        score = float(target["coverage_score"])
        width = 0 if max_score <= 0 else int((score / max_score) * 100)
        rows.append(
            "<tr>"
            f"<td>{html.escape(target['target_label'])}</td>"
            f"<td>{html.escape(str(target['engine']))}</td>"
            f"<td>{html.escape(target['coverage_metric'])}</td>"
            f"<td><div class='bar-shell'><div class='bar-fill' style='width:{width}%'></div></div><span class='score'>{score}</span></td>"
            f"<td>{target['queue_count']}</td>"
            f"<td>{target['exec_count']}</td>"
            f"<td>{target['crash_count']}</td>"
            f"<td>{target['hang_count']}</td>"
            "</tr>"
        )

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Dwarf Coverage Report</title>"
        "<style>"
        "body{font-family:system-ui,-apple-system,sans-serif;margin:24px;color:#111;background:#fff;}"
        "h1,h2{margin:0 0 12px 0;} .cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0 24px 0;}"
        ".card{border:1px solid #ddd;border-radius:6px;padding:12px;background:#fafafa;}"
        ".card .label{font-size:12px;color:#666;margin-bottom:4px;} .card .value{font-size:22px;font-weight:600;}"
        "table{border-collapse:collapse;width:100%;font-size:14px;} th,td{border:1px solid #ddd;padding:8px;vertical-align:middle;text-align:left;}"
        "th{background:#f4f4f4;} .bar-shell{display:inline-block;width:140px;height:10px;background:#eee;border-radius:999px;margin-right:8px;vertical-align:middle;}"
        ".bar-fill{height:10px;background:#2f7ef7;border-radius:999px;} .score{font-variant-numeric:tabular-nums;}"
        "</style></head><body>"
        "<h1>Coverage Report</h1>"
        f"<p>Aggregate bundle <code>{html.escape(summary['aggregate_bundle_id'])}</code></p>"
        "<div class='cards'>"
        f"<div class='card'><div class='label'>Targets</div><div class='value'>{summary['target_count']}</div></div>"
        f"<div class='card'><div class='label'>Queue</div><div class='value'>{summary['totals'].get('queue_count')}</div></div>"
        f"<div class='card'><div class='label'>Execs</div><div class='value'>{summary['totals'].get('exec_count')}</div></div>"
        f"<div class='card'><div class='label'>Novel Queue SHA256</div><div class='value'>{summary['totals'].get('novel_queue_sha256_count')}</div></div>"
        "</div>"
        "<h2>Per-target ranking</h2>"
        "<table><thead><tr><th>target</th><th>engine</th><th>coverage metric</th><th>coverage score</th><th>queue</th><th>execs</th><th>crashes</th><th>hangs</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        "</body></html>"
    )


def _collect_afl_queue_inputs(default_dir: Path, *, max_inputs: int) -> list[Path]:
    queue_dir = default_dir / "queue"
    inputs = [
        path
        for path in sorted(queue_dir.glob("*"))
        if path.is_file() and not path.name.startswith(".") and "/.state/" not in str(path)
    ]
    return inputs[:max_inputs]


def _extract_slug_from_scenario_id(scenario_id: str) -> str | None:
    match = re.match(r"amaru-cargo-fuzz-(.+)-aflpp(?:-.+)?-smoke$", scenario_id)
    if match:
        return match.group(1)
    return None


def _resolve_afl_binary_for_bundle(run_dir: Path, slug: str) -> Path | None:
    candidates = [
        DWARF_ROOT / "targets" / "amaru" / "target" / "release" / f"amaru-afl-{slug}",
        DWARF_ROOT / "targets" / f"amaru-cargo-fuzz-{slug}" / "target" / "release" / f"amaru-afl-{slug}",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _parse_afl_showmap_output(path: Path) -> set[int]:
    edges = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        edge_text, _count_text = line.split(":", 1)
        try:
            edges.add(int(edge_text, 16))
        except ValueError:
            continue
    return edges


def _edge_fingerprint(edges: set[int]) -> str:
    payload = "\n".join(f"{edge:06x}" for edge in sorted(edges)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _run_afl_showmap(binary: Path, input_path: Path) -> tuple[int, set[int], str]:
    with tempfile.NamedTemporaryFile(prefix="dwarf-afl-showmap-", suffix=".txt", delete=False) as handle:
        output_path = Path(handle.name)
    try:
        proc = subprocess.run(
            ["afl-showmap", "-q", "-o", str(output_path), "--", str(binary), str(input_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        stderr = proc.stderr or ""
        edges = _parse_afl_showmap_output(output_path) if output_path.exists() else set()
        return int(proc.returncode), edges, stderr
    finally:
        output_path.unlink(missing_ok=True)


def _render_file_level_markdown(report: dict) -> str:
    lines = [
        "# Coverage Report File-Level Merge",
        "",
        f"- Runs dir: {report['runs_dir']}",
        f"- Bundles requested: {report['requested_bundle_count']}",
        f"- Bundles processed: {report['processed_bundle_count']}",
        f"- AFL++ bundles processed: {report['processed_aflpp_bundle_count']}",
        f"- libFuzzer bundles processed: {report['processed_libfuzzer_bundle_count']}",
        f"- Inputs processed: {report['inputs_processed']}",
        f"- Total unique edges: {report['total_unique_edges']}",
        f"- Covered functions: {report['covered_functions']}",
        f"- Covered lines: {report['covered_lines']}",
        f"- Covered regions: {report['covered_regions']}",
        f"- Tool availability: afl-showmap={report['tool_availability']['afl_showmap']}",
        "",
        "| bundle | engine | target | queue | inputs | unique_edges | covered_lines | covered_regions | novel_edges | status |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for bundle in report["bundles"]:
        lines.append(
            f"| {bundle['bundle_id']} | {bundle.get('engine')} | {bundle.get('target_slug') or bundle.get('subcampaign_id')} | {bundle.get('queue_count', 0)} | "
            f"{bundle.get('input_count', 0)} | {bundle.get('unique_edges_observed', 0)} | "
            f"{bundle.get('covered_lines', 0)} | {bundle.get('covered_regions', 0)} | "
            f"{bundle.get('novel_edge_count', 0)} | {bundle.get('status')} |"
        )
    return "\n".join(lines) + "\n"


def _collect_aflpp_bundle_records(
    *,
    runs_dir: Path,
    aflpp_bundle_ids: list[str],
    max_inputs_per_bundle: int,
) -> tuple[list[dict], dict[str, set[int]], dict]:
    bundle_records = []
    bundle_edge_sets: dict[str, set[int]] = {}
    stats = {
        "processed_aflpp_bundle_count": 0,
        "inputs_processed": 0,
        "tool_availability": {
            "afl_showmap": shutil.which("afl-showmap") is not None,
        },
    }
    if not stats["tool_availability"]["afl_showmap"]:
        return bundle_records, bundle_edge_sets, stats

    bundle_edge_sets: dict[str, set[int]] = {}
    for bundle_id in aflpp_bundle_ids:
        run_dir = runs_dir / bundle_id
        manifest_path = run_dir / "manifest.json"
        summary_path = run_dir / "outputs" / "aflpp" / "summary.json"
        bundle_record = {
            "bundle_id": bundle_id,
            "engine": "aflpp",
            "status": "skipped",
            "queue_count": 0,
            "input_count": 0,
            "unique_edges_observed": 0,
            "covered_functions": 0,
            "covered_lines": 0,
            "covered_regions": 0,
            "novel_edge_count": 0,
            "bitmap_fingerprint": None,
        }
        if not manifest_path.is_file() or not summary_path.is_file():
            bundle_record["skip_reason"] = "missing_bundle_artifacts"
            bundle_records.append(bundle_record)
            continue
        manifest = _load_json(manifest_path)
        summary = _load_json(summary_path)
        scenario_id = ((manifest.get("scenario") or {}).get("id")) or ""
        target_slug = _extract_slug_from_scenario_id(scenario_id)
        bundle_record["target_slug"] = target_slug
        bundle_record["queue_count"] = int(summary.get("queue_count", 0))
        if not target_slug:
            bundle_record["skip_reason"] = "unrecognized_scenario_id"
            bundle_records.append(bundle_record)
            continue
        binary = _resolve_afl_binary_for_bundle(run_dir, target_slug)
        if binary is None:
            bundle_record["skip_reason"] = "missing_target_binary"
            bundle_records.append(bundle_record)
            continue
        default_dir = Path(summary["default_dir"])
        inputs = _collect_afl_queue_inputs(default_dir, max_inputs=max_inputs_per_bundle)
        bundle_record["target_binary"] = str(binary)
        bundle_record["input_count"] = len(inputs)
        if not inputs:
            bundle_record["skip_reason"] = "no_queue_inputs"
            bundle_records.append(bundle_record)
            continue

        edge_union: set[int] = set()
        showmap_failures = 0
        for input_path in inputs:
            exit_code, edges, _stderr = _run_afl_showmap(binary, input_path)
            if exit_code not in {0, 1, 2}:
                showmap_failures += 1
                continue
            edge_union.update(edges)
        if not edge_union and showmap_failures == len(inputs):
            bundle_record["skip_reason"] = "afl_showmap_failed"
            bundle_records.append(bundle_record)
            continue

        bundle_record["status"] = "ok"
        bundle_record["unique_edges_observed"] = len(edge_union)
        bundle_record["bitmap_fingerprint"] = _edge_fingerprint(edge_union)
        stats["processed_aflpp_bundle_count"] += 1
        stats["inputs_processed"] += len(inputs)
        bundle_edge_sets[bundle_id] = edge_union
        bundle_records.append(bundle_record)

    return bundle_records, bundle_edge_sets, stats


def _collect_cargo_fuzz_campaign_bundle_records(
    *,
    runs_dir: Path,
    cargo_fuzz_campaign_bundle_ids: list[str],
) -> tuple[list[dict], dict]:
    bundle_records = []
    stats = {
        "processed_libfuzzer_bundle_count": 0,
        "covered_functions": 0,
        "covered_lines": 0,
        "covered_regions": 0,
    }
    for bundle_id in cargo_fuzz_campaign_bundle_ids:
        run_dir = runs_dir / bundle_id
        subcampaign_root = run_dir / "outputs" / "fuzz-campaign" / "subcampaigns"
        if not subcampaign_root.is_dir():
            bundle_records.append(
                {
                    "bundle_id": bundle_id,
                    "engine": "cargo-fuzz",
                    "status": "skipped",
                    "skip_reason": "missing_fuzz_campaign_subcampaigns",
                    "queue_count": 0,
                    "input_count": 0,
                    "unique_edges_observed": 0,
                    "covered_functions": 0,
                    "covered_lines": 0,
                    "covered_regions": 0,
                    "novel_edge_count": 0,
                }
            )
            continue
        subcampaign_dirs = sorted(path for path in subcampaign_root.iterdir() if path.is_dir())
        for subcampaign_dir in subcampaign_dirs:
            coverage_path = subcampaign_dir / "coverage" / "coverage.json"
            summary_path = subcampaign_dir / "summary.json"
            record = {
                "bundle_id": bundle_id,
                "engine": "cargo-fuzz",
                "subcampaign_id": subcampaign_dir.name,
                "status": "skipped",
                "queue_count": 0,
                "input_count": 0,
                "unique_edges_observed": 0,
                "covered_functions": 0,
                "covered_lines": 0,
                "covered_regions": 0,
                "novel_edge_count": 0,
            }
            if summary_path.is_file():
                summary = _load_json(summary_path)
                record["queue_count"] = int(summary.get("queue_count", 0))
            if not coverage_path.is_file():
                record["skip_reason"] = "missing_coverage_json"
                bundle_records.append(record)
                continue
            coverage = _load_json(coverage_path)
            record["status"] = "ok"
            record["profraw_count"] = int(coverage.get("profraw_count", 0))
            record["target_binary"] = coverage.get("target_binary")
            record["covered_functions"] = int(coverage.get("covered_functions", 0))
            record["covered_lines"] = int(coverage.get("covered_lines", 0))
            record["covered_regions"] = int(coverage.get("covered_regions", 0))
            bundle_records.append(record)
            stats["processed_libfuzzer_bundle_count"] += 1
            stats["covered_functions"] += record["covered_functions"]
            stats["covered_lines"] += record["covered_lines"]
            stats["covered_regions"] += record["covered_regions"]

    return bundle_records, stats


def _build_file_level_mixed_report(
    *,
    runs_dir: Path,
    aflpp_bundle_ids: list[str],
    cargo_fuzz_campaign_bundle_ids: list[str],
    max_inputs_per_bundle: int,
) -> dict:
    report = {
        "schema_version": "v1",
        "merge_mode": "file-level",
        "generated_at_utc": utc_timestamp(),
        "runs_dir": str(runs_dir),
        "requested_bundle_count": len(aflpp_bundle_ids) + len(cargo_fuzz_campaign_bundle_ids),
        "processed_bundle_count": 0,
        "processed_aflpp_bundle_count": 0,
        "processed_libfuzzer_bundle_count": 0,
        "inputs_processed": 0,
        "total_unique_edges": 0,
        "covered_functions": 0,
        "covered_lines": 0,
        "covered_regions": 0,
        "tool_availability": {
            "afl_showmap": shutil.which("afl-showmap") is not None,
        },
        "bundles": [],
    }
    aflpp_records, bundle_edge_sets, aflpp_stats = _collect_aflpp_bundle_records(
        runs_dir=runs_dir,
        aflpp_bundle_ids=aflpp_bundle_ids,
        max_inputs_per_bundle=max_inputs_per_bundle,
    )
    cargo_records, cargo_stats = _collect_cargo_fuzz_campaign_bundle_records(
        runs_dir=runs_dir,
        cargo_fuzz_campaign_bundle_ids=cargo_fuzz_campaign_bundle_ids,
    )
    report["tool_availability"] = aflpp_stats["tool_availability"]
    report["bundles"].extend(aflpp_records)
    report["bundles"].extend(cargo_records)

    total_union: set[int] = set()
    for edges in bundle_edge_sets.values():
        total_union.update(edges)
    report["total_unique_edges"] = len(total_union)
    report["processed_aflpp_bundle_count"] = aflpp_stats["processed_aflpp_bundle_count"]
    report["processed_libfuzzer_bundle_count"] = cargo_stats["processed_libfuzzer_bundle_count"]
    report["processed_bundle_count"] = (
        report["processed_aflpp_bundle_count"] + report["processed_libfuzzer_bundle_count"]
    )
    report["inputs_processed"] = aflpp_stats["inputs_processed"]
    report["covered_functions"] = cargo_stats["covered_functions"]
    report["covered_lines"] = cargo_stats["covered_lines"]
    report["covered_regions"] = cargo_stats["covered_regions"]

    for bundle in report["bundles"]:
        edges = bundle_edge_sets.get(bundle["bundle_id"])
        if edges is None:
            continue
        others = set()
        for other_bundle_id, other_edges in bundle_edge_sets.items():
            if other_bundle_id == bundle["bundle_id"]:
                continue
            others.update(other_edges)
        bundle["novel_edge_count"] = len(edges - others)

    return report


def run_coverage_report(
    *,
    runs_dir: Path,
    aggregate_bundle_id: str | None,
    output_dir: Path,
    merge_mode: str = "stat-only",
    aflpp_bundle_ids: list[str] | None = None,
    cargo_fuzz_campaign_bundle_ids: list[str] | None = None,
    max_inputs_per_bundle: int = 25,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    if merge_mode == "file-level":
        report = _build_file_level_mixed_report(
            runs_dir=runs_dir,
            aflpp_bundle_ids=aflpp_bundle_ids or [],
            cargo_fuzz_campaign_bundle_ids=cargo_fuzz_campaign_bundle_ids or [],
            max_inputs_per_bundle=max_inputs_per_bundle,
        )
        report_path = output_dir / "coverage-report-file-level.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        markdown_path = output_dir / "coverage-file-level.md"
        markdown_path.write_text(_render_file_level_markdown(report), encoding="utf-8")
        return {
            "merge_mode": merge_mode,
            "coverage_file_level_report_relpath": _relative_artifact_path(report_path),
            "coverage_file_level_markdown_relpath": _relative_artifact_path(markdown_path),
            "processed_bundle_count": report["processed_bundle_count"],
            "requested_bundle_count": report["requested_bundle_count"],
            "inputs_processed": report["inputs_processed"],
            "total_unique_edges": report["total_unique_edges"],
            "processed_aflpp_bundle_count": report["processed_aflpp_bundle_count"],
            "processed_libfuzzer_bundle_count": report["processed_libfuzzer_bundle_count"],
            "covered_functions": report["covered_functions"],
            "covered_lines": report["covered_lines"],
            "covered_regions": report["covered_regions"],
            "tool_availability": report["tool_availability"],
        }

    if not aggregate_bundle_id:
        raise ValueError("aggregate_bundle_id is required for stat-only mode")
    aggregate_report_path = runs_dir / aggregate_bundle_id / "outputs" / "aggregate-coverage" / "coverage-report.json"
    aggregate_report = _load_json(aggregate_report_path)
    summary = build_coverage_summary(
        aggregate_report=aggregate_report,
        target_run_id=aggregate_bundle_id,
        aggregate_bundle_id=aggregate_bundle_id,
    )
    summary_path = output_dir / "coverage-summary.json"
    markdown_path = output_dir / "coverage.md"
    html_path = output_dir / "coverage.html"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_render_markdown(summary), encoding="utf-8")
    html_path.write_text(_render_html(summary), encoding="utf-8")
    return {
        "merge_mode": merge_mode,
        "aggregate_bundle_id": aggregate_bundle_id,
        "aggregate_report_relpath": _relative_artifact_path(aggregate_report_path),
        "coverage_summary_relpath": _relative_artifact_path(summary_path),
        "coverage_markdown_relpath": _relative_artifact_path(markdown_path),
        "coverage_html_relpath": _relative_artifact_path(html_path),
        "target_count": summary["target_count"],
        "entry_count": summary["entry_count"],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Render an operator-readable coverage report from aggregate coverage output")
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--aggregate-bundle-id", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--merge-mode", choices=("stat-only", "file-level"), default="stat-only")
    parser.add_argument("--aflpp-bundle-id", action="append", dest="aflpp_bundle_ids", default=[])
    parser.add_argument("--cargo-fuzz-campaign-bundle-id", action="append", dest="cargo_fuzz_campaign_bundle_ids", default=[])
    parser.add_argument("--max-inputs-per-bundle", type=int, default=25)
    args = parser.parse_args(argv[1:])

    runs_dir = infer_runs_dir(args.runs_dir)
    if runs_dir is None:
        print("missing runs dir", file=sys.stderr)
        return 1
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()

    emit_target_event(
        primitive="runtime_coverage_report",
        event="coverage_report_started",
        payload={
            "runs_dir": str(runs_dir),
            "aggregate_bundle_id": args.aggregate_bundle_id,
            "output_dir": str(output_dir),
            "merge_mode": args.merge_mode,
            "aflpp_bundle_ids": list(args.aflpp_bundle_ids),
            "cargo_fuzz_campaign_bundle_ids": list(args.cargo_fuzz_campaign_bundle_ids),
            "max_inputs_per_bundle": args.max_inputs_per_bundle,
        },
    )
    result = run_coverage_report(
        runs_dir=runs_dir,
        aggregate_bundle_id=args.aggregate_bundle_id,
        output_dir=output_dir,
        merge_mode=args.merge_mode,
        aflpp_bundle_ids=list(args.aflpp_bundle_ids),
        cargo_fuzz_campaign_bundle_ids=list(args.cargo_fuzz_campaign_bundle_ids),
        max_inputs_per_bundle=int(args.max_inputs_per_bundle),
    )
    emit_target_event(
        primitive="runtime_coverage_report",
        event="coverage_report_completed",
        payload=result,
    )
    if args.merge_mode == "file-level":
        print(
            "merge_mode=file-level processed_bundle_count={processed_bundle_count} "
            "inputs_processed={inputs_processed} total_unique_edges={total_unique_edges} "
            "covered_lines={covered_lines} "
            "coverage_file_level_report_relpath={coverage_file_level_report_relpath}".format(**result)
        )
    else:
        print(
            "aggregate_bundle_id={aggregate_bundle_id} target_count={target_count} "
            "coverage_html_relpath={coverage_html_relpath}".format(**result)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
