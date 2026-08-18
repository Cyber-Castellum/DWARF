"""View for the /operate/antithesis page — the GUI Antithesis pipeline."""

from __future__ import annotations

from profile_manager.data.operate_antithesis import operate_antithesis_payload
from profile_manager.templating import render


def render_operate_antithesis(token: str | None = None) -> str:
    payload = operate_antithesis_payload()
    return render(
        "operate/antithesis.j2",
        page_title="Antithesis",
        density="reading",
        active="operate",
        active_sub="antithesis",
        profiles=payload["profiles"],
        scenarios=payload["scenarios"],
        asset_dirs=payload["asset_dirs"],
        moog_configured=payload["moog_configured"],
        build_out_root=payload["build_out_root"],
        token=token,
    )
