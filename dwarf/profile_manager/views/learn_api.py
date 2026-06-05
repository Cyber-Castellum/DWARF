"""View for /learn/api (slice 2 of dispatch 8)."""
from __future__ import annotations

from profile_manager.data.learn_api import api_payload
from profile_manager.templating import render


def render_learn_api() -> str:
    payload = api_payload()
    return render(
        "learn/api.j2",
        page_title="REST API",
        density="reading",
        active="learn",
        active_sub="api",
        endpoints=payload["endpoints"],
        html_route_groups=payload["html_route_groups"],
        html_count=payload["html_count"],
        machine_readable_count=payload["machine_readable_count"],
        json_count=payload["json_count"],
        sse_count=payload["sse_count"],
        binary_count=payload["binary_count"],
    )
