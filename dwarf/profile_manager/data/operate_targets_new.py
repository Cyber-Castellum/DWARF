"""Data + POST handler for /operate/targets/new — register a decode target.

A target is a manifest (JSON, .yaml extension by convention) describing a
decoder binary. Registering one writes a manifest into the writable manifests
overlay so it appears in the /operate/targets catalog. (Running it additionally
needs the referenced binary present — same as any fuzz target.)
"""

from __future__ import annotations

import json
import re
from html import escape
from urllib.parse import parse_qs

from profile_manager.data.operate_targets import manifests_dir

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")

IMPLEMENTATIONS = ["amaru", "cardano-node"]
LANGUAGES = ["rust", "haskell"]
INPUT_FORMATS = ["stdin_bytes", "file_path", "argv"]
DECODER_TYPES = ["CBOR codec", "Mini-protocol decoder"]


def targets_new_payload() -> dict:
    return {
        "implementations": IMPLEMENTATIONS,
        "languages": LANGUAGES,
        "input_formats": INPUT_FORMATS,
        "decoder_types": DECODER_TYPES,
        "manifests_dir": str(manifests_dir()),
    }


def is_valid_target_id(tid: str) -> bool:
    return bool(_ID_RE.match(tid or ""))


def _result(status: int, title: str, body_html: str, *, ok: bool = False) -> tuple[int, str]:
    from profile_manager.templating import render
    html = render(
        "operate/_action_result.j2",
        page_title=title,
        active="operate",
        active_sub="targets",
        eyebrow="Operate · Targets · Register",
        result_title=title,
        body_html=body_html,
        ok=ok,
        back_href="/operate/targets?token=dwarf",
        back_label="Back to targets",
    )
    return status, html


def handle_register_post(body_bytes: bytes) -> tuple[int, str]:
    form = parse_qs(body_bytes.decode("utf-8", errors="replace"), keep_blank_values=True)

    def g(k):
        return (form.get(k) or [""])[0].strip()

    tid = g("id")
    if not is_valid_target_id(tid):
        return _result(400, "Invalid target id",
                       "<p>Id must be lowercase letters/digits/dot/underscore/dash, 3–80 chars.</p>")
    binary = g("binary")
    implementation = g("implementation") or "amaru"
    language = g("language") or "rust"
    input_format = g("input_format") or "stdin_bytes"
    decoder_type = g("decoder_type") or "CBOR codec"
    upstream_commit = g("upstream_commit") or "local"
    invariants = [ln.strip() for ln in g("invariants").splitlines() if ln.strip()]
    if not binary:
        return _result(400, "Missing binary", "<p>The <code>binary</code> path is required.</p>")

    manifest = {
        "id": tid,
        "binary": binary,
        "input_format": input_format,
        "implementation": implementation,
        "language": language,
        "upstream_commit": upstream_commit,
        "decoder_type": decoder_type,
        "invariants": invariants or ["no panic on bounded input"],
    }

    mdir = manifests_dir()
    try:
        mdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _result(500, "Cannot write manifest",
                       f"<p>Manifests dir not writable: <code>{escape(str(mdir))}</code> ({escape(str(exc))}).</p>")
    target_path = mdir / f"{tid}.yaml"
    if target_path.exists():
        return _result(409, "Target already exists",
                       f"<p><code>{escape(tid)}</code> is already registered. Existing manifests are not overwritten.</p>")
    target_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return _result(200, "Target registered",
                   f"<p>Wrote <code>{escape(str(target_path))}</code>. It now appears in the "
                   f"<a style='color:#2BE0E0' href='/operate/targets?token=dwarf'>targets catalog</a>. "
                   f"To run it, ensure the binary <code>{escape(binary)}</code> is present.</p>",
                   ok=True)
