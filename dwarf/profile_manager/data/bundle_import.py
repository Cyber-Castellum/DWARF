"""Multipart parsing + bundle import handler for /api/bundle/import.

Slice 3 of dispatch 7 — operators upload a previously-exported bundle
``tar.gz``; the framework writes it to a tmp path and runs the
existing ``scripts/bundle_import.py`` helper to verify + unpack into
``runs/<run_id>/``.

The multipart parser handles only the single-file case (one
``<input type="file" name="bundle">``) — the form is dashboard-internal
and won't see other shapes.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from html import escape
from pathlib import Path


def _runs_dir() -> Path:
    env = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "runs"


def _extract_boundary(content_type: str) -> str | None:
    m = re.search(r"boundary=([^;]+)", content_type)
    if not m:
        return None
    return m.group(1).strip().strip('"')


def _parse_single_file_part(body: bytes, boundary: str) -> tuple[str | None, bytes | None, str | None]:
    """Return (filename, file_bytes, error). Handles the multipart form
    body produced by a single file input. The boundary line is
    ``--<boundary>`` and the trailer is ``--<boundary>--``."""
    sep = b"--" + boundary.encode("ascii")
    parts = body.split(sep)
    # First slice is the preamble (typically empty); each subsequent
    # part starts with \r\n then headers \r\n\r\n then content. Last
    # part is the trailer ``--``.
    for part in parts:
        if not part or part in (b"--", b"--\r\n"):
            continue
        # Strip leading \r\n
        if part.startswith(b"\r\n"):
            part = part[2:]
        # Find headers/body split
        head_split = part.find(b"\r\n\r\n")
        if head_split == -1:
            continue
        headers = part[:head_split].decode("utf-8", errors="replace")
        content = part[head_split + 4:]
        # Trim trailing \r\n that precedes the next boundary marker
        if content.endswith(b"\r\n"):
            content = content[:-2]
        # Look for filename + name
        if 'name="bundle"' not in headers:
            continue
        m = re.search(r'filename="([^"]*)"', headers)
        filename = m.group(1) if m else "uploaded.tar.gz"
        return filename, content, None
    return None, None, "no bundle field found in upload"


def _safe_filename(name: str) -> str:
    base = Path(name).name
    # Allow alphanumerics + a small punctuation set; everything else collapses to _.
    return re.sub(r"[^A-Za-z0-9._-]", "_", base) or "uploaded.tar.gz"


def _result_page(title: str, body: str, *, status: int) -> tuple[int, str]:
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Dwarf — Bundle import</title>"
        "<link rel='stylesheet' href='/static/css/tokens.css'>"
        "<link rel='stylesheet' href='/static/css/themes.css'>"
        "<link rel='stylesheet' href='/static/css/base.css'>"
        "</head><body data-density='reading'>"
        f"<main class='shell-main'><span class='eyebrow'>Operate · Bundles</span><h1>{escape(title)}</h1>"
        f"{body}"
        "<p><a href='/operate/bundles'>↩ back to bundles</a></p>"
        "</main></body></html>"
    )
    return status, html


def handle_bundle_import_post(content_type: str, body: bytes) -> tuple[int, str]:
    """Top-level handler. Returns (status, html). Errors render an HTML
    page rather than raw text so the form submission lands somewhere
    readable."""
    boundary = _extract_boundary(content_type)
    if not boundary:
        return _result_page(
            "Import failed",
            "<p class='status-tile__errors-raw'>Missing multipart boundary in Content-Type header.</p>",
            status=400,
        )
    filename, content, err = _parse_single_file_part(body, boundary)
    if err:
        return _result_page("Import failed", f"<p class='status-tile__errors-raw'>{escape(err)}</p>", status=400)
    if content is None or not content:
        return _result_page("Import failed", "<p class='status-tile__errors-raw'>Empty upload.</p>", status=400)
    filename = _safe_filename(filename or "uploaded.tar.gz")
    # Persist the upload and call the bundle_import script.
    base = _runs_dir()
    runs_dir = base
    helper = Path(__file__).resolve().parents[2] / "scripts" / "bundle_import.py"
    if not helper.is_file():
        return _result_page(
            "Import unavailable",
            f"<p class='status-tile__errors-raw'>Helper script not found at {escape(str(helper))}. Use the CLI: <code>cardano-profile bundle import &lt;path&gt;</code>.</p>",
            status=503,
        )
    tmp = tempfile.NamedTemporaryFile(prefix="dwarf-bundle-import-", suffix="-" + filename, delete=False)
    try:
        tmp.write(content)
        tmp.close()
        proc = subprocess.run(
            [sys.executable, str(helper), tmp.name, "--runs-dir", str(runs_dir)],
            text=True, capture_output=True, check=False,
        )
        ok = proc.returncode == 0
        body_html = (
            f"<p class='page-subhero'>Uploaded {escape(filename)} ({len(content)} bytes), "
            f"helper exit {proc.returncode}.</p>"
            f"<h2>stdout</h2><pre class='run-payload-pre'>{escape(proc.stdout) or '(empty)'}</pre>"
            f"<h2>stderr</h2><pre class='run-payload-pre'>{escape(proc.stderr) or '(empty)'}</pre>"
        )
        return _result_page(
            "Import succeeded" if ok else "Import failed",
            body_html,
            status=200 if ok else 502,
        )
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
