#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(DWARF_ROOT) not in sys.path:
    sys.path.insert(0, str(DWARF_ROOT))

import runtime_bundle_attestation  # noqa: E402
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
        return Path(run_dir) / "outputs" / "bundle-timeline"
    return Path.cwd() / "outputs" / "bundle-timeline"


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


def _attestation_verdict(run_dir: Path) -> str | None:
    attestation_path = run_dir / "outputs" / "attestation" / "attestation.json"
    if not attestation_path.is_file():
        return None
    verification = runtime_bundle_attestation.verify_attestation(attestation_path)
    return verification.get("verdict") or None


def _extract_evidence_tokens(run_dir: Path) -> list[str]:
    tokens: set[str] = set()

    verdict = _attestation_verdict(run_dir)
    if verdict:
        tokens.add(f"attestation:{verdict}")

    replay = _load_json(run_dir / "outputs" / "replay" / "result.json")
    if replay:
        tokens.add(f"bundle_replay:{replay.get('comparison_verdict', 'unknown')}")
        if replay.get("target_run_id"):
            tokens.add("bundle_replay:has-parent")

    bundle_diff = _load_json(run_dir / "outputs" / "bundle-diff" / "diff.json")
    if bundle_diff:
        tokens.add(f"bundle_diff:{bundle_diff.get('comparison_verdict', 'unknown')}")

    chain_verify = _load_json(run_dir / "outputs" / "chain-verify" / "chain-verify-report.json")
    if chain_verify:
        tokens.add(f"chain_verify:{chain_verify.get('chain_verdict', 'unknown')}")

    sarif_export = _load_json(run_dir / "outputs" / "sarif-export" / "result.json")
    if sarif_export:
        tokens.add(f"sarif_export:results:{int(sarif_export.get('sarif_result_count', 0))}")
        for evidence_type in sarif_export.get("mapped_evidence_types") or []:
            tokens.add(f"sarif_export:type:{evidence_type}")

    coverage_summary = _load_json(run_dir / "outputs" / "coverage-report" / "coverage-summary.json")
    if coverage_summary:
        tokens.add("coverage_report:stat-only")
    coverage_file_level = _load_json(run_dir / "outputs" / "coverage-report" / "coverage-report-file-level.json")
    if coverage_file_level:
        tokens.add("coverage_report:file-level")

    for tool in ("clippy", "audit", "deny"):
        findings = _load_json(run_dir / "outputs" / f"static-analysis-{tool}" / "findings.json")
        if findings:
            tokens.add(f"static_analysis:{tool}:{findings.get('tool_status', 'unknown')}")

    lsq_result = _load_json(run_dir / "outputs" / "runtime-cardano-lsq-extract" / "result.json")
    if lsq_result:
        tokens.add(f"cardano_lsq_extract:{lsq_result.get('exit_status', 'unknown')}")

    synth_root = run_dir / "outputs" / "runtime-corpus-synthesize"
    if synth_root.is_dir():
        for manifest_path in synth_root.glob("*/manifest.json"):
            payload = _load_json(manifest_path)
            if not payload:
                continue
            tokens.add(
                "corpus_synthesize:{target}:{strategy}".format(
                    target=manifest_path.parent.name,
                    strategy=payload.get("strategy", "unknown"),
                )
            )

    if not tokens:
        tokens.add("bundle:plain")
    return sorted(tokens)


def _bundle_event(run_dir: Path) -> dict | None:
    manifest = _load_json(run_dir / "manifest.json")
    if not manifest:
        return None
    scenario = manifest.get("scenario") or {}
    scenario_id = str(scenario.get("id") or "")
    exit_status = str(manifest.get("exit_status") or "unknown")
    started_at = manifest.get("started_at") or ""
    evidence_tokens = _extract_evidence_tokens(run_dir)
    signature_body = {
        "scenario_id": scenario_id,
        "exit_status": exit_status,
        "evidence_tokens": evidence_tokens,
    }
    canonical = json.dumps(signature_body, sort_keys=True, separators=(",", ":"))
    signature_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    signature_label = f"{scenario_id}|{exit_status}|{'|'.join(evidence_tokens)}"
    return {
        "bundle_id": run_dir.name,
        "timestamp": started_at,
        "scenario_id": scenario_id,
        "exit_status": exit_status,
        "signature_id": signature_id,
        "signature_label": signature_label,
        "evidence_tokens": evidence_tokens,
    }


