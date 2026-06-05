"""Sub-nav route catalogues for /operate/* and /learn/*.

The primary nav (`Operate` | `Learn`) lives in _base.j2 verbatim; the
sub-nav is a per-section row beneath it that highlights the current
sub-route. Slice 34 introduces this module as the single source of truth
for the chip ordering on each section.

Adding a new sub-page is two lines: one entry here + the matching view
passing `active_sub=<slug>` to render(). The chip ordering follows the
operate-landing tile order so operators learn the navigation hierarchy
in one place.
"""
from __future__ import annotations

from typing import Any


OPERATE_SUB_NAV: list[dict[str, str]] = [
    {"slug": "overview", "label": "Overview", "url": "/operate"},
    {"slug": "runs", "label": "Runs", "url": "/operate/runs"},
    {"slug": "scenarios", "label": "Scenarios", "url": "/operate/scenarios"},
    {"slug": "compare", "label": "Compare", "url": "/operate/compare"},
    {"slug": "bundles", "label": "Bundles", "url": "/operate/bundles"},
    {"slug": "targets", "label": "Targets", "url": "/operate/targets"},
    {"slug": "profiles", "label": "Profiles", "url": "/operate/profiles"},
    {"slug": "status", "label": "Status", "url": "/operate/status"},
    {"slug": "coverage", "label": "Coverage", "url": "/operate/coverage"},
    {"slug": "crashes", "label": "Crashes", "url": "/operate/crashes"},
    {"slug": "schedule", "label": "Schedule", "url": "/operate/schedule"},
    {"slug": "audit", "label": "Audit", "url": "/operate/audit"},
    {"slug": "timeline", "label": "Timeline", "url": "/operate/timeline"},
    {"slug": "static-analysis", "label": "Static analysis", "url": "/operate/static-analysis"},
    {"slug": "plugins", "label": "Plugins", "url": "/operate/plugins"},
    {"slug": "config", "label": "Config", "url": "/operate/config"},
    {"slug": "notifications", "label": "Notifications", "url": "/operate/notifications"},
]

LEARN_SUB_NAV: list[dict[str, str]] = [
    {"slug": "overview", "label": "Overview", "url": "/learn"},
    {"slug": "getting-started", "label": "Getting started", "url": "/learn/getting-started"},
    {"slug": "examples", "label": "Examples", "url": "/learn/examples"},
    {"slug": "concepts", "label": "Glossary", "url": "/learn/concepts"},
    {"slug": "glossary", "label": "Glossary (audit)", "url": "/learn/glossary"},
    {"slug": "walkthroughs", "label": "Walkthroughs", "url": "/learn/walkthroughs"},
    {"slug": "architecture", "label": "Architecture", "url": "/learn/architecture"},
    {"slug": "api", "label": "API", "url": "/learn/api"},
    {"slug": "faq", "label": "FAQ", "url": "/learn/faq"},
    {"slug": "troubleshooting", "label": "Troubleshooting", "url": "/learn/troubleshooting"},
    {"slug": "operator-runbook", "label": "Operator runbook", "url": "/learn/operator-runbook"},
    {"slug": "developer-onboarding", "label": "Developer onboarding", "url": "/learn/developer-onboarding"},
    {"slug": "plugin-authoring", "label": "Plugin authoring", "url": "/learn/plugin-authoring"},
    {"slug": "coverage", "label": "Coverage", "url": "/learn/coverage"},
    {"slug": "status", "label": "Status", "url": "/learn/status"},
    {"slug": "cli", "label": "CLI", "url": "/learn/cli"},
]


def operate_sub_nav() -> list[dict[str, str]]:
    """Defensive copy so the view layer never mutates the catalogue."""
    return [dict(entry) for entry in OPERATE_SUB_NAV]


def learn_sub_nav() -> list[dict[str, str]]:
    """Defensive copy so the view layer never mutates the catalogue."""
    return [dict(entry) for entry in LEARN_SUB_NAV]


def sub_nav_for(active: str | None) -> dict[str, Any]:
    """Return the chip list + slug-to-label map for the active section.

    `active` is the primary-nav token a view already passes ("operate"
    or "learn"); this helper centralises the lookup so _base.j2 only
    needs to read a single context variable rather than branching on
    two tokens.
    """
    if active == "operate":
        return {"section": "operate", "chips": operate_sub_nav()}
    if active == "learn":
        return {"section": "learn", "chips": learn_sub_nav()}
    return {"section": None, "chips": []}
