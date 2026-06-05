"""View for /operate/notifications (slice 6 of dispatch 7)."""
from __future__ import annotations

from profile_manager.data.operate_notifications import operate_notifications_payload
from profile_manager.templating import render


def render_operate_notifications() -> str:
    payload = operate_notifications_payload()
    return render(
        "operate/notifications.j2",
        page_title="Notifications",
        density="reading",
        active="operate",
        active_sub="notifications",
        rules=payload["rules"],
        smtp_configured=payload["smtp_configured"],
        smtp_host=payload["smtp_host"],
        smtp_port=payload["smtp_port"],
        log_path=payload["log_path"],
    )
