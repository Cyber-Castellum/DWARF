"""Operate-side dashboard landing — operator command center.

Slice 20 CP2: replaced the 3-row wiring proof with a tile grid sourced
entirely from existing data extractors. Every value on the page comes
from a real evidence path; nothing is synthesized. Anti-fabrication
rails inherited from prior slices: no findings/severity/risk language,
no synthesized labels, evidence-only counts.
"""
from __future__ import annotations

from profile_manager.data.compare import (
    _has_metric_divergence,
    latest_compare_per_scenario,
)
from profile_manager.data.operate_bundles import operate_bundle_rows
from profile_manager.data.operate_runs import (
    operate_run_rows,
    status_pill_inventory,
)
from profile_manager.data.operate_targets import (
    implementation_pill_inventory,
    operate_m2_target_rows,
)
from profile_manager.data.profiles import _profile_rows
from profile_manager.data.scenarios import _list_scenarios_for_compare
from profile_manager.templating import render

# Recent-runs window for the pass/fail tile. 200 keeps a meaningful
# rolling sample without dragging in years of history.
_RECENT_RUNS_WINDOW = 200


def render_operate_landing() -> str:
    profiles = _profile_rows()
    profile_id = profiles[0]["id"] if profiles else None

    runs = operate_run_rows(limit=_RECENT_RUNS_WINDOW)
    pills = status_pill_inventory(runs)
    pass_count = next((p["count"] for p in pills if p["slug"] == "pass"), 0)
    fail_count = next((p["count"] for p in pills if p["slug"] == "fail"), 0)
    other_count = max(len(runs) - pass_count - fail_count, 0)
    pass_rate_pct = round(100 * pass_count / len(runs)) if runs else None

    targets = operate_m2_target_rows()
    target_pills = implementation_pill_inventory(targets)

    bundles = operate_bundle_rows()

    comparisons = latest_compare_per_scenario(limit=_RECENT_RUNS_WINDOW)
    divergent = [c for c in comparisons if _has_metric_divergence(c)]

    return render(
        "operate/landing.j2",
        page_title="Operate",
        density="dense",        active="operate",
        active_sub="overview",
        profile_id=profile_id,
        profile_count=len(profiles),
        runs_total=len(runs),
        runs_window=_RECENT_RUNS_WINDOW,
        pass_count=pass_count,
        fail_count=fail_count,
        other_count=other_count,
        pass_rate_pct=pass_rate_pct,
        bundles_count=len(bundles),
        target_count=len(targets),
        target_pills=[p for p in target_pills if p["slug"]],
        scenario_count=len(_list_scenarios_for_compare()),
        comparisons_count=len(comparisons),
        divergent_count=len(divergent),
    )
