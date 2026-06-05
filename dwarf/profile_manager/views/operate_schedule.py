"""Operate /schedule view — list of scheduled entries + create form."""
from __future__ import annotations

from profile_manager.data import schedule_store
from profile_manager.templating import render


def _scenario_options() -> list[dict[str, str]]:
    """Pre-load the scenario picker from the live catalog. Each option
    carries the scenario.id (for the schedule entry's scenario_id field)
    plus the absolute scenario.yaml path the CLI consumes."""
    from profile_manager.data.scenarios import _list_scenarios_for_compare
    out = []
    for s in _list_scenarios_for_compare():
        sid = s.get("id") or ""
        path = s.get("path") or ""
        if sid and path:
            out.append({"id": sid, "title": s.get("title") or sid, "path": path})
    out.sort(key=lambda r: r["id"])
    return out


def render_operate_schedule() -> str:
    entries = schedule_store.list_entries()
    return render(
        "operate/schedule.j2",
        page_title="Schedule",
        density="dense",
        active="operate",
        active_sub="schedule",
        entries=entries,
        scenarios=_scenario_options(),
        empty=not entries,
    )
