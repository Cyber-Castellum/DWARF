"""Data + create handler for /operate/scenarios/new (slice 7)."""
from __future__ import annotations

import os
import re
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")


def _scenarios_dir() -> Path:
    env = os.environ.get("ADA2_DWARF_SCENARIOS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "scenarios"


def list_templates_with_preview() -> list[dict[str, Any]]:
    """Walk scenarios/templates/*.yaml and return per-template metadata
    + a body preview that the picker can show in a disclosure."""
    try:
        from profile_manager.scenario_templates import TEMPLATES_DIR
    except ImportError:
        TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "scenarios" / "templates"

    out: list[dict[str, Any]] = []
    if not TEMPLATES_DIR.is_dir():
        return out
    for path in sorted(TEMPLATES_DIR.glob("*.yaml")):
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        out.append({
            "slug": path.stem,
            "label": path.stem.replace("-", " "),
            "preview": body,
            "size_bytes": path.stat().st_size,
        })
    return out


def render_template_preview(template_slug: str, scenario_name: str) -> str:
    """Substitute SCENARIO_ID + SCENARIO_TITLE without writing to disk."""
    try:
        from profile_manager.scenario_templates import TEMPLATES_DIR
    except ImportError:
        TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "scenarios" / "templates"
    p = TEMPLATES_DIR / f"{template_slug}.yaml"
    if not p.is_file():
        return ""
    body = p.read_text(encoding="utf-8")
    name = scenario_name or "<unnamed>"
    return body.replace("{{SCENARIO_ID}}", name).replace("{{SCENARIO_TITLE}}", name)


def is_valid_scenario_name(name: str) -> bool:
    return bool(_NAME_RE.match(name or ""))


def handle_create_post(body: bytes) -> tuple[int, str]:
    """Parse a POSTed urlencoded form and write scenarios/<name>.yaml.

    Returns (status, html_body). 200 on success, 400 on bad input,
    409 when the target file exists.
    """
    try:
        params = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError:
        return _result(400, "Bad Request", "form body was not valid utf-8")
    template = (params.get("template") or [""])[0]
    name = (params.get("name") or [""])[0].strip().lower()
    if not template:
        return _result(400, "Missing template", "Pick a template from the list.")
    if not is_valid_scenario_name(name):
        return _result(
            400,
            "Bad scenario name",
            "Names must match <code>[a-z0-9][a-z0-9._-]{2,79}</code> "
            "(lowercase letters / digits / dot / underscore / dash, 3–80 chars).",
        )
    target_dir = _scenarios_dir()
    target = target_dir / f"{name}.yaml"
    if target.exists():
        return _result(409, "Already exists", f"<code>{escape(str(target))}</code> already exists. Pick a different name or delete the existing file.")
    try:
        from profile_manager.scenario_templates import render_template
    except ImportError:
        return _result(503, "Templates unavailable",
                       "scenario_templates module is not available in this deployment.")
    try:
        written = render_template(template_name=template, scenario_name=name, output_path=target)
    except FileNotFoundError as exc:
        return _result(400, "Unknown template", str(exc))
    except Exception as exc:  # noqa: BLE001
        return _result(500, "Write failed", str(exc))

    body_html = (
        f"<p class='page-subhero'>Wrote <code>{escape(str(written))}</code> "
        f"({written.stat().st_size} bytes) from template <code>{escape(template)}</code>.</p>"
        f"<p class='page-subhero'>Hot-reload picks it up on the next "
        f"<a href='/operate/scenarios'>scenarios page</a> request.</p>"
    )
    return _result(200, "Scenario created", body_html, ok=True)


def _result(status: int, title: str, body_html: str, *, ok: bool = False) -> tuple[int, str]:
    page = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>Dwarf — {escape(title)}</title>"
        "<link rel='stylesheet' href='/static/css/tokens.css'>"
        "<link rel='stylesheet' href='/static/css/themes.css'>"
        "<link rel='stylesheet' href='/static/css/base.css'>"
        "</head><body data-density='reading'>"
        f"<main class='shell-main'><span class='eyebrow'>Operate · Scenarios · New</span><h1>{escape(title)}</h1>"
        f"{body_html}"
        "<p><a href='/operate/scenarios/new'>↩ pick another template</a> · "
        "<a href='/operate/scenarios'>scenarios</a></p>"
        "</main></body></html>"
    )
    return status, page
