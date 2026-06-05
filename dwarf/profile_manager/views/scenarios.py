"""Operate /scenarios view."""
from __future__ import annotations


def _scenario_family(scenario_id: str) -> str:
    """Derive a family from a scenario id by auto-derivation.

    Algorithm: split on hyphens; the family is the first segment, except
    "cardano-node" is recognized as a compound and combined. Ids without
    a hyphen-separable prefix fall to "other" (likely empty in the
    current corpus but reserved for single-word ids).

    No hand-curated prefix list. New top-level namespaces appearing in
    the corpus surface automatically as new pill entries; the discipline
    is "scan code state, display only what's there."
    """
    if not scenario_id or "-" not in scenario_id:
        return "other"
    parts = scenario_id.split("-")
    if parts[0] == "cardano" and len(parts) >= 2 and parts[1] == "node":
        return "cardano-node"
    return parts[0]


def _sort_family_slug(slug: str) -> tuple[int, str]:
    return (1 if slug == "other" else 0, slug)


def render_operate_scenarios() -> str:
    """Build context and render operate/scenarios.j2."""
    from profile_manager.data.scenarios import _list_scenarios_for_compare
    from profile_manager.templating import render

    raw = _list_scenarios_for_compare()
    rows = []
    for entry in raw:
        family = _scenario_family(entry["id"])
        rows.append({
            "id": entry["id"],
            "title": entry.get("title") or entry["id"],
            "runtime": entry.get("runtime") or "",
            "family": family,
        })

    family_slugs = sorted({row["family"] for row in rows}, key=_sort_family_slug)
    # Family label is the slug itself — single-segment families (with
    # cardano-node as the one recognized compound) need no interpunct
    # display variant.
    families = [
        {"slug": slug, "label": slug}
        for slug in family_slugs
    ]

    return render(
        "operate/scenarios.j2",
        page_title="Scenarios",
        density="dense",        active="operate",
        active_sub="scenarios",
        total=len(rows),
        families=families,
        rows=rows,
    )
