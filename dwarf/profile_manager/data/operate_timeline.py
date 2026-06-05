"""Pure data extractors for /operate/timeline.

Slice 32 surfaces ada3's runtime_bundle_timeline outputs as a top-level
chronology surface. The primitive emits two artifacts per run:

  outputs/bundle-timeline/timeline.json
  outputs/bundle-timeline/timeline-summary.md

This module reads timeline.json and returns render-ready dicts. The
view layer renders the chronology as a vertical event list (one row per
event, newest first), a per-signature breakdown table (first/last seen,
sightings_count), and a rolling history of past timeline runs.

No fabrication. Fields the JSON did not contain come back as None.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _forensic_runs_dir() -> Path:
    env = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "runs"


def _bundle_inspector_url(run_id: str) -> str:
    return f"/operate/runs/{run_id}"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _timeline_artifacts(run_dir: Path) -> dict[str, Any] | None:
    btd = run_dir / "outputs" / "bundle-timeline"
    if not btd.is_dir():
        return None
    json_path = btd / "timeline.json"
    md_path = btd / "timeline-summary.md"
    return {
        "dir": btd,
        "json_path": json_path,
        "has_json": json_path.is_file(),
        "json_relpath": "outputs/bundle-timeline/timeline.json" if json_path.is_file() else None,
        "md_relpath": "outputs/bundle-timeline/timeline-summary.md" if md_path.is_file() else None,
    }


def _summary_block(payload: dict[str, Any]) -> dict[str, Any]:
    """Render-ready view of the JSON's `summary` block."""
    summary = payload.get("summary") or {}
    window = summary.get("time_window") or {}
    filters = summary.get("applied_filters") or {}
    return {
        "input_bundle_count": summary.get("input_bundle_count"),
        "event_count": summary.get("event_count"),
        "signature_count": summary.get("signature_count"),
        "unique_signatures": summary.get("unique_signatures"),
        "first_seen_at": window.get("first_seen_at"),
        "last_seen_at": window.get("last_seen_at"),
        "applied_scenario_filters": list(filters.get("scenario_ids") or []),
        "applied_signature_filters": list(filters.get("signature_tokens") or []),
    }


def _event_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Render-ready chronological event list (template renders newest
    first; sort here so the view layer doesn't need to reorder)."""
    rows: list[dict[str, Any]] = []
    for e in payload.get("events") or []:
        bundle_id = e.get("bundle_id")
        rows.append({
            "bundle_id": bundle_id,
            "bundle_url": _bundle_inspector_url(bundle_id) if bundle_id else None,
            "timestamp": e.get("timestamp"),
            "scenario_id": e.get("scenario_id"),
            "exit_status": e.get("exit_status"),
            "signature_id": e.get("signature_id"),
            "signature_label": e.get("signature_label"),
            "evidence_tokens": list(e.get("evidence_tokens") or []),
        })
    # Newest first.
    rows.sort(key=lambda r: (r.get("timestamp") or ""), reverse=True)
    return rows


def _signature_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Render-ready per-signature breakdown."""
    out: list[dict[str, Any]] = []
    for s in payload.get("signatures") or []:
        first_id = s.get("first_seen_bundle")
        last_id = s.get("last_seen_bundle")
        sighting_ids = list(s.get("sighting_bundles") or [])
        out.append({
            "signature_id": s.get("signature_id"),
            "signature_label": s.get("signature_label"),
            "scenario_id": s.get("scenario_id"),
            "exit_status": s.get("exit_status"),
            "evidence_tokens": list(s.get("evidence_tokens") or []),
            "first_seen_at": s.get("first_seen_at"),
            "last_seen_at": s.get("last_seen_at"),
            "sightings_count": s.get("sightings_count"),
            "first_seen_bundle": first_id,
            "first_seen_bundle_url": _bundle_inspector_url(first_id) if first_id else None,
            "last_seen_bundle": last_id,
            "last_seen_bundle_url": _bundle_inspector_url(last_id) if last_id else None,
            "sighting_bundle_urls": [
                {"id": rid, "url": _bundle_inspector_url(rid)} for rid in sighting_ids
            ],
        })
    # Newest first by first_seen_at so the chronology is intuitive.
    out.sort(key=lambda r: (r.get("first_seen_at") or ""), reverse=True)
    return out


def latest_timeline_run(*, runs_dir: Path | None = None) -> dict[str, Any] | None:
    """Walk runs/ newest-first and return the most recent run that has
    an outputs/bundle-timeline/ directory. None signals empty state."""
    base = Path(runs_dir) if runs_dir is not None else _forensic_runs_dir()
    if not base.is_dir():
        return None
    candidates = []
    for p in base.iterdir():
        if not p.is_dir():
            continue
        artifacts = _timeline_artifacts(p)
        if artifacts is None:
            continue
        candidates.append((p.name, p, artifacts))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0], reverse=True)
    run_id, _run_dir, artifacts = candidates[0]
    payload = _read_json(artifacts["json_path"]) or {}
    summary = _summary_block(payload)
    return {
        "run_id": run_id,
        "run_url": _bundle_inspector_url(run_id),
        "generated_at_utc": payload.get("generated_at_utc"),
        "summary": summary,
        "events": _event_rows(payload),
        "signatures": _signature_rows(payload),
        "json_url": (
            f"/runs/{run_id}/output?path={artifacts['json_relpath']}"
            if artifacts["has_json"] else None
        ),
        "md_url": (
            f"/runs/{run_id}/output?path={artifacts['md_relpath']}"
            if artifacts["md_relpath"] else None
        ),
    }


def timeline_history(*, runs_dir: Path | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Past timeline runs newest-first with their summary totals."""
    base = Path(runs_dir) if runs_dir is not None else _forensic_runs_dir()
    if not base.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for p in sorted(base.iterdir(), reverse=True):
        if not p.is_dir():
            continue
        artifacts = _timeline_artifacts(p)
        if artifacts is None:
            continue
        payload = _read_json(artifacts["json_path"]) or {}
        summary = _summary_block(payload)
        rows.append({
            "run_id": p.name,
            "run_url": _bundle_inspector_url(p.name),
            "generated_at_utc": payload.get("generated_at_utc"),
            "input_bundle_count": summary["input_bundle_count"],
            "event_count": summary["event_count"],
            "signature_count": summary["signature_count"],
            "unique_signatures": summary["unique_signatures"],
            "first_seen_at": summary["first_seen_at"],
            "last_seen_at": summary["last_seen_at"],
        })
        if len(rows) >= limit:
            break
    return rows
