"""Read-only data extractor for /operate/notifications (slice 6).

Walks ``state/config.yaml``'s ``notifications:`` section + the
``state/notifications.log`` ndjson tail to surface:

- Which event types have handlers wired (on_scenario_fail,
  on_coverage_regression, on_assertion_population_shift).
- Per-handler row: kind (webhook / slack / email), endpoint summary
  (URL host masked / "smtp://..."), last-fired timestamp if any.

Never reveals full URLs (host-only) so an operator screenshot doesn't
leak a webhook secret.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def _state_dir() -> Path:
    env = os.environ.get("ADA2_DWARF_STATE_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "state"


def _mask_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        host = parts.netloc or url
    except ValueError:
        host = url
    return f"{(parts.scheme or 'https')}://{host}/…" if host else url


def _summary_for(handler: dict[str, Any]) -> str:
    h_type = handler.get("type")
    if h_type == "webhook" or h_type == "slack":
        return _mask_url(handler.get("url") or "")
    if h_type == "email":
        to = handler.get("to") or "—"
        return f"smtp -> {to}"
    return ""


def _read_last_fired(state_dir: Path) -> dict[str, str]:
    """Most-recent timestamp per (event, handler-kind) pair from
    state/notifications.log. Returns ``{(event, type): ts}``."""
    log_path = state_dir / "notifications.log"
    if not log_path.is_file():
        return {}
    last: dict[str, str] = {}
    try:
        with log_path.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = f"{rec.get('event','?')}|{rec.get('type','?')}"
                ts = rec.get("ts") or ""
                # File is appended in chronological order; later wins.
                if ts:
                    last[key] = ts
                else:
                    last[key] = last.get(key, "")
    except OSError:
        return {}
    return last


def operate_notifications_payload() -> dict[str, Any]:
    try:
        from profile_manager.data.notifications import (
            load_notification_config,
            SUPPORTED_EVENTS,
        )
    except ImportError:
        SUPPORTED_EVENTS = ("on_scenario_fail", "on_coverage_regression", "on_assertion_population_shift")
        load_notification_config = None  # type: ignore

    cfg: dict[str, Any]
    if load_notification_config is not None:
        try:
            cfg = load_notification_config()
        except Exception:  # noqa: BLE001
            cfg = {}
    else:
        cfg = {}

    state_dir = _state_dir()
    last_fired = _read_last_fired(state_dir)

    rules: list[dict[str, Any]] = []
    for event in SUPPORTED_EVENTS:
        handlers = cfg.get(event) or []
        if not handlers:
            rules.append({
                "event": event,
                "type": "—",
                "summary": "no handler configured",
                "last_fired": last_fired.get(f"{event}|webhook")
                              or last_fired.get(f"{event}|slack")
                              or last_fired.get(f"{event}|email") or "",
                "configured": False,
            })
            continue
        for h in handlers:
            rules.append({
                "event": event,
                "type": h.get("type") or "?",
                "summary": _summary_for(h),
                "last_fired": last_fired.get(f"{event}|{h.get('type','')}", ""),
                "configured": True,
            })

    smtp = cfg.get("smtp") or {}
    return {
        "rules": rules,
        "smtp_configured": bool(smtp.get("host")),
        "smtp_host": smtp.get("host") or "",
        "smtp_port": smtp.get("port") or "",
        "log_path": str(state_dir / "notifications.log"),
    }
