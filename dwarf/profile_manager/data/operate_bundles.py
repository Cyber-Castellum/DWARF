"""Forensic bundle catalog for /operate/bundles.

Lists *.tar.gz under dwarf/bundles/ (or ADA2_DWARF_BUNDLES_DIR override)
and cross-references runs/<run_id>/manifest.json opportunistically for
richer fields (scenario_id, profile_id, exit_status). Mirrors slice-10's
_enrich_run_row per-file enrichment; missing run dir or malformed
manifest -> manifest fields None and the row still renders with
filesystem fields (run_id, size, mtime).

URL helpers: _bundle_catalog_url is the single source of truth for
/operate/bundles#<run_id>. Distinct from slice-7's _bundle_inspector_url
which returns /runs/<run_id> for the per-run-dir inspector. Two
helpers because two surfaces.

No tar.gz inspection — opening every archive on every page render is
too expensive at scale. When run dir is gone, columns degrade to None.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from profile_manager.data.bundles import _forensic_bundles_dir
from profile_manager.data.compare import _bundle_inspector_url
from profile_manager.data.operate_profiles import _profile_url
from profile_manager.data.runs import _forensic_runs_dir


def _bundle_catalog_url(run_id: str) -> str:
    """Single source of truth for bundle catalog deep-link URLs.

    Returns /operate/bundles#<run_id> today; future per-bundle sub-route
    is a one-line change. Distinct from _bundle_inspector_url (slice 7)
    which returns /runs/<run_id> for the per-run-dir inspector.
    """
    return f"/operate/bundles#{run_id}"


_KIB = 1024
_MIB = _KIB * 1024
_GIB = _MIB * 1024


def _format_size(size_bytes: int) -> str:
    """Format bytes as B / KiB / MiB / GiB with one decimal above KiB."""
    if size_bytes < _KIB:
        return f"{size_bytes} B"
    if size_bytes < _MIB:
        return f"{size_bytes / _KIB:.1f} KiB"
    if size_bytes < _GIB:
        return f"{size_bytes / _MIB:.1f} MiB"
    return f"{size_bytes / _GIB:.1f} GiB"


def _enrich_bundle_row(bundle_path: Path, runs_dir: Path) -> dict[str, Any]:
    """Augment a bundle file with optional manifest fields.

    Reads runs/<run_id>/manifest.json defensively; missing run dir,
    malformed JSON, or missing nested key -> manifest fields None.
    The row never drops out — bundle file presence is the listing
    source of truth.
    """
    run_id = bundle_path.stem  # filename minus last suffix (.gz)
    if run_id.endswith(".tar"):
        # `.tar.gz`.stem returns "<name>.tar"; strip the `.tar` to get bare run_id.
        run_id = run_id[: -len(".tar")]
    try:
        stat = bundle_path.stat()
        size_bytes = int(stat.st_size)
        mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except OSError:
        size_bytes = 0
        mtime_iso = ""

    scenario_id: str | None = None
    profile_id: str | None = None
    exit_status: str | None = None
    manifest_path = runs_dir / run_id / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        scenario = manifest.get("scenario") or {}
        if isinstance(scenario, dict):
            scenario_id = scenario.get("id")
        profile = manifest.get("profile") or {}
        if isinstance(profile, dict):
            profile_id = profile.get("id")
        exit_status = manifest.get("exit_status")
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        pass

    return {
        "run_id": run_id,
        "run_url": _bundle_inspector_url(run_id),
        "catalog_url": _bundle_catalog_url(run_id),
        "created": mtime_iso,
        "size_bytes": size_bytes,
        "size_display": _format_size(size_bytes),
        "scenario_id": scenario_id,
        "profile_id": profile_id,
        "profile_url": _profile_url(profile_id) if profile_id else None,
        "exit_status": exit_status,
    }


def operate_bundle_rows(*, bundles_dir: Path | None = None,
                       runs_dir: Path | None = None) -> list[dict[str, Any]]:
    """List *.tar.gz under bundles_dir; enrich via runs/<id>/manifest.json
    cross-reference; return rows newest-first by file mtime.

    Defensive: malformed JSON / missing run dir / missing nested key
    keeps the row, manifest fields None.
    """
    base_bundles = Path(bundles_dir) if bundles_dir is not None else _forensic_bundles_dir()
    base_runs = Path(runs_dir) if runs_dir is not None else _forensic_runs_dir()
    if not base_bundles.is_dir():
        return []
    paths: list[Path] = sorted(base_bundles.glob("*.tar.gz"))
    rows: list[dict[str, Any]] = [
        _enrich_bundle_row(path, runs_dir=base_runs) for path in paths
    ]
    # Newest first by `created` (ISO timestamp from file mtime).
    rows.sort(key=lambda r: r.get("created") or "", reverse=True)
    return rows


def status_pill_inventory(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter-pill set: all (default-active), pass, fail.

    Mirrors slice-10's signature exactly. Pills with zero matching rows
    still render. Other exit_status values (None for missing-manifest
    bundles, error, unknown) match only the all pill.
    """
    pass_count = sum(1 for r in rows if r.get("exit_status") == "pass")
    fail_count = sum(1 for r in rows if r.get("exit_status") == "fail")
    return [
        {"slug": "", "label": "all", "count": len(rows), "active": True},
        {"slug": "pass", "label": "pass", "count": pass_count, "active": False},
        {"slug": "fail", "label": "fail", "count": fail_count, "active": False},
    ]
