"""View for /operate/targets/new — register a decode target."""

from __future__ import annotations

from profile_manager.data.operate_targets_new import targets_new_payload
from profile_manager.templating import render


def render_operate_targets_new(token: str | None = None) -> str:
    payload = targets_new_payload()
    return render(
        "operate/targets_new.j2",
        page_title="Register target",
        density="reading",
        active="operate",
        active_sub="targets",
        implementations=payload["implementations"],
        languages=payload["languages"],
        input_formats=payload["input_formats"],
        decoder_types=payload["decoder_types"],
        manifests_dir=payload["manifests_dir"],
        token=token,
    )
