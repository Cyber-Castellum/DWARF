"""Operate /targets view."""
from __future__ import annotations

from profile_manager.data.operate_targets import (
    implementation_pill_inventory,
    operate_target_rows,
)
from profile_manager.templating import render


def render_operate_targets(token: str | None = None) -> str:
    """Render /operate/targets: dense-table decode-target catalog with filter pills.

    Shows every registered target (not just the M2 serdes subset) so
    dashboard-registered targets appear.
    """
    rows = operate_target_rows()
    pills = implementation_pill_inventory(rows)
    return render(
        "operate/targets.j2",
        page_title="Targets",
        density="dense",
        active="operate",
        active_sub="targets",
        rows=rows,
        pills=pills,
        empty=not rows,
        full_target_count=len(rows),
        token=token,
    )
