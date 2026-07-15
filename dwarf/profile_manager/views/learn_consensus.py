"""View for /learn/consensus — the cross-implementation chain-selection differential.

Serves the self-contained consensus differential status/capability page (cardano-node
vs Amaru: does chain selection ever diverge?) as its own page.
"""
from __future__ import annotations

from profile_manager.templating import TEMPLATES_DIR

_CONSENSUS = TEMPLATES_DIR.parent / "static" / "consensus-differential.html"


def render_learn_consensus() -> str:
    try:
        return _CONSENSUS.read_text(encoding="utf-8")
    except OSError:
        return "<!doctype html><meta charset=utf-8><title>Consensus differential</title><p>Consensus differential page unavailable.</p>"
