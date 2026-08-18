"""View for /operate/profiles/new — create a deployment profile."""

from __future__ import annotations

from profile_manager.data.operate_profiles_new import profiles_new_payload
from profile_manager.templating import render


def render_operate_profiles_new(token: str | None = None) -> str:
    payload = profiles_new_payload()
    return render(
        "operate/profiles_new.j2",
        page_title="New profile",
        density="reading",
        active="operate",
        active_sub="profiles",
        templates=payload["templates"],
        profile_root=payload["profile_root"],
        token=token,
    )
