"""Pure data extractors for /operate/coverage.

Slice 31 surfaces ada3's runtime_coverage_report bundle outputs as a
top-level dashboard page so operators don't have to drill into a
specific bundle to see current coverage. Two report shapes coexist:

  per-target rollup
    outputs/coverage-report/coverage-summary.json
    outputs/coverage-report/coverage.html  (browser-renderable)
    outputs/coverage-report/coverage.md

  file-level merge (slice-N from ada3 lane E)
    outputs/coverage-report/coverage-report-file-level.json
    outputs/coverage-report/coverage-file-level.md

This module reads what's on disk and returns a render-ready dict — the
view layer handles iframe-vs-fallback and history rendering.
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


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _bundle_inspector_url(run_id: str) -> str:
    """Slice 26: canonical URL is /operate/runs/<id>. Mirror data.compare."""
    return f"/operate/runs/{run_id}"


def _coverage_artifacts(run_dir: Path) -> dict[str, Any] | None:
    """Detect a coverage-report directory in a run and classify the
    artifacts present. Returns None if the dir is absent."""
    crd = run_dir / "outputs" / "coverage-report"
    if not crd.is_dir():
        return None
    summary = crd / "coverage-summary.json"
    html = crd / "coverage.html"
    md = crd / "coverage.md"
    file_level_json = crd / "coverage-report-file-level.json"
    file_level_md = crd / "coverage-file-level.md"
    return {
        "dir": crd,
        "has_summary": summary.is_file(),
        "summary_relpath": "outputs/coverage-report/coverage-summary.json" if summary.is_file() else None,
        "has_html": html.is_file(),
        "html_relpath": "outputs/coverage-report/coverage.html" if html.is_file() else None,
        "has_md": md.is_file(),
        "md_relpath": "outputs/coverage-report/coverage.md" if md.is_file() else None,
        "has_file_level": file_level_json.is_file(),
        "file_level_json_relpath": "outputs/coverage-report/coverage-report-file-level.json" if file_level_json.is_file() else None,
        "file_level_md_relpath": "outputs/coverage-report/coverage-file-level.md" if file_level_md.is_file() else None,
    }


def _summary_rollup(artifacts: dict[str, Any]) -> dict[str, Any]:
    """Extract render-ready totals from whichever JSON the run produced.

    Returns a uniform shape across both report modes so the template can
    render a single tile row. Fields with no data on this run come back
    as None — never fabricated.
    """
    crd = artifacts["dir"]
    out: dict[str, Any] = {
        "mode": None,
        "generated_at_utc": None,
        "bundle_count": None,
        "exec_count": None,
        "queue_count": None,
        "max_bitmap_cvg": None,
        "novel_queue_sha256_count": None,
        "total_unique_edges": None,
        "covered_functions": None,
        "covered_lines": None,
        "covered_regions": None,
        "inputs_processed": None,
        "processed_aflpp_bundle_count": None,
        "processed_libfuzzer_bundle_count": None,
        "target_count": None,
    }
    summary = _read_json(crd / "coverage-summary.json")
    if summary:
        totals = summary.get("totals") or {}
        out.update({
            "mode": "per-target-rollup",
            "generated_at_utc": summary.get("generated_at_utc"),
            "bundle_count": summary.get("bundle_count"),
            "target_count": summary.get("target_count"),
            "exec_count": totals.get("exec_count"),
            "queue_count": totals.get("queue_count"),
            "max_bitmap_cvg": totals.get("max_bitmap_cvg"),
            "novel_queue_sha256_count": totals.get("novel_queue_sha256_count"),
        })
        return out
    file_level = _read_json(crd / "coverage-report-file-level.json")
    if file_level:
        out.update({
            "mode": "file-level-merge",
            "generated_at_utc": file_level.get("generated_at_utc"),
            "bundle_count": file_level.get("processed_bundle_count"),
            "processed_aflpp_bundle_count": file_level.get("processed_aflpp_bundle_count"),
            "processed_libfuzzer_bundle_count": file_level.get("processed_libfuzzer_bundle_count"),
            "inputs_processed": file_level.get("inputs_processed"),
            "total_unique_edges": file_level.get("total_unique_edges"),
            "covered_functions": file_level.get("covered_functions"),
            "covered_lines": file_level.get("covered_lines"),
            "covered_regions": file_level.get("covered_regions"),
        })
    return out


def _per_target_rows(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    """Surface the per-target table for the rollup mode (when present)."""
    summary = _read_json(artifacts["dir"] / "coverage-summary.json") or {}
    out = []
    for t in summary.get("targets") or []:
        bundle_id = t.get("bundle_id")
        out.append({
            "target_label": t.get("target_label"),
            "engine": t.get("engine"),
            "subcampaign_id": t.get("subcampaign_id"),
            "queue_count": t.get("queue_count"),
            "exec_count": t.get("exec_count"),
            "exec_rate": t.get("exec_rate"),
            "bitmap_cvg": t.get("bitmap_cvg"),
            "coverage_score": t.get("coverage_score"),
            "coverage_metric": t.get("coverage_metric"),
            "bundle_id": bundle_id,
            "bundle_url": _bundle_inspector_url(bundle_id) if bundle_id else None,
        })
    return out


def _per_bundle_rows(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    """Surface the per-bundle table for the file-level mode."""
    payload = _read_json(artifacts["dir"] / "coverage-report-file-level.json") or {}
    out = []
    for b in payload.get("bundles") or []:
        bundle_id = b.get("bundle_id")
        out.append({
            "bundle_id": bundle_id,
            "bundle_url": _bundle_inspector_url(bundle_id) if bundle_id else None,
            "engine": b.get("engine"),
            "subcampaign_id": b.get("subcampaign_id"),
            "status": b.get("status"),
            "skip_reason": b.get("skip_reason"),
            "queue_count": b.get("queue_count"),
            "input_count": b.get("input_count"),
            "unique_edges_observed": b.get("unique_edges_observed"),
            "covered_functions": b.get("covered_functions"),
            "covered_lines": b.get("covered_lines"),
            "covered_regions": b.get("covered_regions"),
            "novel_edge_count": b.get("novel_edge_count"),
            "target_slug": b.get("target_slug"),
        })
    return out


def latest_coverage_run(*, runs_dir: Path | None = None) -> dict[str, Any] | None:
    """Walk runs/ newest-first and return render-ready data for the most
    recent run that produced a coverage-report.

    None signals the empty state — no coverage runs anywhere on disk.
    """
    base = Path(runs_dir) if runs_dir is not None else _forensic_runs_dir()
    if not base.is_dir():
        return None
    candidates = []
    for p in base.iterdir():
        if not p.is_dir():
            continue
        artifacts = _coverage_artifacts(p)
        if artifacts is None:
            continue
        candidates.append((p.name, p, artifacts))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0], reverse=True)
    run_id, run_dir, artifacts = candidates[0]
    rollup = _summary_rollup(artifacts)
    return {
        "run_id": run_id,
        "run_url": _bundle_inspector_url(run_id),
        "html_url": (
            f"/runs/{run_id}/output?path={artifacts['html_relpath']}"
            if artifacts["has_html"] else None
        ),
        "summary_url": (
            f"/runs/{run_id}/output?path={artifacts['summary_relpath']}"
            if artifacts["has_summary"] else None
        ),
        "file_level_json_url": (
            f"/runs/{run_id}/output?path={artifacts['file_level_json_relpath']}"
            if artifacts["has_file_level"] else None
        ),
        "file_level_md_url": (
            f"/runs/{run_id}/output?path={artifacts['file_level_md_relpath']}"
            if artifacts["file_level_md_relpath"] else None
        ),
        "rollup": rollup,
        "per_target_rows": _per_target_rows(artifacts),
        "per_bundle_rows": _per_bundle_rows(artifacts),
    }


def coverage_history(*, runs_dir: Path | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Chronological list of past coverage-report bundles (newest first).

    Each row carries the per-run rollup so operators can scan trend at a
    glance without opening each report.
    """
    base = Path(runs_dir) if runs_dir is not None else _forensic_runs_dir()
    if not base.is_dir():
        return []
    rows = []
    for p in sorted(base.iterdir(), reverse=True):
        if not p.is_dir():
            continue
        artifacts = _coverage_artifacts(p)
        if artifacts is None:
            continue
        rollup = _summary_rollup(artifacts)
        rows.append({
            "run_id": p.name,
            "run_url": _bundle_inspector_url(p.name),
            "mode": rollup["mode"],
            "generated_at_utc": rollup["generated_at_utc"],
            "bundle_count": rollup["bundle_count"],
            "exec_count": rollup["exec_count"],
            "queue_count": rollup["queue_count"],
            "max_bitmap_cvg": rollup["max_bitmap_cvg"],
            "total_unique_edges": rollup["total_unique_edges"],
            "covered_functions": rollup["covered_functions"],
            "covered_lines": rollup["covered_lines"],
            "covered_regions": rollup["covered_regions"],
        })
        if len(rows) >= limit:
            break
    return rows
