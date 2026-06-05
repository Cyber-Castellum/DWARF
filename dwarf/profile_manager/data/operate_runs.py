"""Recent forensic runs index for /operate/runs.

Walks recent_runs_payload(limit=N) and re-reads each run's manifest.json
defensively to extract profile_id and target_implementation (which the
upstream payload doesn't carry). Mirrors slice-7's _comparison_from_run
per-run-dir read pattern; missing or malformed manifest -> the affected
fields fall back to None and the row renders with a "—" placeholder.

URL helpers are imported, not inlined:
    _bundle_inspector_url (slice 7) for run-id links
    _profile_url (slice 9) for profile-id links

The status_pill_inventory always returns three pills (all / pass / fail)
even when one count is zero, so the filter UI stays consistent.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from profile_manager.data.compare import _bundle_inspector_url
from profile_manager.data.operate_profiles import _profile_url


def _enrich_run_row(run: dict, runs_dir: Path) -> dict[str, Any]:
    """Augment a recent_runs_payload entry with profile_id +
    target_implementation by re-reading the run's manifest.json.

    Defensive: missing manifest, malformed JSON, or missing nested key
    falls back to None. The row itself never drops out — recent_runs_payload
    already enumerated it.
    """
    run_id = run["run_id"]
    profile_id: str | None = None
    target_implementation: str | None = None
    manifest_path = runs_dir / run_id / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        profile = manifest.get("profile") or {}
        if isinstance(profile, dict):
            profile_id = profile.get("id")
        target = manifest.get("target") or {}
        if isinstance(target, dict):
            target_implementation = target.get("implementation")
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        pass

    source = run.get("source") or "local"
    is_local = source == "local"

    # Item A (Phase 4.3 D-1) — thinness-suspicion badge per row. Only
    # evaluated for local bundles (remote rows don't carry the
    # underlying telemetry on this filesystem).
    thinness_signals: list[dict] = []
    if is_local:
        from profile_manager.data.thinness_signals import detect_thinness
        try:
            thinness_signals = detect_thinness(runs_dir / run_id)
        except Exception:  # noqa: BLE001 — never crash the index render
            thinness_signals = []

    return {
        "run_id": run_id,
        "ended_at": run.get("ended_at"),
        "scenario_id": run.get("scenario_id"),
        "profile_id": profile_id,
        "profile_url": _profile_url(profile_id) if profile_id else None,
        "runtime": run.get("runtime"),
        "exit_status": run.get("exit_status"),
        "target_implementation": target_implementation,
        "run_url": _bundle_inspector_url(run_id),
        "source": source,
        "is_local": is_local,
        "thinness_signals": thinness_signals,
    }


def operate_run_rows(*, runs_dir: Path | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Walk recent_runs_payload, return enriched rows.

    Order is the newest-first sort the upstream extractor produces.
    Remote-source runs render run_id without an inspector link
    (matches existing legacy treatment in dashboard.py).
    """
    from profile_manager.data.runs import recent_runs_payload, _forensic_runs_dir

    base = Path(runs_dir) if runs_dir is not None else _forensic_runs_dir()
    payload = recent_runs_payload(runs_dir=base, limit=limit)
    return [_enrich_run_row(run, runs_dir=base) for run in payload.get("recent_runs", [])]


def apply_run_filters(rows: list[dict[str, Any]], *, outcome: str = "",
                      q: str = "") -> list[dict[str, Any]]:
    """Slice 46 — server-side filter rails over operate_run_rows.

    ``outcome`` matches ``exit_status`` exactly when set; empty string
    means "all". ``q`` is a case-insensitive substring match against
    run_id, scenario_id, profile_id, and target_implementation."""
    out = list(rows)
    if outcome:
        out = [r for r in out if (r.get("exit_status") or "") == outcome]
    if q:
        needle = q.lower()
        def hay(row: dict[str, Any]) -> str:
            parts = [
                row.get("run_id") or "",
                row.get("scenario_id") or "",
                row.get("profile_id") or "",
                row.get("target_implementation") or "",
            ]
            return " ".join(parts).lower()
        out = [r for r in out if needle in hay(r)]
    return out


def status_pill_inventory(rows: list[dict[str, Any]], *, active_outcome: str = "") -> list[dict[str, Any]]:
    """Filter-pill set: all (default-active), pass, fail, error.

    Pills with zero matching rows still render — clicking them shows
    the empty-filtered state honestly. error is the bucket for runs
    that never completed cleanly enough to produce a pass/fail outcome.
    Counts are computed against the unfiltered ``rows`` so the pills
    advertise the corpus size, not the filtered subset.
    """
    pass_count = sum(1 for r in rows if r.get("exit_status") == "pass")
    fail_count = sum(1 for r in rows if r.get("exit_status") == "fail")
    error_count = sum(1 for r in rows if r.get("exit_status") == "error")
    return [
        {"slug": "", "label": "all", "count": len(rows), "active": active_outcome == ""},
        {"slug": "pass", "label": "pass", "count": pass_count, "active": active_outcome == "pass"},
        {"slug": "fail", "label": "fail", "count": fail_count, "active": active_outcome == "fail"},
        {"slug": "error", "label": "error", "count": error_count, "active": active_outcome == "error"},
    ]
