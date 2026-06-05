"""Webhook / notification framework (slice 49).

Operators register handlers in ``state/config.yaml`` under a
``notifications:`` key. Three event types are supported:

- ``on_scenario_fail`` — fires when a scenario completes with a fail
  exit_status.
- ``on_coverage_regression`` — fires when a coverage report regresses
  versus the prior baseline.
- ``on_assertion_population_shift`` — fires when an assertion's
  pass/fail population significantly changes.

Three handler types ship in this slice:

- ``webhook`` — POST to a URL with a JSON body.
- ``slack`` — POST to a Slack-compatible webhook URL with the
  Slack-formatted text payload.
- ``email`` — SMTP relay via the operator's existing config (host,
  port, from, to). Requires SMTP-host config; if missing the handler
  records a "skipped" outcome instead of crashing.

The dispatcher is sync but bounded: each handler has a 5-second
timeout. Failures are logged to ``state/notifications.log`` and never
propagate — a downstream outage must not break a scenario run.

Config schema (illustrative):

    notifications:
      on_scenario_fail:
        - type: webhook
          url: https://example.com/dwarf-hook
        - type: slack
          url: https://hooks.slack.com/services/T0/B0/XXX
      on_coverage_regression:
        - type: email
          to: ops@example.com
      on_assertion_population_shift: []
      smtp:
        host: smtp.example.com
        port: 587
        from: dwarf@example.com
"""
from __future__ import annotations

import json
import os
import smtplib
import socket
import ssl
import urllib.error
import urllib.request
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any


SUPPORTED_EVENTS = (
    "on_scenario_fail",
    "on_coverage_regression",
    "on_assertion_population_shift",
)
SUPPORTED_HANDLER_TYPES = ("webhook", "slack", "email")
DEFAULT_TIMEOUT_SECONDS = 5.0


def _state_dir() -> Path:
    env = os.environ.get("ADA2_DWARF_STATE_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "state"


def _load_yaml_simple(path: Path) -> dict[str, Any]:
    """Tiny YAML subset parser. Supports flat key:value pairs and
    indented sub-mappings + lists-of-dicts. Avoids a PyYAML dep.

    Layout supported:

        notifications:
          on_scenario_fail:
            - type: webhook
              url: https://...
            - type: slack
              url: https://...
          smtp:
            host: smtp.x
            port: 587
    """
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, raw.strip()))
    return _parse_block(lines, 0, 0)[0]


def _parse_block(lines: list[tuple[int, str]], start: int, indent: int) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    i = start
    while i < len(lines):
        ind, line = lines[i]
        if ind < indent:
            break
        if ind > indent:
            i += 1
            continue
        # ind == indent — this is a key in our block.
        key, sep, value = line.partition(":")
        if not sep:
            i += 1
            continue
        key = key.strip()
        value = value.strip()
        if value:
            out[key] = _parse_scalar(value)
            i += 1
            continue
        # Empty value — peek next non-empty line to decide container type.
        if i + 1 >= len(lines):
            out[key] = {}
            i += 1
            continue
        next_ind, next_line = lines[i + 1]
        if next_ind <= indent:
            out[key] = {}
            i += 1
            continue
        if next_line.startswith("- "):
            sub_list, consumed = _parse_list(lines, i + 1, next_ind)
            out[key] = sub_list
            i = consumed
        else:
            sub_map, consumed = _parse_block(lines, i + 1, next_ind)
            out[key] = sub_map
            i = consumed
    return out, i


def _parse_list(lines: list[tuple[int, str]], start: int, indent: int) -> tuple[list[Any], int]:
    out: list[Any] = []
    i = start
    while i < len(lines):
        ind, line = lines[i]
        if ind < indent:
            break
        if ind > indent or not line.startswith("- "):
            i += 1
            continue
        rest = line[2:].strip()
        entry: dict[str, Any] = {}
        if rest:
            ek, _, ev = rest.partition(":")
            entry[ek.strip()] = _parse_scalar(ev.strip())
        # Consume continuation keys: lines deeper than `indent` until next `- ` or shallower line.
        i += 1
        while i < len(lines):
            next_ind, next_line = lines[i]
            if next_ind <= indent:
                break
            if next_line.startswith("- "):
                break
            ek, _, ev = next_line.partition(":")
            if ek:
                entry[ek.strip()] = _parse_scalar(ev.strip())
            i += 1
        out.append(entry)
    return out, i


def _parse_scalar(value: str) -> Any:
    value = value.strip().strip('"').strip("'")
    if value.lower() in ("true", "yes"):
        return True
    if value.lower() in ("false", "no"):
        return False
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)
    return value


