"""Operate /runs/<id> single-run inspector view."""
from __future__ import annotations

from profile_manager.data.operate_run import operate_run_detail
from profile_manager.templating import render


def render_operate_run(run_id: str) -> str | None:
    """Render /operate/runs/<id>. Returns None when the run is not on disk
    so the caller can dispatch a 404; the view itself never invents a run.
    """
    detail = operate_run_detail(run_id)
    if detail is None:
        return None
    return render(
        "operate/run.j2",
        page_title=f"Run · {run_id}",
        density="reading",
        active="operate",
        active_sub="runs",
        run=detail,
    )


def render_operate_run_not_found(run_id: str) -> str:
    """Render the explicit not-found surface for /operate/runs/<id>."""
    return render(
        "operate/run_not_found.j2",
        page_title=f"Run not found · {run_id}",
        density="reading",
        active="operate",
        active_sub="runs",
        run_id=run_id,
    )
