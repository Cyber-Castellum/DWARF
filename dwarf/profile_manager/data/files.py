"""Pure file/string utilities for the dashboard data layer."""
from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import quote


def _escape(value):
    return html.escape(str(value), quote=True)


def _read_text(path, limit=12000):
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(body) > limit:
        return body[:limit] + "\n..."
    return body


def _latest_files(root, patterns, count=8):
    files = []
    if not root.exists():
        return files
    for pattern in patterns:
        files.extend(root.glob(pattern))
    files = [path for path in files if path.is_file()]
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)[:count]


def _download_url(relative):
    return f"/deliverables/download?path={quote(relative, safe='')}"


def _pdf_url(relative):
    return f"/deliverables/pdf?path={quote(relative, safe='')}"


def _best_existing(paths):
    # Imported lazily so the data package can be imported before the
    # dashboard module finishes its own initialization.
    from profile_manager.dashboard import PROJECT_ROOT

    for relative in paths:
        if (PROJECT_ROOT / relative).is_file():
            return relative
    return None


def _attachment_headers(filename):
    safe_name = Path(filename).name.replace("\\", "_").replace('"', "_")
    return {"Content-Disposition": f'attachment; filename="{safe_name}"'}
