"""Theme selection for the dashboard.

The default theme — ``forensic-noir`` — matches the brutalist obsidian
+ cyan-glow palette baked into ``tokens.css``. Alternate themes
override CSS variables under ``[data-theme="<slug>"]`` selectors in
``themes.css``.

The active theme is read from one of (highest priority first):

1. ``ADA2_DWARF_DASHBOARD_THEME`` env var (operator override).
2. ``dashboard_theme:`` field in ``state/config.yaml``.
3. ``forensic-noir`` (built-in default).

Unknown themes degrade silently to the default — no broken pages.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


SUPPORTED_THEMES = (
    "forensic-noir",
    "light-audit",
    "monochrome-print",
)


def _state_dir() -> Path:
    env = os.environ.get("ADA2_DWARF_STATE_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "state"


def _load_state_config() -> dict[str, Any]:
    path = _state_dir() / "config.yaml"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    # Extremely small key:value parser — the dashboard's state/config.yaml
    # is a flat map and bringing in PyYAML for one field would be heavy.
    out: dict[str, Any] = {}
    for line in text.splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def current_theme() -> str:
    env = os.environ.get("ADA2_DWARF_DASHBOARD_THEME")
    if env and env in SUPPORTED_THEMES:
        return env
    cfg = _load_state_config()
    theme = cfg.get("dashboard_theme")
    if theme in SUPPORTED_THEMES:
        return theme
    return "forensic-noir"
