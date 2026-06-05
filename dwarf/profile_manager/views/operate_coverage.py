"""Operate /coverage view — top-level coverage roll-up surface."""
from __future__ import annotations

from profile_manager.data.operate_coverage import (
    coverage_history,
    latest_coverage_run,
)
from profile_manager.data.operate_coverage_trend import coverage_trend_payload
from profile_manager.templating import render


def render_operate_coverage() -> str:
    """Render /operate/coverage. Reads the newest run on disk that has a
    coverage-report directory; falls back to a clean empty-state surface
    if no coverage reports have been staged yet.

    Item #16: also surfaces per-target bitmap_cvg trend pulled from
    every AFL/AFL++ run's fuzzer_stats."""
    latest = latest_coverage_run()
    history = coverage_history(limit=20)
    trend = coverage_trend_payload()
    return render(
        "operate/coverage.j2",
        page_title="Coverage",
        density="reading",        active="operate",
        active_sub="coverage",
        latest=latest,
        history=history,
        trend=trend,
    )
