"""View for /operate/compare/runs?left=<id>&right=<id>."""
from __future__ import annotations

from profile_manager.data.operate_run_compare import compare_runs
from profile_manager.data.operate_run_compare_field_diff import field_diff_payload
from profile_manager.templating import render


def render_operate_run_compare(left_id: str, right_id: str) -> str:
    """Render the substrate-aware side-by-side compare. Falls back to a
    helper-text page when either input is empty or unreadable; never
    returns None so the dispatcher always serves 200.

    Item #18 — adds a field-level structural diff overlay over
    manifest.json + assertion stats + AFL telemetry, classifying each
    leaf as added / removed / mutated / same with semantic highlighting
    for pass/fail flips and ±20% telemetry drift."""
    if not left_id or not right_id:
        return render(
            "operate/run_compare_help.j2",
            page_title="Compare runs",
            density="reading",
            active="operate",
            active_sub="compare",
            left_id=left_id,
            right_id=right_id,
        )
    diff = compare_runs(left_id, right_id)
    field_diff = field_diff_payload(left_id, right_id)
    return render(
        "operate/run_compare.j2",
        page_title=f"Compare · {left_id} ↔ {right_id}",
        density="reading",
        active="operate",
        active_sub="compare",
        diff=diff,
        field_diff=field_diff,
        left_id=left_id,
        right_id=right_id,
    )
