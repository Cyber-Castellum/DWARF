"""Operate /compare view — runner + standing divergence dashboard."""
from __future__ import annotations

from profile_manager.data.compare import (
    aggregate_strip,
    comparisons_grouped_by_family,
    count_compare_runs_in_last_24h,
    latest_compare_per_scenario,
)
from profile_manager.data.operate_compare import (
    compare_command_for,
    family_pills,
    recent_compare_runs,
    runnable_scenarios,
)
from profile_manager.data.runs import recent_runs_payload
from profile_manager.templating import render


def render_operate_compare() -> str:
    """Render /operate/compare with the runner + standing divergence sections.

    Slice-24 redesign merges three concerns:
      * "Run a compare" — scenario picker, command disclosure, live output.
      * "Recent compares" — table of the most recent compare runs on disk.
      * "Standing divergence" — latest-comparison-per-scenario family groups.

    Each section reads from its own data extractor; nothing is hand-curated.
    """
    runs = recent_runs_payload(limit=200)
    comparisons = latest_compare_per_scenario(limit=200)
    families = comparisons_grouped_by_family(comparisons)
    strip = aggregate_strip(comparisons, all_runs_24h_count=count_compare_runs_in_last_24h(runs))

    scenarios = runnable_scenarios()
    pills = family_pills(scenarios)
    recents = recent_compare_runs(limit=20)
    sample_command = compare_command_for(scenarios[0]["path"]) if scenarios else "cardano-profile compare <scenario>"

    return render(
        "operate/compare.j2",
        page_title="Compare",
        density="reading",        active="operate",
        active_sub="compare",
        scenarios=scenarios,
        pills=pills,
        sample_command=sample_command,
        recents=recents,
        strip=strip,
        families=families,
        empty=not comparisons,
    )
