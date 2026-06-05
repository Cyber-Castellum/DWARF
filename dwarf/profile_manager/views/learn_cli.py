"""Learn /cli view — operator-facing CLI documentation."""
from __future__ import annotations

from profile_manager.data.cli import cli_groups
from profile_manager.templating import render


def render_learn_cli() -> str:
    """Render /learn/cli: top-level command groups with worked examples."""
    return render(
        "learn/cli.j2",
        page_title="CLI",
        density="reading",        active="learn",
        active_sub="cli",
        groups=cli_groups(),
    )
