"""Fuzz-target catalog for /operate/targets.

Walks dwarf/targets/manifests/*.yaml and reads raw JSON defensively to
extract the catalog fields (id, implementation, decoder_type, invariants,
upstream_commit). Mirrors slice-7 / slice-9 / slice-10 per-file enrichment;
malformed JSON or missing required field -> manifest silently dropped.

The _target_url helper is the single source of truth for target URLs.
Future cross-link consumers (e.g., a target column on /operate/runs) must
import the helper rather than inline /operate/targets# strings.

Curated columns (visible):
    id, implementation, decoder_type, invariants, upstream_commit

Deliberately omitted (anti-creep rails enforced by tests):
    binary           — verbose path noise (drill into manifest)
    input_format     — uniform "stdin_bytes" across all current manifests
    language         — derivable from implementation today (YAGNI)
    expected_outcomes — three-key dict; cluttered at catalog density;
                        future per-target page handles it
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _target_url(target_id: str) -> str:
    """Single source of truth for target detail URLs.

    Returns /operate/targets#<target_id> today; future
    /operate/targets/<target_id> sub-route migration is a one-line
    change to this function. Do NOT inline URL building elsewhere.
    """
    return f"/operate/targets#{target_id}"


def _enrich_target(manifest: dict[str, Any]) -> dict[str, Any]:
    """Translate a raw manifest dict into a render-ready row.

    Required fields (id, implementation) raise KeyError if missing — the
    walker catches that and drops the manifest from the listing.
    """
    target_id = manifest["id"]
    implementation = manifest["implementation"]
    invariants = list(manifest.get("invariants") or [])
    return {
        "id": target_id,
        "url": _target_url(target_id),
        "implementation": implementation,
        "decoder_type": manifest.get("decoder_type"),
        "invariants": invariants,
        "invariants_count": len(invariants),
        "upstream_commit": manifest.get("upstream_commit", "unknown"),
    }


DEFAULT_MANIFESTS_DIR = Path(__file__).resolve().parents[2] / "targets" / "manifests"

_M2_TARGET_MARKERS = ("-cbor-decode-", "-mini-protocol-decode-")


def _is_m2_delivery_target_id(target_id: str) -> bool:
    """True for target manifests that are directly in the June M2 serdes scope."""
    return any(marker in target_id for marker in _M2_TARGET_MARKERS)


def operate_target_rows(*, manifests_dir: Path | None = None) -> list[dict[str, Any]]:
    """Walk *.yaml under manifests_dir; return enriched rows.

    Defensive: each manifest is parsed in its own try/except. Malformed
    JSON, OSError, missing required key (id, implementation) -> manifest
    silently dropped. Order is alphabetical by filename (= alphabetical
    by id, since id matches filename by convention).
    """
    base = Path(manifests_dir) if manifests_dir is not None else DEFAULT_MANIFESTS_DIR
    if not base.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(base.glob("*.yaml")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            row = _enrich_target(data)
        except (json.JSONDecodeError, OSError, KeyError, ValueError):
            continue
        out.append(row)
    return out


def operate_m2_target_rows(*, manifests_dir: Path | None = None) -> list[dict[str, Any]]:
    """Return only target manifests that match the June M2 delivery scope.

    The full framework catalog remains available via operate_target_rows().
    The delivery dashboard uses this narrower list so it does not present
    future ledger, cargo-fuzz, or template harnesses as M2 deliverables.
    """
    return [
        row
        for row in operate_target_rows(manifests_dir=manifests_dir)
        if _is_m2_delivery_target_id(row["id"])
    ]


def implementation_pill_inventory(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter-pill set: all (default-active), amaru, cardano-node.

    Pills with zero matching rows still render. Other implementation
    values match only the all pill.
    """
    amaru_count = sum(1 for r in rows if r.get("implementation") == "amaru")
    cn_count = sum(1 for r in rows if r.get("implementation") == "cardano-node")
    return [
        {"slug": "", "label": "all", "count": len(rows), "active": True},
        {"slug": "amaru", "label": "amaru", "count": amaru_count, "active": False},
        {"slug": "cardano-node", "label": "cardano-node", "count": cn_count, "active": False},
    ]
