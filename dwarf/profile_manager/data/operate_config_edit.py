"""Data + POST handler for /operate/config/edit — edit deployment settings.

Renders every field in CONFIG_FIELDS as an editable control and writes the
result back to the config overlay via the existing config API (so type
validation is the single source of truth in config.py, not duplicated here).
"""
from __future__ import annotations

import json
from html import escape
from urllib.parse import parse_qs

from profile_manager.config import (
    CONFIG_FIELDS,
    DeploymentConfig,
    config_path,
    load_config,
    parse_config_value,
    save_config,
)


def _display_value(type_name: str, raw) -> str:
    if type_name == "array[string]":
        return ", ".join(raw or [])
    if type_name in ("array[object]", "object"):
        return json.dumps(raw if raw is not None else ([] if type_name.startswith("array") else {}), indent=2)
    if type_name == "boolean":
        return "true" if raw else "false"
    return "" if raw is None else str(raw)


def _field_kind(type_name: str) -> str:
    if type_name == "boolean":
        return "bool"
    if type_name == "integer":
        return "int"
    if type_name in ("array[object]", "object"):
        return "textarea"
    return "text"  # string, array[string]


# Fields whose value steers the SSH control channel / privileges — surfaced with
# a warning so an operator knows a bad edit can break deploy/coverage.
_SENSITIVE = {"host", "ssh_user", "ssh_key_path", "remote_base_path", "allow_sudo", "allow_prereq_install"}

# The `moog` block stores secrets (github_pat, etc.). It is intentionally NOT
# editable here — echoing it into the form would leak those secrets into the
# page HTML. It has its own secret-aware panel on /operate/config. Excluded from
# both display and save so its stored values are preserved untouched.
_EXCLUDED = {"moog"}


def config_edit_payload() -> dict:
    cfg = load_config()
    values = cfg.to_dict()
    fields = []
    for key, meta in CONFIG_FIELDS.items():
        if key in _EXCLUDED:
            continue
        type_name = meta["type"]
        fields.append({
            "key": key,
            "type": type_name,
            "description": meta["description"],
            "value": _display_value(type_name, values.get(key)),
            "kind": _field_kind(type_name),
            "sensitive": key in _SENSITIVE,
        })
    return {"fields": fields, "config_path": str(config_path())}


def _result(status: int, title: str, body_html: str, *, ok: bool):
    from profile_manager.templating import render
    html = render(
        "operate/_action_result.j2",
        page_title=title,
        active="operate",
        active_sub="config",
        eyebrow="Operate · Settings",
        result_title=title,
        body_html=body_html,
        ok=ok,
        back_href="/operate/config?token=dwarf",
        back_label="Back to settings",
    )
    return status, html


def handle_config_save(body_bytes: bytes) -> tuple[int, str]:
    form = parse_qs(body_bytes.decode("utf-8", errors="replace"), keep_blank_values=True)
    try:
        values = load_config().to_dict()
    except Exception as exc:  # noqa: BLE001 - surface, don't 500
        return _result(500, "Settings not saved", f"<p>Could not load current config: <code>{escape(str(exc))}</code></p>", ok=False)

    changed = []
    for key, meta in CONFIG_FIELDS.items():
        if key in _EXCLUDED or key not in form:
            continue
        raw = (form.get(key) or [""])[0]
        try:
            new_val = parse_config_value(key, raw)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            return _result(400, "Settings not saved",
                           f"<p>Invalid value for <code>{escape(key)}</code> "
                           f"(<code>{escape(meta['type'])}</code>): {escape(str(exc))}</p>",
                           ok=False)
        if new_val != values.get(key):
            changed.append(key)
        values[key] = new_val

    try:
        path = save_config(DeploymentConfig.from_dict(values))
    except Exception as exc:  # noqa: BLE001
        return _result(500, "Settings not saved", f"<p>Write failed: <code>{escape(str(exc))}</code></p>", ok=False)

    if changed:
        body = (f"<p>Wrote <code>{escape(str(path))}</code>.</p>"
                f"<p>Updated {len(changed)} setting(s): <code>{escape(', '.join(changed))}</code>. "
                f"New values are live immediately.</p>")
    else:
        body = (f"<p>Wrote <code>{escape(str(path))}</code> — no values changed.</p>")
    return _result(200, "Settings saved", body, ok=True)
