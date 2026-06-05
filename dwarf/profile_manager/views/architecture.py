"""Learn /architecture view."""
from __future__ import annotations

from profile_manager.data.architecture import (
    architecture_edges,
    architecture_nodes,
)
from profile_manager.templating import render


def render_learn_architecture() -> str:
    """Render /learn/architecture: top intro + inline SVG diagram + node detail rows."""
    return render(
        "learn/architecture.j2",
        page_title="Architecture",
        density="reading",        active="learn",
        active_sub="architecture",
        nodes=architecture_nodes(),
        edges=architecture_edges(),
    )