def _promote_lists(node: Any) -> Any:
    """Walk the tree; if a dict value contains only integer-keyed
    consecutive entries it stays a dict — but our parser already produces
    actual lists for `- ` syntax via append. This helper exists for any
    future cleanup; today it's a no-op."""
    return node


def load_notification_config() -> dict[str, Any]:
    """Read ``state/config.yaml`` and return the ``notifications:``
    sub-mapping (plus a top-level ``smtp:`` block if present). Empty
    dict when the file or the section is missing."""
    cfg = _load_yaml_simple(_state_dir() / "config.yaml")
    notif = cfg.get("notifications") or {}
    if isinstance(notif, dict):
        return {
            "on_scenario_fail": _to_handler_list(notif.get("on_scenario_fail")),
            "on_coverage_regression": _to_handler_list(notif.get("on_coverage_regression")),
            "on_assertion_population_shift": _to_handler_list(notif.get("on_assertion_population_shift")),
            "smtp": notif.get("smtp") or cfg.get("smtp") or {},
        }
    return {
        "on_scenario_fail": [],
        "on_coverage_regression": [],
        "on_assertion_population_shift": [],
        "smtp": {},
    }


def _to_handler_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [h for h in value if isinstance(h, dict) and h.get("type") in SUPPORTED_HANDLER_TYPES]
    return []


def _post_json(url: str, payload: dict[str, Any], *, timeout: float) -> tuple[bool, str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return (200 <= resp.status < 300, f"HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        return False, f"transport: {exc}"


def _format_slack_payload(event_type: str, body: dict[str, Any]) -> dict[str, Any]:
    """Slack incoming-webhook expects ``{text: "..."}`` (or ``blocks``).
    The single-text variant is the smallest portable surface."""
    title = event_type.replace("_", " ")
    summary = body.get("summary") or json.dumps(body, sort_keys=True)
    return {"text": f"*dwarf · {title}*\n```{summary}```"}


def _send_email(handler: dict[str, Any], smtp_cfg: dict[str, Any],
                event_type: str, body: dict[str, Any], *, timeout: float) -> tuple[bool, str]:
    host = smtp_cfg.get("host")
    if not host:
        return False, "skipped: smtp.host not configured"
    port = int(smtp_cfg.get("port") or 25)
    sender = smtp_cfg.get("from") or "dwarf@localhost"
    to = handler.get("to")
    if not to:
        return False, "skipped: handler.to not set"
    subject = f"[dwarf] {event_type}"
    msg = MIMEText(json.dumps(body, indent=2, sort_keys=True))
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    try:
        with smtplib.SMTP(host, port, timeout=timeout) as srv:
            if smtp_cfg.get("starttls"):
                srv.starttls(context=ssl.create_default_context())
            if smtp_cfg.get("username") and smtp_cfg.get("password"):
                srv.login(smtp_cfg["username"], smtp_cfg["password"])
            srv.sendmail(sender, [to], msg.as_string())
        return True, f"sent to {to}"
    except (smtplib.SMTPException, OSError) as exc:
        return False, f"smtp: {exc}"


def dispatch(event_type: str, body: dict[str, Any], *,
             config: dict[str, Any] | None = None,
             timeout: float = DEFAULT_TIMEOUT_SECONDS) -> list[dict[str, Any]]:
    """Fire every handler registered for ``event_type``. Returns one
    outcome dict per handler so callers / tests can verify dispatch.
    Failures never raise — a downstream outage must not break a run."""
    if event_type not in SUPPORTED_EVENTS:
        return [{"ok": False, "type": "unknown", "detail": f"unsupported event: {event_type}"}]
    cfg = config if config is not None else load_notification_config()
    handlers = cfg.get(event_type) or []
    smtp_cfg = cfg.get("smtp") or {}
    outcomes: list[dict[str, Any]] = []
    for handler in handlers:
        h_type = handler.get("type")
        if h_type == "webhook":
            ok, detail = _post_json(handler.get("url", ""), {"event": event_type, "body": body}, timeout=timeout)
        elif h_type == "slack":
            ok, detail = _post_json(handler.get("url", ""), _format_slack_payload(event_type, body), timeout=timeout)
        elif h_type == "email":
            ok, detail = _send_email(handler, smtp_cfg, event_type, body, timeout=timeout)
        else:
            ok, detail = False, f"unknown handler type: {h_type}"
        outcomes.append({"type": h_type, "ok": ok, "detail": detail})
    _log_outcomes(event_type, outcomes)
    return outcomes


def _log_outcomes(event_type: str, outcomes: list[dict[str, Any]]) -> None:
    """Append one ndjson line per outcome to state/notifications.log
    so operators can audit dispatch history."""
    if not outcomes:
        return
    log_path = _state_dir() / "notifications.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fp:
            for o in outcomes:
                fp.write(json.dumps({"event": event_type, **o}) + "\n")
    except OSError:
        pass
