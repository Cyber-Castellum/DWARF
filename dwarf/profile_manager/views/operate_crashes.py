"""Operate /crashes view — crash-triage signature roll-up."""
from __future__ import annotations

from profile_manager.data.operate_crashes import operate_crashes_payload
from profile_manager.templating import render


def render_operate_crashes() -> str:
    """Render /operate/crashes: crash dedup signatures grouped by
    (signal, op), with last-find timestamp + exemplar input per group."""
    payload = operate_crashes_payload()
    return render(
        "operate/crashes.j2",
        page_title="Crashes",
        density="reading",
        active="operate",
        active_sub="crashes",
        groups=payload["groups"],
        total_crashes=payload["total_crashes"],
        total_signatures=payload["total_signatures"],
        sources_observed=payload["sources_observed"],
        empty=payload["empty"],
    )
