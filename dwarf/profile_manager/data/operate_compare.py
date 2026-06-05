"""Runner-side data for /operate/compare.

The post-run divergence dashboard (latest comparison per scenario, family
groupings, aggregate strip) lives in `data.compare`. This module is the
operator-driven side: scenario picker rows, family-pill counts, the
canonical compare invocation string, and the recent-compare-runs table.

Source of truth chain:
    runnable_scenarios -> data.scenarios._list_scenarios_for_compare
    family_pills       -> views.scenarios._scenario_family
    recent_compare_runs-> data.runs.recent_runs_payload + cross-impl-comparison.json

No fabricated rows. recent_compare_runs only surfaces runs whose dir
contains cross-impl-comparison.json on disk.
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
    """Slice 26: canonical URL is /operate/runs/<id>. Mirror data.compare."""
    return f"/operate/runs/{run_id}"


def runnable_scenarios() -> list[dict[str, Any]]:
    """Return scenario picker rows: id, title, path, runtime, family.

    Sourced from data.scenarios._list_scenarios_for_compare so the picker
    cannot drift from the canonical scenario catalog.
    """
    from profile_manager.data.scenarios import _list_scenarios_for_compare
    from profile_manager.views.scenarios import _scenario_family

    rows = []
    for entry in _list_scenarios_for_compare():
        rows.append({
            "id": entry["id"],
            "title": entry.get("title") or entry["id"],
            "path": entry["path"],
            "runtime": entry.get("runtime") or "",
            "family": _scenario_family(entry["id"]),
        })
    rows.sort(key=lambda r: (1 if r["family"] == "other" else 0, r["family"], r["id"]))
    return rows


def family_pills(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter pills for the scenario picker: all + one per family slug."""
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["family"]] = counts.get(r["family"], 0) + 1
    family_slugs = sorted(counts.keys(), key=lambda s: (1 if s == "other" else 0, s))
    pills = [{"slug": "", "label": "all", "count": len(rows), "active": True}]
    for slug in family_slugs:
        pills.append({"slug": slug, "label": slug, "count": counts[slug], "active": False})
    return pills


def compare_command_for(scenario_path: str) -> str:
    """Return the canonical CLI invocation for `cardano-profile compare`.

    Surfaced verbatim in the "show command" disclosure on the runner card so
    operators can copy-paste an identical run from a terminal.
    """
    return f"cardano-profile compare {scenario_path}"


def _read_comparison_summary(run_dir: Path) -> dict[str, Any] | None:
    """Read just the summary fields from cross-impl-comparison.json.

    Lighter than data.compare._comparison_from_run — we only need
    scenario_id, agreed, and the two per-impl run ids for the recent-runs
    table.
    """
    json_path = run_dir / "cross-impl-comparison.json"
    if not json_path.is_file():
        return None
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    runs_block = payload.get("runs") or {}
    amaru = runs_block.get("amaru") or {}
    cn = runs_block.get("cardano-node") or {}
    return {
        "scenario_id": payload.get("scenario_id") or "",
        "result": payload.get("result") or ("AGREED" if payload.get("agreed") else "DIVERGED"),
        "agreed": bool(payload.get("agreed")),
        "amaru_run_id": amaru.get("run_id"),
        "cardano_node_run_id": cn.get("run_id"),
    }


def recent_compare_runs(*, runs_dir: Path | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Most recent runs that produced a cross-impl-comparison.json.

    The JSON is emitted in the cardano-node-side bundle by `cardano-profile
    compare`, so the run id surfaced here is that side's id; the amaru
    counterpart is read from the JSON's `runs.amaru.run_id` field.
    """
    from profile_manager.data.runs import recent_runs_payload
    from profile_manager.views.scenarios import _scenario_family

    base = Path(runs_dir) if runs_dir is not None else _forensic_runs_dir()
    payload = recent_runs_payload(runs_dir=base, limit=max(limit * 4, limit))
    out: list[dict[str, Any]] = []
    for run in payload.get("recent_runs") or []:
        rid = run.get("run_id")
        if not rid:
            continue
        run_dir = base / rid
        summary = _read_comparison_summary(run_dir)
        if summary is None:
            continue
        scenario_id = summary["scenario_id"]
        out.append({
            "run_id": rid,
            "run_url": _bundle_inspector_url(rid),
            "ended_at": run.get("ended_at"),
            "scenario_id": scenario_id,
            "family": _scenario_family(scenario_id) if scenario_id else "other",
            "agreed": summary["agreed"],
            "result": summary["result"],
            "amaru_run_id": summary["amaru_run_id"],
            "amaru_run_url": _bundle_inspector_url(summary["amaru_run_id"]) if summary["amaru_run_id"] else None,
            "cardano_node_run_id": summary["cardano_node_run_id"],
            "cardano_node_run_url": _bundle_inspector_url(summary["cardano_node_run_id"]) if summary["cardano_node_run_id"] else None,
        })
        if len(out) >= limit:
            break
    return out
