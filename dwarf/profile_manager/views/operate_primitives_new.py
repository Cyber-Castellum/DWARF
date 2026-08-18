"""View for /operate/primitives/new — scaffold a new primitive."""

from __future__ import annotations

from profile_manager.data.operate_primitives_new import primitives_new_payload
from profile_manager.templating import render


def render_operate_primitives_new(token: str | None = None) -> str:
    payload = primitives_new_payload()
    return render(
        "operate/primitives_new.j2",
        page_title="New primitive",
        density="reading",
        active="operate",
        active_sub="plugins",
        families=payload["families"],
        scaffold_root=payload["scaffold_root"],
        token=token,
    )
