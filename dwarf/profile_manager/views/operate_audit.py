"""View for /operate/audit — retro-classification of preserved runs."""
from __future__ import annotations

from urllib.parse import parse_qs

from profile_manager.data.operate_audit import (
    CLASSIFICATIONS,
    apply_audit_filters,
    operate_audit_payload,
)
from profile_manager.templating import render


def render_operate_audit(query_string: str = "") -> str:
    qs = parse_qs(query_string, keep_blank_values=True)
    classification = (qs.get("classification") or [""])[0]
    family = (qs.get("family") or [""])[0][:128]
    if classification not in CLASSIFICATIONS:
        classification = ""

    payload = operate_audit_payload()
    filtered = apply_audit_filters(
        payload["rows"], classification=classification, family=family,
    )

    pills = []
    for c in CLASSIFICATIONS:
        pills.append({
            "slug": c,
            "label": c,
            "count": payload["counts"].get(c, 0),
            "active": classification == c,
        })
    pills.insert(0, {
        "slug": "", "label": "all",
        "count": payload["total"], "active": classification == "",
    })

    return render(
        "operate/audit.j2",
        page_title="Audit · retro-classification",
        density="dense",
        active="operate",
        active_sub="audit",
        rows=filtered,
        all_count=payload["total"],
        filtered_count=len(filtered),
        empty=payload["empty"],
        counts=payload["counts"],
        pills=pills,
        filter_classification=classification,
        filter_family=family,
    )
