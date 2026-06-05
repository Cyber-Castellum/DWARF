"""Operate /profiles view."""
from __future__ import annotations

from profile_manager.data.operate_profiles import operate_profile_entries
from profile_manager.templating import render


def render_operate_profiles() -> str:
    """Render /operate/profiles: stacked reading-mode profile entries."""
    entries = operate_profile_entries()
    return render(
        "operate/profiles.j2",
        page_title="Profiles",
        density="reading",        active="operate",
        active_sub="profiles",
        entries=entries,
        empty=not entries,
    )
