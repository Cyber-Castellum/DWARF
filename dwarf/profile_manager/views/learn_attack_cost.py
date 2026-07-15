"""View for /learn/attack-cost — a live 'cost to attack Cardano' ticker.

Computed client-side from Koios (on-chain stake/supply) + CoinGecko (ADA price),
with a baked snapshot fallback when the live feed is blocked. Educational; makes
the effective-threshold (~40% of active stake, not 51% of total supply) tangible.
"""
from __future__ import annotations

from profile_manager.templating import render


def render_learn_attack_cost() -> str:
    return render(
        "learn/attack_cost.j2",
        page_title="Attack cost (live)",
        density="reading",
        active="learn",
        active_sub="attack-cost",
    )
