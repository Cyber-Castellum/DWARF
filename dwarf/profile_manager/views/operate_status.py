"""Operate /status view — substrate health, active profile, configuration."""
from __future__ import annotations

from profile_manager.data.operate_status import (
    active_profile_tile,
    all_profiles,
    configuration_rows,
    dashboard_serving_tile,
    last_sync_tile,
    moog_status_tile,
    substrate_health,
    wallet_status_rows,
)
from profile_manager.templating import render


def render_operate_status(*, port: int | None = None, bind: str | None = None,
                          token: str | None = None) -> str:
    """Render /operate/status.

    The dashboard handler may pass in port/bind/token so the serving-tile
    reflects the live process; on first generation (static rendering) the
    tile renders with placeholder values and the page is still useful
    for the rest of the substrate state.
    """
    from profile_manager.dashboard import build_dashboard_status_payload
    payload = build_dashboard_status_payload(live=True)
    return render(
        "operate/status.j2",
        page_title="Status",
        density="reading",        active="operate",
        active_sub="status",
        substrate=substrate_health(payload),
        active_profile=active_profile_tile(payload),
        last_sync=last_sync_tile(payload),
        serving=dashboard_serving_tile(port=port, bind=bind, token=token),
        config_rows=configuration_rows(payload),
        profiles=all_profiles(payload),
        wallets=wallet_status_rows(payload),
        moog=moog_status_tile(payload),
    )
