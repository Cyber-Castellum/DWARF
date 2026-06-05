"""Read-only config view (slice 4 of dispatch 7).

Walks ``CONFIG_FIELDS`` (the canonical schema) and projects each
known key as a row with name / current value / default / source /
description. Source is one of:

- ``env``        — overridden by an environment variable
- ``config-file``— set in state/config.yaml (differs from default)
- ``default``    — fallback to the schema default

The dashboard never mutates config; editing happens via
``cardano-profile config set <key> <value>`` from a terminal.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


# Best-effort env-var overrides matching ada3's CLI naming convention.
_ENV_PREFIX = "DWARF_"


def _read_config_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    # The state/config.yaml is JSON-formatted in practice. Try JSON first;
    # fall back to a flat key:value parse.
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    out: dict[str, Any] = {}
    for line in text.splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def operate_config_payload() -> dict[str, Any]:
    try:
        from profile_manager.config import CONFIG_FIELDS, config_path
        cfg_path = config_path()
    except ImportError:
        # Older deployments don't ship CONFIG_FIELDS — fall back to a
        # state-dir lookup and derive the schema from the file contents.
        CONFIG_FIELDS = {}
        cfg_path = (Path(os.environ.get("ADA2_DWARF_STATE_DIR")
                          or Path(__file__).resolve().parents[2] / "state")
                    / "config.yaml")

    file_data = _read_config_file(cfg_path)
    rows: list[dict[str, Any]] = []
    for key, meta in CONFIG_FIELDS.items():
        env_key = _ENV_PREFIX + key.upper()
        env_value = os.environ.get(env_key)
        default = meta.get("default")
        if env_value is not None:
            value = env_value
            source = "env"
        elif key in file_data:
            value = file_data[key]
            source = "config-file"
        else:
            value = default
            source = "default"
        rows.append({
            "key": key,
            "value": _stringify(value),
            "default": _stringify(default),
            "source": source,
            "type": meta.get("type") or "string",
            "description": meta.get("description") or "",
            "env_key": env_key,
        })
    # Surface unknown keys present in config.yaml so operators see them
    # even when CONFIG_FIELDS doesn't enumerate them yet.
    for key, value in file_data.items():
        if key in CONFIG_FIELDS:
            continue
        rows.append({
            "key": key,
            "value": _stringify(value),
            "default": "",
            "source": "config-file (unknown to schema)",
            "type": "unknown",
            "description": "",
            "env_key": _ENV_PREFIX + key.upper(),
        })
    return {
        "config_path": str(cfg_path),
        "config_present": cfg_path.is_file(),
        "rows": rows,
    }


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)
