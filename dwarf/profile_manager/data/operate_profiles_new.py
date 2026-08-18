"""Data + POST handler for /operate/profiles/new — create a deployment profile.

Renders a profile template into a new ``<name>/profile.yaml`` under the writable
PROFILE_ROOT so it appears in /operate/profiles and can be deployed.
"""

from __future__ import annotations

import re
from html import escape
from urllib.parse import parse_qs

from profile_manager.profiles import PROFILE_ROOT

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")


def profiles_new_payload() -> dict:
    try:
        from profile_manager.profile_templates import list_templates
        templates = list_templates()
    except Exception:
        templates = []
    return {"templates": templates, "profile_root": str(PROFILE_ROOT)}


def is_valid_profile_name(name: str) -> bool:
    return bool(_NAME_RE.match(name or ""))


def _result(status: int, title: str, body_html: str, *, ok: bool = False) -> tuple[int, str]:
    from profile_manager.templating import render
    html = render(
        "operate/_action_result.j2",
        page_title=title,
        active="operate",
        active_sub="profiles",
        eyebrow="Operate · Profiles · New",
        result_title=title,
        body_html=body_html,
        ok=ok,
        back_href="/operate/profiles?token=dwarf",
        back_label="Back to profiles",
    )
    return status, html


def handle_create_post(body_bytes: bytes) -> tuple[int, str]:
    form = parse_qs(body_bytes.decode("utf-8", errors="replace"), keep_blank_values=True)
    template = (form.get("template") or [""])[0].strip()
    name = (form.get("name") or [""])[0].strip()
    if not template:
        return _result(400, "No template", "<p>Pick a profile template.</p>")
    if not is_valid_profile_name(name):
        return _result(400, "Invalid profile name",
                       "<p>Name must be lowercase letters/digits/dot/underscore/dash, 3–80 chars.</p>")
    output_path = PROFILE_ROOT / name / "profile.yaml"
    if output_path.exists():
        return _result(409, "Profile already exists",
                       f"<p><code>{escape(name)}</code> already exists. Existing profiles are not overwritten.</p>")
    try:
        from profile_manager.profile_templates import render_template
        render_template(template_name=template, profile_name=name, output_path=output_path)
    except FileNotFoundError as exc:
        return _result(404, "Unknown template", f"<p>{escape(str(exc))}</p>")
    except OSError as exc:
        return _result(500, "Cannot write profile",
                       f"<p>Profile dir not writable: <code>{escape(str(PROFILE_ROOT))}</code> ({escape(str(exc))}).</p>")
    return _result(200, "Profile created",
                   f"<p>Wrote <code>{escape(str(output_path))}</code>. It now appears in "
                   f"<a style='color:#2BE0E0' href='/operate/profiles?token=dwarf'>/operate/profiles</a>. "
                   f"Edit host-specific values, then deploy with <code>cardano-profile deploy {escape(name)}</code>.</p>",
                   ok=True)
