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

from collections import Counter
import json
from pathlib import Path
import re

from profile_manager.data.coverage import _cbor_shapes_in_text, _protocols_in_text
from profile_manager.data.scenarios import _list_scenarios_for_compare

_PAGE = Path(__file__).resolve().parent.parent / "data" / "threat_risk_coverage.html"


def render_learn_threat_coverage() -> str:
    html = _PAGE.read_text(encoding="utf-8")
    match = re.search(r"const DATA = (\{.*\});\nconst TYPES", html)
    if match is None:
        return html
    data = json.loads(match.group(1))
    baked = {item["id"]: item for item in data.get("scenarios") or []}
    current = []
    for row in _list_scenarios_for_compare():
        existing = baked.get(row["id"])
        if existing is not None:
            current.append(existing)
            continue
        raw = json.loads(Path(row["path"]).read_text(encoding="utf-8"))
        tags = list(raw.get("tags") or [])
        lowered = " ".join([row["id"], row.get("title") or "", *tags]).lower()
        if "primitive" in lowered:
            kind = "primitive"
        elif "fuzz" in lowered:
            kind = "fuzz"
        elif "smoke" in lowered:
            kind = "smoke"
        elif "baseline" in lowered:
            kind = "baseline"
        elif "demo" in lowered:
            kind = "demo"
        else:
            kind = "scripted"
        surfaces = set(_protocols_in_text(lowered))
        if _cbor_shapes_in_text(lowered):
            surfaces.add("cbor/parser")
        if "kes" in lowered:
            surfaces.add("consensus/chain-selection")
        if "bootstrap" in lowered:
            surfaces.update(("bootstrap/genesis", "consensus/chain-selection"))
        if not surfaces:
            surfaces.add("other")
        current.append({
            "id": row["id"],
            "title": row.get("title") or row["id"],
            "tags": tags,
            "type": kind,
            "ei": row.get("evidence_intent") or "",
            "target": row.get("target_impl") or "unknown",
            "surfaces": sorted(surfaces),
            "m1": row.get("m1_trace") or {},
        })
    data["scenarios"] = current
    data["meta"]["n_scen"] = len(current)
    data["meta"]["types"] = dict(Counter(item["type"] for item in current))
    encoded = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    return html[:match.start(1)] + encoded + html[match.end(1):]
