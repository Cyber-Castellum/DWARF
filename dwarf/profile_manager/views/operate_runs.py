"""Operate /runs view."""
from __future__ import annotations

from profile_manager.data.lifecycle import _local_testcase_lifecycle_summary
from profile_manager.data.operate_run_families import issue_families
from profile_manager.data.operate_runs import (
    apply_run_filters,
    operate_run_rows,
    status_pill_inventory,
)
from profile_manager.templating import render


def render_operate_runs(*, outcome: str = "", q: str = "") -> str:
    """Render /operate/runs: dense-table recent-runs index with filter pills.

    Slice 26 added an "Issue families" aggregate panel that surfaces the
    runtime-anomaly buckets across the testcase lifecycle state — derived
    from data/state/testcases not the runs index itself, so the panel is
    informative even when filtered by a particular result pill.

    Slice 46 wired server-side filters: ``outcome`` (pass / fail / error /
    "" for all) and ``q`` (substring match on run_id / scenario_id /
    profile_id / target_implementation). Filters are bookmarkable since
    they live in the URL.
    """
    all_rows = operate_run_rows(limit=100)
    filtered = apply_run_filters(all_rows, outcome=outcome, q=q)
    pills = status_pill_inventory(all_rows, active_outcome=outcome)
    lifecycle = _local_testcase_lifecycle_summary()
    families = issue_families(lifecycle)
    return render(
        "operate/runs.j2",
        page_title="Runs",
        density="dense",        active="operate",
        active_sub="runs",
        rows=filtered,
        all_count=len(all_rows),
        filtered_count=len(filtered),
        pills=pills,
        empty=not all_rows,
        filter_outcome=outcome,
        filter_q=q,
        families=families,
        runtime_anomaly_count=lifecycle.get("runtime_anomaly_count", 0),
    )
