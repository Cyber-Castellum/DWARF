#!/usr/bin/env python3

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(DWARF_ROOT) not in sys.path:
    sys.path.insert(0, str(DWARF_ROOT))

from bundle_chain_helpers import bundle_attestation_summary  # noqa: E402
import runtime_bundle_timeline  # noqa: E402
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
        return Path(run_dir) / "outputs" / "bundle-summary"
    return Path.cwd() / "outputs" / "bundle-summary"


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
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _collect_tag_index(runs_dir: Path) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for artifact_path in sorted(runs_dir.glob("*/outputs/bundle-tag/tags.json")):
        payload = _load_json(artifact_path)
        if not payload:
            continue
        target_run_id = payload.get("target_run_id")
        if not target_run_id:
            continue
        index.setdefault(str(target_run_id), []).append(dict(payload))
    return index


def _tags_for_run(tag_index: dict[str, list[dict]], run_id: str) -> list[str]:
    tags: set[str] = set()
    for record in tag_index.get(run_id, []):
        for tag in record.get("tags_added") or []:
            if tag:
                tags.add(str(tag))
    return sorted(tags)


def _scenario_family(scenario_id: str) -> str:
    if not scenario_id:
        return "unknown"
    parts = scenario_id.split("-")
    if len(parts) >= 3 and parts[0] == "runtime":
        return "-".join(parts[:3])
    if len(parts) >= 2:
        return "-".join(parts[:2])
    return scenario_id


def _extract_coverage_metrics(run_dir: Path) -> dict:
    summary = _load_json(run_dir / "outputs" / "coverage-report" / "coverage-summary.json")
    file_level = _load_json(run_dir / "outputs" / "coverage-report" / "coverage-report-file-level.json")
    metrics = {
        "coverage_score": None,
        "coverage_metric": None,
        "throughput_exec_rate_max": None,
        "crash_count": None,
        "file_level_unique_edges": None,
        "file_level_covered_functions": None,
        "file_level_covered_lines": None,
        "file_level_covered_regions": None,
    }
    if summary:
        targets = summary.get("targets") or []
        best = max(targets, key=lambda item: float(item.get("coverage_score") or 0.0), default=None)
        if best:
            metrics["coverage_score"] = best.get("coverage_score")
            metrics["coverage_metric"] = best.get("coverage_metric")
        exec_rates = [target.get("exec_rate") for target in targets if target.get("exec_rate") is not None]
        if exec_rates:
            metrics["throughput_exec_rate_max"] = max(exec_rates)
        crash_counts = [int(target.get("crash_count", 0)) for target in targets]
        metrics["crash_count"] = sum(crash_counts)
    if file_level:
        metrics["file_level_unique_edges"] = file_level.get("total_unique_edges")
        metrics["file_level_covered_functions"] = file_level.get("covered_functions")
        metrics["file_level_covered_lines"] = file_level.get("covered_lines")
        metrics["file_level_covered_regions"] = file_level.get("covered_regions")
    return metrics


def _extract_static_analysis_metrics(run_dir: Path) -> dict:
    tools = {}
    for tool in ("clippy", "audit", "deny"):
        payload = _load_json(run_dir / "outputs" / f"static-analysis-{tool}" / "findings.json")
        if not payload:
            continue
        tools[tool] = {
            "tool_status": payload.get("tool_status"),
            "findings_count": payload.get("findings_count"),
            "tool_exit_code": payload.get("tool_exit_code"),
        }
    return tools


def _extract_chain_metrics(run_dir: Path) -> dict:
    payload = _load_json(run_dir / "outputs" / "chain-verify" / "chain-verify-report.json")
    if not payload:
        return {"chain_verdict": None, "chain_length": None}
    return {
        "chain_verdict": payload.get("chain_verdict"),
        "chain_length": payload.get("chain_length"),
    }


def _extract_timeline_metrics(run_dir: Path) -> dict:
    payload = _load_json(run_dir / "outputs" / "bundle-timeline" / "timeline.json")
    if not payload:
        return {"signature_count": None, "event_count": None}
    summary = payload.get("summary") or {}
    return {
        "signature_count": summary.get("signature_count"),
        "event_count": summary.get("event_count"),
    }


