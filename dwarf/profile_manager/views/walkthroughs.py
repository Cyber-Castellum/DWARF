"""Learn /walkthroughs view."""
from __future__ import annotations

from profile_manager.data.walkthroughs import walkthrough_entries
from profile_manager.templating import render


def render_learn_walkthroughs() -> str:
    """Render /learn/walkthroughs: 4 stacked narrated paths."""
    walkthroughs = walkthrough_entries()
    return render(
        "learn/walkthroughs.j2",
        page_title="Walkthroughs",
        density="reading",        active="learn",
        active_sub="walkthroughs",
        walkthroughs=walkthroughs,
        empty=not walkthroughs,
    )
