"""Operate /timeline view — chronological evidence surface."""
from __future__ import annotations

from profile_manager.data.operate_timeline import (
    latest_timeline_run,
    timeline_history,
)
from profile_manager.templating import render


def render_operate_timeline() -> str:
    """Render /operate/timeline. Shows the newest timeline.json on disk
    as a vertical event list + per-signature breakdown, with a rolling
    history of past timeline runs below. Honest empty state when the
    runtime_bundle_timeline primitive has not been run anywhere."""
    latest = latest_timeline_run()
    history = timeline_history(limit=20)
    return render(
        "operate/timeline.j2",
        page_title="Timeline",
        density="reading",        active="operate",
        active_sub="timeline",
        latest=latest,
        history=history,
    )
