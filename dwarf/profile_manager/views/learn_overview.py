"""View for /learn/overview — the full self-contained DWARF reference page.

Serves the pre-built self-contained HTML (hero + DSL / Antithesis / Primitives /
Coverage / Evidence / Extend / Attack-cost) as its own page. Returned raw (it is
a complete HTML document with its own styling and nav), not wrapped in the
dashboard shell.
"""
from __future__ import annotations

from profile_manager.templating import TEMPLATES_DIR

_OVERVIEW = TEMPLATES_DIR.parent / "static" / "overview.html"


def render_learn_overview() -> str:
    try:
        return _OVERVIEW.read_text(encoding="utf-8")
    except OSError:
        return "<!doctype html><meta charset=utf-8><title>Overview</title><p>Overview page unavailable.</p>"
