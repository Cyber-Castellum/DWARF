"""Data + POST handler for /operate/primitives/new — scaffold a new primitive.

Mirrors `cardano-profile primitive new`: generates the starter files (executor,
JSON schema, test, and the registry entry) into a writable scaffold directory
so a developer can drop them into the repo. It does NOT hot-register a live
primitive (the executor must be implemented + the image rebuilt first).
"""

from __future__ import annotations

import json
import os
import re
from html import escape
from pathlib import Path
from urllib.parse import parse_qs

FAMILIES = ["setup", "load", "probe", "assertion", "fault", "teardown"]
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,79}$")


def primitives_new_payload() -> dict:
    root = Path(os.environ.get("ADA2_DWARF_STATE_DIR") or "/var/dwarf/state") / "primitive-scaffold"
    return {"families": FAMILIES, "scaffold_root": str(root)}


def is_valid_primitive_name(name: str) -> bool:
    return bool(_NAME_RE.match(name or ""))


def _result(status: int, title: str, body_html: str, *, ok: bool = False) -> tuple[int, str]:
    from profile_manager.templating import render
    html = render(
        "operate/_action_result.j2",
        page_title=title,
        active="operate",
        active_sub="plugins",
        eyebrow="Operate · Plugins · New primitive",
        result_title=title,
        body_html=body_html,
        ok=ok,
        back_href="/operate/plugins?token=dwarf",
        back_label="Back to plugins",
    )
    return status, html


def _pre(text: str) -> str:
    return (f"<pre style='background:#04060a;border:1px solid rgba(43,224,224,.15);border-radius:10px;"
            f"padding:12px 14px;overflow-x:auto;font-family:ui-monospace;font-size:.8rem;color:#cfeaea'>"
            f"{escape(text)}</pre>")


def handle_create_post(body_bytes: bytes) -> tuple[int, str]:
    form = parse_qs(body_bytes.decode("utf-8", errors="replace"), keep_blank_values=True)
    family = (form.get("family") or [""])[0].strip()
    name = (form.get("name") or [""])[0].strip()
    if family not in FAMILIES:
        return _result(400, "Invalid family", f"<p>Family must be one of: {', '.join(FAMILIES)}.</p>")
    if not is_valid_primitive_name(name):
        return _result(400, "Invalid primitive name",
                       "<p>Name must start with a letter; lowercase letters/digits/underscore; 3–80 chars.</p>")

    root = Path(os.environ.get("ADA2_DWARF_STATE_DIR") or "/var/dwarf/state") / "primitive-scaffold" / name
    if root.exists():
        return _result(409, "Scaffold already exists",
                       f"<p><code>{escape(name)}</code> was already scaffolded at <code>{escape(str(root))}</code>.</p>")
    try:
        (root / "primitives").mkdir(parents=True, exist_ok=True)
        (root / "primitives" / "registry.json").write_text('{"primitives": {}}\n', encoding="utf-8")
        from scripts import primitive_scaffold
        result = primitive_scaffold.scaffold_primitive(repo_root=root, family=family, name=name)
    except Exception as exc:  # noqa: BLE001 - surface any scaffolding failure to the operator
        return _result(500, "Scaffold failed", f"<p>{escape(str(exc))}</p>")

    helper = Path(result["helper_path"]).read_text(encoding="utf-8")
    schema = Path(result["schema_path"]).read_text(encoding="utf-8")
    registry = json.loads(Path(result["registry_path"]).read_text(encoding="utf-8"))
    entry = registry.get("primitives", {}).get(name, {})

    body = (
        f"<p>Scaffolded under <code>{escape(str(root))}</code>. Copy these into the repo, implement the "
        f"executor, then rebuild the image to make the primitive live.</p>"
        f"<h3>dwarf/scripts/runtime_{escape(name)}.py</h3>{_pre(helper)}"
        f"<h3>dwarf/primitives/{escape(family)}/{escape(name)}.schema.json</h3>{_pre(schema)}"
        f"<h3>registry entry (merge into dwarf/primitives/registry.json)</h3>"
        f"{_pre(json.dumps({name: entry}, indent=2))}"
    )
    return _result(200, "Primitive scaffolded", body, ok=True)