def _extract_forensic_snapshot_metrics(run_dir: Path) -> dict:
    payload = _load_json(run_dir / "outputs" / "forensic-snapshot" / "snapshot-manifest.json")
    if not payload:
        return {"included_bundle_count": None}
    return {
        "included_bundle_count": payload.get("included_bundle_count"),
    }


def _extract_multi_node_metrics(run_dir: Path) -> dict:
    payload = _load_json(run_dir / "outputs" / "multi-node-observation" / "observation-summary.json")
    if not payload:
        return {
            "multi_node_node_count": None,
            "multi_node_responsive_node_count": None,
            "multi_node_tip_group_count": None,
            "multi_node_quorum_fraction": None,
            "multi_node_chain_select_consistent": None,
            "multi_node_impl_versions": [],
        }
    summary = payload.get("summary") or {}
    per_node = payload.get("per_node") or {}
    impl_versions = sorted(
        {
            f"{body.get('implementation') or 'unknown'}:{body.get('version') or 'unknown'}"
            for body in per_node.values()
        }
    )
    return {
        "multi_node_node_count": summary.get("node_count"),
        "multi_node_responsive_node_count": summary.get("responsive_node_count"),
        "multi_node_tip_group_count": summary.get("tip_group_count"),
        "multi_node_quorum_fraction": summary.get("quorum_fraction"),
        "multi_node_chain_select_consistent": summary.get("chain_select_consistent"),
        "multi_node_impl_versions": impl_versions,
    }


def _bundle_row(*, runs_dir: Path, run_id: str, tag_index: dict[str, list[dict]]) -> dict | None:
    run_dir = runs_dir / run_id
    manifest = _load_json(run_dir / "manifest.json")
    if not manifest:
        return None
    scenario = manifest.get("scenario") or {}
    scenario_id = str(scenario.get("id") or manifest.get("scenario_id") or "")
    attestation = bundle_attestation_summary(run_dir)
    signature_event = runtime_bundle_timeline._bundle_event(run_dir)
    hash_anchor = None
    for record in tag_index.get(run_id, []):
        hash_anchor = record.get("hash_anchor")
        if hash_anchor:
            break
    if not hash_anchor:
        hash_anchor = attestation.get("scenario_yaml_sha256")
    return {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "scenario_family": _scenario_family(scenario_id),
        "started_at": manifest.get("started_at"),
        "ended_at": manifest.get("ended_at"),
        "exit_status": manifest.get("exit_status"),
        "attestation_verdict": attestation.get("attestation_verdict"),
        "attestation_present": attestation.get("attestation_present"),
        "dwarf_source_sha256": attestation.get("dwarf_source_sha256"),
        "scenario_yaml_sha256": attestation.get("scenario_yaml_sha256"),
        "signing_key_fingerprint": attestation.get("signing_key_fingerprint"),
        "tooling_versions": attestation.get("tooling_versions"),
        "tags": _tags_for_run(tag_index, run_id),
        "hash_anchor": hash_anchor,
        "key_metrics": {
            **_extract_coverage_metrics(run_dir),
            "static_analysis": _extract_static_analysis_metrics(run_dir),
            **_extract_chain_metrics(run_dir),
            **_extract_timeline_metrics(run_dir),
            **_extract_forensic_snapshot_metrics(run_dir),
            **_extract_multi_node_metrics(run_dir),
        },
        "signature": signature_event,
    }


def _top_signatures(rows: list[dict]) -> list[dict]:
    counts: dict[str, dict] = {}
    for row in rows:
        event = row.get("signature")
        if not event:
            continue
        signature_id = event["signature_id"]
        bucket = counts.setdefault(
            signature_id,
            {
                "signature_id": signature_id,
                "signature_label": event["signature_label"],
                "scenario_id": event["scenario_id"],
                "exit_status": event["exit_status"],
                "evidence_tokens": list(event["evidence_tokens"]),
                "sightings_count": 0,
                "bundle_ids": [],
            },
        )
        bucket["sightings_count"] += 1
        bucket["bundle_ids"].append(row["run_id"])
    return sorted(counts.values(), key=lambda item: (-item["sightings_count"], item["signature_id"]))


