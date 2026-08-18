"""Operate /config/edit view — editable deployment settings form."""
from __future__ import annotations


def render_operate_config_edit(token: str | None = None) -> str:
    from profile_manager.data.operate_config_edit import config_edit_payload
    from profile_manager.templating import render

    payload = config_edit_payload()
    return render(
        "operate/config_edit.j2",
        page_title="Edit settings",
        active="operate",
        active_sub="config",
        token=token,
        fields=payload["fields"],
        config_path=payload["config_path"],
    )
