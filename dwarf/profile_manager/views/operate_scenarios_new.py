"""View for /operate/scenarios/new (slice 7 of dispatch 7)."""
from __future__ import annotations

from urllib.parse import urlsplit, parse_qs

from profile_manager.data.operate_scenarios_new import (
    list_templates_with_preview,
    render_template_preview,
)
from profile_manager.templating import render


def render_operate_scenarios_new(query_string: str = "") -> str:
    qs = parse_qs(query_string, keep_blank_values=True)
    selected = (qs.get("template") or [""])[0]
    name = (qs.get("name") or [""])[0]
    templates = list_templates_with_preview()
    valid_slugs = {t["slug"] for t in templates}
    if selected not in valid_slugs:
        selected = templates[0]["slug"] if templates else ""
    preview = render_template_preview(selected, name) if selected else ""
    return render(
        "operate/scenarios_new.j2",
        page_title="New scenario",
        density="reading",
        active="operate",
        active_sub="scenarios",
        templates=templates,
        selected_template=selected,
        scenario_name=name,
        preview=preview,
    )
