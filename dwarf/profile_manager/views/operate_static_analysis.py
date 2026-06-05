"""Operate /static-analysis view — clippy/audit/deny status surface."""
from __future__ import annotations

from profile_manager.data.operate_static_analysis import (
    TOOLS,
    history_per_tool,
    latest_per_tool,
)
from profile_manager.templating import render


def render_operate_static_analysis() -> str:
    """Render /operate/static-analysis. Three columns (clippy / audit /
    deny), each carrying the latest-run summary plus a small per-tool
    history table. Honest empty state per column when a tool has never
    been run anywhere."""
    latest = latest_per_tool()
    history = history_per_tool(limit_per_tool=5)
    return render(
        "operate/static_analysis.j2",
        page_title="Static analysis",
        density="reading",        active="operate",
        active_sub="static-analysis",
        tools=list(TOOLS),
        latest=latest,
        history=history,
    )
