"""View for /operate/plugins."""
from __future__ import annotations

from profile_manager.data.operate_plugins import operate_plugins_payload
from profile_manager.templating import render


def render_operate_plugins() -> str:
    payload = operate_plugins_payload()
    return render(
        "operate/plugins.j2",
        page_title="Plugins",
        density="reading",
        active="operate",
        active_sub="plugins",
        plugins=payload["plugins"],
        plugin_roots=payload["plugin_roots"],
        default_plugin_root=payload["default_plugin_root"],
        plugins_env=payload["plugins_env"],
        expected_api_version=payload["expected_api_version"],
        total_primitives=payload["total_primitives"],
    )
