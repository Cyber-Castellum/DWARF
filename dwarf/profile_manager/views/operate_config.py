"""View for /operate/config (slice 4 of dispatch 7)."""
from __future__ import annotations

from profile_manager.data.operate_config import operate_config_payload
from profile_manager.templating import render


def render_operate_config(token: str | None = None) -> str:
    payload = operate_config_payload()
    return render(
        "operate/config.j2",
        page_title="Config",
        density="reading",
        active="operate",
        active_sub="config",
        config_path=payload["config_path"],
        config_present=payload["config_present"],
        rows=payload["rows"],
        moog_setup=payload["moog_setup"],
        token=token,
    )