def build_bundle_summary(*, runs_dir: Path, bundle_ids: list[str]) -> dict:
    tag_index = _collect_tag_index(runs_dir)
    rows = []
    for run_id in bundle_ids:
        row = _bundle_row(runs_dir=runs_dir, run_id=run_id, tag_index=tag_index)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda row: ((row.get("started_at") or ""), row["run_id"]))

    family_counts = Counter(row["scenario_family"] for row in rows)
    attested_count = sum(1 for row in rows if row.get("attestation_present"))
    verified_count = sum(1 for row in rows if row.get("attestation_verdict") == "verified")
    top_signatures = _top_signatures(rows)
    summary = {
        "total_bundle_count": len(rows),
        "by_scenario_family_counts": dict(sorted(family_counts.items())),
        "attestation_present_count": attested_count,
        "attestation_verified_count": verified_count,
        "attestation_coverage_pct": round((verified_count / len(rows)) * 100.0, 2) if rows else 0.0,
        "top_signatures": top_signatures[:5],
        "time_window": {
            "first_started_at": rows[0]["started_at"] if rows else None,
            "last_started_at": rows[-1]["started_at"] if rows else None,
        },
    }
    return {
        "schema_version": "v1",
        "generated_at_utc": utc_timestamp(),
        "input_bundle_ids": list(bundle_ids),
        "bundles": rows,
        "summary": summary,
    }


def _metric_display(value) -> str:
    return "n/a" if value is None else str(value)