def _render_markdown(payload: dict) -> str:
    summary = payload["summary"]
    lines = [
        "# Bundle Timeline",
        "",
        f"- Input bundles: {summary['input_bundle_count']}",
        f"- Events included: {summary['event_count']}",
        f"- Signature count: {summary['signature_count']}",
        f"- Time window start: {summary['time_window'].get('first_seen_at')}",
        f"- Time window end: {summary['time_window'].get('last_seen_at')}",
        "",
        "## Signatures",
        "",
        "| signature_id | scenario | exit | sightings | first bundle | last bundle |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for item in payload["signatures"]:
        lines.append(
            f"| {item['signature_id'][:12]} | {item['scenario_id']} | {item['exit_status']} | "
            f"{item['sightings_count']} | {item['first_seen_bundle']} | {item['last_seen_bundle']} |"
        )
    lines.extend(["", "## Events", "", "| timestamp | bundle | signature |", "| --- | --- | --- |"])
    for event in payload["events"]:
        lines.append(f"| {event['timestamp']} | {event['bundle_id']} | {event['signature_id'][:12]} |")
    return "\n".join(lines) + "\n"


def run_bundle_timeline(
    *,
    runs_dir: Path,
    bundle_ids: list[str],
    output_dir: Path,
    scenario_id_filters: list[str],
    signature_token_filters: list[str],
) -> dict:
    events = []
    for bundle_id in bundle_ids:
        event = _bundle_event(runs_dir / bundle_id)
        if event is None:
            continue
        if scenario_id_filters and event["scenario_id"] not in scenario_id_filters:
            continue
        if signature_token_filters and not all(token in event["evidence_tokens"] for token in signature_token_filters):
            continue
        events.append(event)

    events.sort(key=lambda item: (item["timestamp"], item["bundle_id"]))
    signature_index: dict[str, dict] = {}
    for event in events:
        bucket = signature_index.setdefault(
            event["signature_id"],
            {
                "signature_id": event["signature_id"],
                "signature_label": event["signature_label"],
                "scenario_id": event["scenario_id"],
                "exit_status": event["exit_status"],
                "evidence_tokens": list(event["evidence_tokens"]),
                "first_seen_bundle": event["bundle_id"],
                "last_seen_bundle": event["bundle_id"],
                "first_seen_at": event["timestamp"],
                "last_seen_at": event["timestamp"],
                "sightings_count": 0,
                "sighting_bundles": [],
            },
        )
        bucket["sightings_count"] += 1
        bucket["last_seen_bundle"] = event["bundle_id"]
        bucket["last_seen_at"] = event["timestamp"]
        bucket["sighting_bundles"].append(event["bundle_id"])

    signatures = sorted(signature_index.values(), key=lambda item: (item["first_seen_at"], item["signature_id"]))
    summary = {
        "input_bundle_count": len(bundle_ids),
        "event_count": len(events),
        "signature_count": len(signatures),
        "unique_signatures": len(signatures),
        "time_window": {
            "first_seen_at": events[0]["timestamp"] if events else None,
            "last_seen_at": events[-1]["timestamp"] if events else None,
        },
        "applied_filters": {
            "scenario_ids": list(scenario_id_filters),
            "signature_tokens": list(signature_token_filters),
        },
    }
    payload = {
        "schema_version": "v1",
        "generated_at_utc": utc_timestamp(),
        "summary": summary,
        "events": events,
        "signatures": signatures,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    timeline_path = output_dir / "timeline.json"
    markdown_path = output_dir / "timeline-summary.md"
    timeline_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_render_markdown(payload), encoding="utf-8")
    return {
        "signature_count": summary["signature_count"],
        "unique_signatures": summary["unique_signatures"],
        "event_count": summary["event_count"],
        "time_window": summary["time_window"],
        "applied_filters": summary["applied_filters"],
        "timeline_relpath": _relative_artifact_path(timeline_path),
        "markdown_relpath": _relative_artifact_path(markdown_path),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Assemble a chronological evidence timeline across captured bundles")
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--bundle-id", action="append", dest="bundle_ids", required=True)
    parser.add_argument("--scenario-id", action="append", dest="scenario_ids", default=[])
    parser.add_argument("--signature-token", action="append", dest="signature_tokens", default=[])
    args = parser.parse_args(argv[1:])

    runs_dir = infer_runs_dir(args.runs_dir)
    if runs_dir is None:
        print("missing runs dir", file=sys.stderr)
        return 1
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()

    emit_target_event(
        primitive="runtime_bundle_timeline",
        event="bundle_timeline_started",
        payload={
            "runs_dir": str(runs_dir),
            "bundle_ids": list(args.bundle_ids),
            "scenario_id_filters": list(args.scenario_ids),
            "signature_token_filters": list(args.signature_tokens),
            "output_dir": str(output_dir),
        },
    )
    result = run_bundle_timeline(
        runs_dir=runs_dir,
        bundle_ids=list(args.bundle_ids),
        output_dir=output_dir,
        scenario_id_filters=list(args.scenario_ids),
        signature_token_filters=list(args.signature_tokens),
    )
    emit_target_event(
        primitive="runtime_bundle_timeline",
        event="bundle_timeline_completed",
        payload=result,
    )
    print(
        "signature_count={signature_count} event_count={event_count} timeline_relpath={timeline_relpath}".format(
            **result
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
