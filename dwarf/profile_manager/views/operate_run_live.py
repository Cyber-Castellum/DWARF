"""View for /operate/runs/<id>/live — streaming log.ndjson over SSE."""
from __future__ import annotations

from profile_manager.templating import render


def render_operate_run_live(run_id: str) -> str:
    return render(
        "operate/run_live.j2",
        page_title=f"Live · {run_id}",
        density="reading",
        active="operate",
        active_sub="runs",
        run_id=run_id,
    )
