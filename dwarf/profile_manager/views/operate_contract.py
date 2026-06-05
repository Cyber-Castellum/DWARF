"""Operate /contract view — auto-derived contract progress page."""
from __future__ import annotations

from profile_manager.data.operate_contract import (
    work_goals,
    deliverables,
    m1_last_updated,
    future_milestones,
)
from profile_manager.templating import render


def render_operate_contract() -> str:
    """Render /operate/contract: M1 detail tables + M2-M9 collapsed list."""
    return render(
        "operate/contract.j2",
        page_title="Contract",
        density="dense",        active="operate",
        active_sub="contract",
        work_goals=work_goals(),
        deliverables=deliverables(),
        m1_last_updated=m1_last_updated(),
        future_milestones=future_milestones(),
    )
