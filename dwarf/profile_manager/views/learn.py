"""Learner-side dashboard landing — editorial entry point.

Slice 20 CP2: tile grid linking concepts, walkthroughs, architecture,
coverage, and status. Reading-mode density preserves the editorial
tone while the brand chrome carries the forensic-noir HUD aesthetic.

Anti-fabrication rails: every count is sourced from a real catalog
(CONCEPTS, walkthrough_entries, scenarios, fuzz targets, profile node
types). Nothing synthesized.
"""
from __future__ import annotations

from profile_manager.data.concepts import CONCEPTS
from profile_manager.data.fuzz import _fuzz_rows
from profile_manager.data.operate_runs import (
    operate_run_rows,
    status_pill_inventory,
)
from profile_manager.data.profiles import _profile_rows
from profile_manager.data.scenarios import _list_scenarios_for_compare
from profile_manager.data.walkthroughs import walkthrough_entries
from profile_manager.templating import render


def render_learn_landing() -> str:
    impls = sorted({row["node_type"] for row in _profile_rows() if row.get("node_type")})

    runs = operate_run_rows(limit=200)
    pills = status_pill_inventory(runs)
    pass_count = next((p["count"] for p in pills if p["slug"] == "pass"), 0)
    pass_rate_pct = round(100 * pass_count / len(runs)) if runs else None

    return render(
        "learn/landing.j2",
        page_title="Learn",
        density="reading",        active="learn",
        active_sub="overview",
        implementations=impls,
        scenario_count=len(_list_scenarios_for_compare()),
        fuzz_target_count=len(_fuzz_rows()),
        concept_count=len(CONCEPTS),
        walkthrough_count=len(walkthrough_entries()),
        runs_total=len(runs),
        pass_rate_pct=pass_rate_pct,
    )
