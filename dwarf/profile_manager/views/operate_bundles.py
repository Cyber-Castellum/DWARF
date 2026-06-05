"""Operate /bundles view."""
from __future__ import annotations

from profile_manager.data.operate_bundles import (
    operate_bundle_rows,
    status_pill_inventory,
)
from profile_manager.templating import render


def render_operate_bundles() -> str:
    """Render /operate/bundles: dense-table forensic-bundle catalog with filter pills."""
    rows = operate_bundle_rows()
    pills = status_pill_inventory(rows)
    return render(
        "operate/bundles.j2",
        page_title="Bundles",
        density="dense",        active="operate",
        active_sub="bundles",
        rows=rows,
        pills=pills,
        empty=not rows,
    )