def _render_markdown(payload: dict) -> str:
    summary = payload["summary"]
    lines = [
        "# Bundle Summary",
        "",
        f"- Bundles summarized: {summary['total_bundle_count']}",
        f"- Attestation verified: {summary['attestation_verified_count']}/{summary['total_bundle_count']}",
        f"- Attestation coverage pct: {summary['attestation_coverage_pct']}",
        f"- Time window start: {summary['time_window'].get('first_started_at')}",
        f"- Time window end: {summary['time_window'].get('last_started_at')}",
        "",
        "## By Scenario Family",
        "",
        "| family | count |",
        "| --- | ---: |",
    ]
    for family, count in summary["by_scenario_family_counts"].items():
        lines.append(f"| {family} | {count} |")
    lines.extend(
        [
            "",
            "## Per Bundle",
            "",
            "| run_id | scenario_id | exit | attestation | tags | coverage | throughput | crashes |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload["bundles"]:
        metrics = row["key_metrics"]
        lines.append(
            f"| {row['run_id']} | {row['scenario_id']} | {row['exit_status']} | {row['attestation_verdict']} | "
            f"{', '.join(row['tags']) or '-'} | {_metric_display(metrics.get('coverage_score'))} | "
            f"{_metric_display(metrics.get('throughput_exec_rate_max'))} | {_metric_display(metrics.get('crash_count'))} |"
        )
    lines.extend(
        [
            "",
            "## Top Signatures",
            "",
            "| signature_id | scenario_id | exit | sightings |",
            "| --- | --- | --- | ---: |",
        ]
    )
    for item in summary["top_signatures"]:
        lines.append(
            f"| {item['signature_id'][:12]} | {item['scenario_id']} | {item['exit_status']} | {item['sightings_count']} |"
        )
    return "\n".join(lines) + "\n"


def _render_html(payload: dict) -> str:
    summary = payload["summary"]
    family_rows = "".join(
        f"<tr><td>{html.escape(family)}</td><td>{count}</td></tr>"
        for family, count in summary["by_scenario_family_counts"].items()
    )
    bundle_rows = []
    for row in payload["bundles"]:
        metrics = row["key_metrics"]
        bundle_rows.append(
            "<tr>"
            f"<td>{html.escape(row['run_id'])}</td>"
            f"<td>{html.escape(row['scenario_id'])}</td>"
            f"<td>{html.escape(str(row['exit_status']))}</td>"
            f"<td>{html.escape(str(row['attestation_verdict']))}</td>"
            f"<td>{html.escape(', '.join(row['tags']) or '-')}</td>"
            f"<td>{html.escape(_metric_display(metrics.get('coverage_score')))}</td>"
            f"<td>{html.escape(_metric_display(metrics.get('throughput_exec_rate_max')))}</td>"
            f"<td>{html.escape(_metric_display(metrics.get('crash_count')))}</td>"
            "</tr>"
        )
    signature_rows = "".join(
        "<tr>"
        f"<td>{html.escape(item['signature_id'][:12])}</td>"
        f"<td>{html.escape(item['scenario_id'])}</td>"
        f"<td>{html.escape(item['exit_status'])}</td>"
        f"<td>{item['sightings_count']}</td>"
        "</tr>"
        for item in summary["top_signatures"]
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Dwarf Bundle Summary</title>"
        "<style>"
        "body{font-family:system-ui,-apple-system,sans-serif;margin:24px;color:#111;background:#fff;}"
        "h1,h2{margin:0 0 12px 0;} .cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0 24px 0;}"
        ".card{border:1px solid #ddd;border-radius:6px;padding:12px;background:#fafafa;}"
        ".card .label{font-size:12px;color:#666;margin-bottom:4px;} .card .value{font-size:22px;font-weight:600;}"
        "table{border-collapse:collapse;width:100%;font-size:14px;margin-bottom:24px;} th,td{border:1px solid #ddd;padding:8px;text-align:left;vertical-align:top;}"
        "th{background:#f4f4f4;}"
        "</style></head><body>"
        "<h1>Bundle Summary</h1>"
        "<div class='cards'>"
        f"<div class='card'><div class='label'>Bundles</div><div class='value'>{summary['total_bundle_count']}</div></div>"
        f"<div class='card'><div class='label'>Verified Attestations</div><div class='value'>{summary['attestation_verified_count']}</div></div>"
        f"<div class='card'><div class='label'>Attestation Coverage %</div><div class='value'>{summary['attestation_coverage_pct']}</div></div>"
        f"<div class='card'><div class='label'>Top Signatures</div><div class='value'>{len(summary['top_signatures'])}</div></div>"
        "</div>"
        "<h2>By Scenario Family</h2>"
        f"<table><thead><tr><th>family</th><th>count</th></tr></thead><tbody>{family_rows}</tbody></table>"
        "<h2>Per Bundle</h2>"
        "<table><thead><tr><th>run_id</th><th>scenario_id</th><th>exit</th><th>attestation</th><th>tags</th><th>coverage</th><th>throughput</th><th>crashes</th></tr></thead>"
        f"<tbody>{''.join(bundle_rows)}</tbody></table>"
        "<h2>Top Signatures</h2>"
        "<table><thead><tr><th>signature_id</th><th>scenario_id</th><th>exit</th><th>sightings</th></tr></thead>"
        f"<tbody>{signature_rows}</tbody></table>"
        "</body></html>"
    )


def run_bundle_summary_compose(*, runs_dir: Path, bundle_ids: list[str], output_dir: Path) -> dict:
    payload = build_bundle_summary(runs_dir=runs_dir, bundle_ids=bundle_ids)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "summary.json"
    md_path = output_dir / "summary.md"
    html_path = output_dir / "summary.html"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    html_path.write_text(_render_html(payload), encoding="utf-8")
    payload["summary_json_relpath"] = _relative_artifact_path(json_path)
    payload["summary_md_relpath"] = _relative_artifact_path(md_path)
    payload["summary_html_relpath"] = _relative_artifact_path(html_path)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Compose an executive bundle summary across captured Dwarf bundles")
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--bundle-id", action="append", dest="bundle_ids", required=True)
    args = parser.parse_args(argv[1:])

    runs_dir = infer_runs_dir(args.runs_dir)
    if runs_dir is None:
        print("missing runs dir", file=sys.stderr)
        return 1
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()

    emit_target_event(
        primitive="runtime_bundle_summary_compose",
        event="bundle_summary_compose_started",
        payload={"runs_dir": str(runs_dir), "output_dir": str(output_dir), "bundle_ids": list(args.bundle_ids or [])},
    )
    try:
        result = run_bundle_summary_compose(
            runs_dir=runs_dir,
            bundle_ids=list(args.bundle_ids or []),
            output_dir=output_dir,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    emit_target_event(
        primitive="runtime_bundle_summary_compose",
        event="bundle_summary_compose_completed",
        payload=result,
    )
    print(
        "bundle_count={bundle_count} summary_json_relpath={summary_json_relpath} summary_md_relpath={summary_md_relpath} summary_html_relpath={summary_html_relpath}".format(
            bundle_count=result["summary"]["total_bundle_count"],
            summary_json_relpath=result["summary_json_relpath"],
            summary_md_relpath=result["summary_md_relpath"],
            summary_html_relpath=result["summary_html_relpath"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
