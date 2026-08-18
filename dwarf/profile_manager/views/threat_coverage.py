"""Learn > Threat/Risk coverage map.

Serves the self-contained scenario-coverage page that correlates every DWARF
scenario to the Amaru Risk Register v2 (RR) and Threat Model v2 (TM) via a
vetted per-concern mapping, with maturity/kind pills and explicit GAP rows.

The page is a complete, self-contained HTML document (own <head>/<style>/<script>,
data embedded inline) authored from dwarf/scenarios/*.yaml + JG-RiskRegister-V2.csv
+ JG-Threatmodel-V2.docx. It is returned verbatim — no dashboard chrome — because it
carries its own forensic-noir styling.
"""
from __future__ import annotations

from pathlib import Path

_PAGE = Path(__file__).resolve().parent.parent / "data" / "threat_risk_coverage.html"


def render_learn_threat_coverage() -> str:
    return _PAGE.read_text(encoding="utf-8")
