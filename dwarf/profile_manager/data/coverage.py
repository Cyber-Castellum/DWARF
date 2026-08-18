"""Auto-derived support matrices for /learn/coverage.

Every axis derives from real repo state at call time. No hand-curated
lists. Display names are mapped from slugs only for capitalization;
membership of each axis comes from scanning files on disk.
"""
from __future__ import annotations

import json
from pathlib import Path

# Display-name maps. Capitalization only — set membership is auto-derived.
_MINI_PROTOCOL_LABELS = {
    "chainsync":     "ChainSync",
    "blockfetch":    "BlockFetch",
    "txsubmission":  "TxSubmission",
    "handshake":     "Handshake",
    "keep-alive":    "KeepAlive",
    "keepalive":     "KeepAlive",
    "peersharing":   "PeerSharing",
    "localstatequery": "LocalStateQuery",
    "localtxsubmission": "LocalTxSubmission",
    "localtxmonitor": "LocalTxMonitor",
}

_FUZZER_BACKEND_LABELS = {
    "afl":         "AFL++",
    "cargo-fuzz":  "cargo-fuzz",
}


def _label_for(slug: str, mapping: dict[str, str]) -> str:
    return mapping.get(slug, slug.replace("-", " ").title())


def _project_root() -> Path:
    # data/coverage.py -> data/ -> profile_manager/ -> dwarf/ -> repo
    return Path(__file__).resolve().parents[3]


def _runs_root() -> Path:
    return _project_root() / "dwarf" / "runs"


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _protocol_slug_from_target_id(target_id: str | None) -> str | None:
    if not target_id:
        return None
    marker = "-mini-protocol-decode-"
    if marker not in target_id:
        return None
    return target_id.split(marker, 1)[1]


def _protocol_slug_from_text(text: str | None) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    candidates = [
        "localstatequery",
        "localtxsubmission",
        "localtxmonitor",
        "blockfetch",
        "chainsync",
        "txsubmission",
        "peersharing",
        "keepalive",
        "keep-alive",
        "handshake",
    ]
    for slug in candidates:
        if slug in lowered:
            return "keepalive" if slug == "keep-alive" else slug
    return None


def _protocols_in_text(text: str | None) -> set[str]:
    """Return every mini-protocol slug named anywhere in ``text``.

    Unlike ``_protocol_slug_from_text`` (first-match only), this returns the
    full set so a scenario touching several protocols counts under each.
    Node-to-client (local*) tokens are stripped before the node-to-node
    checks so their substrings ('txsubmission' inside 'localtxsubmission')
    don't leak a false node-to-node hit. Abbreviations are normalized:
    'txsub' -> 'txsubmission', 'keep-alive' -> 'keepalive'.
    """
    if not text:
        return set()
    t = text.lower()
    found: set[str] = set()
    for slug in ("localstatequery", "localtxsubmission", "localtxmonitor"):
        if slug in t:
            found.add(slug)
    tn = (
        t.replace("localtxsubmission", "")
        .replace("localtxmonitor", "")
        .replace("localstatequery", "")
    )
    if "blockfetch" in tn:
        found.add("blockfetch")
    if "chainsync" in tn:
        found.add("chainsync")
    if "peersharing" in tn:
        found.add("peersharing")
    if "handshake" in tn:
        found.add("handshake")
    if "keepalive" in tn or "keep-alive" in tn:
        found.add("keepalive")
    if "txsubmission" in tn or "txsub" in tn:
        found.add("txsubmission")
    return found


_CBOR_SHAPE_LABELS = {
    "auxiliary-data": "Auxiliary Data",
    "block":          "Block",
    "block-header":   "Block Header",
    "certificate":    "Certificate",
    "tx-body":        "Tx Body",
}


def _cbor_shapes_in_text(text: str | None) -> set[str]:
    """Return every CBOR decode-surface shape named in ``text``.

    Substring overlaps are handled explicitly: 'blockfetch' and
    'block-header' must not register as the 'block' shape, and the
    'cov-txsub' mini-protocol smoke must not register as 'tx-body'.
    """
    if not text:
        return set()
    t = text.lower()
    found: set[str] = set()
    if "auxiliary" in t:
        found.add("auxiliary-data")
    if "certificate" in t:
        found.add("certificate")
    if "tx-body" in t or ("cov-tx-" in t and "txsub" not in t):
        found.add("tx-body")
    if "block-header" in t or "cov-header" in t:
        found.add("block-header")
    # 'block' only after stripping the tokens that merely contain the
    # substring 'block' without being the block shape.
    stripped = t.replace("blockfetch", "").replace("block-header", "").replace("cov-header", "")
    if ("cbor" in t or "cov-block" in t) and ("block" in stripped or "cov-block" in t):
        found.add("block")
    return found


def _implementation_from_bundle_manifest(manifest: dict) -> str | None:
    target = manifest.get("target") or {}
    return target.get("implementation") or manifest.get("target_implementation")


def _target_id_from_bundle_manifest(manifest: dict) -> str | None:
    target = manifest.get("target") or {}
    return target.get("id") or manifest.get("target_id")


def _scenario_id_from_bundle_manifest(manifest: dict) -> str | None:
    scenario = manifest.get("scenario") or {}
    return scenario.get("id") or manifest.get("scenario_id")


def _mini_protocol_target_index(*, repo_root: Path | None = None) -> dict[tuple[str, str], str]:
    root = Path(repo_root) if repo_root is not None else _project_root()
    manifests_dir = root / "dwarf" / "targets" / "manifests"
    index: dict[tuple[str, str], str] = {}
    if not manifests_dir.is_dir():
        return index
    for path in manifests_dir.glob("*-mini-protocol-decode-*.yaml"):
        body = _read_json(path)
        if not body:
            continue
        target_id = body.get("id")
        implementation = body.get("implementation")
        protocol = _protocol_slug_from_target_id(target_id)
        if not target_id or not implementation or not protocol:
            continue
        index[(protocol, implementation)] = target_id
    return index


def mini_protocol_cell_evidence(*, runs_dir: Path | None = None, repo_root: Path | None = None) -> dict[tuple[str, str], dict]:
    """Per mini-protocol × implementation evidence index for /learn/coverage.

    Status semantics:
      no_run   — no matching coverage bundles observed
      ran_zero — a matching bundle exists but reported 0% bitmap coverage
      covered  — a matching bundle exists with >0 bitmap coverage
    """
    root = Path(repo_root) if repo_root is not None else _project_root()
    runs = Path(runs_dir) if runs_dir is not None else _runs_root()
    target_index = _mini_protocol_target_index(repo_root=root)
    evidence: dict[tuple[str, str], dict] = {
        key: {"target_id": target_id, "status": "no_run"}
        for key, target_id in target_index.items()
    }
    if not runs.is_dir():
        return evidence

    for run_dir in sorted((p for p in runs.iterdir() if p.is_dir()), key=lambda p: p.name):
        summary = _read_json(run_dir / "outputs" / "coverage-report" / "coverage-summary.json")
        if not summary:
            continue
        run_ts = summary.get("generated_at_utc") or run_dir.name
        for entry in summary.get("targets") or []:
            bundle_id = entry.get("bundle_id")
            if not bundle_id:
                continue
            bundle_manifest = _read_json(runs / bundle_id / "manifest.json") or {}
            implementation = _implementation_from_bundle_manifest(bundle_manifest)
            target_id = _target_id_from_bundle_manifest(bundle_manifest)
            protocol = (
                _protocol_slug_from_target_id(target_id)
                or _protocol_slug_from_text(_scenario_id_from_bundle_manifest(bundle_manifest))
                or _protocol_slug_from_text(entry.get("subcampaign_id"))
                or _protocol_slug_from_text(entry.get("target_label"))
            )
            if not implementation or not protocol:
                continue
            bitmap_pct = entry.get("bitmap_cvg")
            if bitmap_pct is None:
                score = entry.get("coverage_score")
                metric = entry.get("coverage_metric")
                if metric in {"bitmap_cvg", "bitmap_pct", "coverage_pct"} and score is not None:
                    bitmap_pct = score
            if bitmap_pct is None:
                continue
            try:
                bitmap_pct_value = float(bitmap_pct)
            except (TypeError, ValueError):
                continue
            key = (protocol, implementation)
            target_id = target_id or target_index.get(key)
            record = {
                "target_id": target_id,
                "status": "covered" if bitmap_pct_value > 0 else "ran_zero",
                "last_run_id": bundle_id,
                "last_run_ts": run_ts,
                "bitmap_pct": bitmap_pct_value,
            }
            existing = evidence.get(key)
            if existing is None or str(existing.get("last_run_ts") or "") <= str(run_ts):
                evidence[key] = record
    return evidence


def implementation_axis() -> list[dict]:
    """Distinct implementations across profiles, sorted alphabetically by slug."""
    from profile_manager.data.profiles import _profile_rows
    slugs = sorted({row["node_type"] for row in _profile_rows() if row.get("node_type")})
    return [{"slug": slug, "label": slug} for slug in slugs]


def mini_protocol_axis() -> list[dict]:
    """Mini-protocols named anywhere in any scenario id, sorted by slug.

    Derives from every scenario id (not just the strict ``-mini-protocol-*``
    naming), so runtime/substrate/coverage scenarios that exercise a protocol
    under a different id convention still surface a row.
    """
    from profile_manager.data.scenarios import _list_scenarios_for_compare
    found: set[str] = set()
    for entry in _list_scenarios_for_compare():
        found |= _protocols_in_text(entry["id"])
    return [
        {"slug": slug, "label": _label_for(slug, _MINI_PROTOCOL_LABELS)}
        for slug in sorted(found)
    ]


def fault_family_axis() -> list[dict]:
    """Fault families derived from dwarf/primitives/fault/*.schema.json filenames."""
    fault_dir = _project_root() / "dwarf" / "primitives" / "fault"
    if not fault_dir.is_dir():
        return []
    found = set()
    for p in fault_dir.glob("fault_*.schema.json"):
        stem = p.name.removesuffix(".schema.json")
        if stem.startswith("fault_"):
            stem = stem[len("fault_"):]
        if stem:
            found.add(stem)
    return [
        {"slug": slug, "label": slug.replace("_", " ").title().replace(" ", "-")}
        for slug in sorted(found)
    ]


def fuzzer_backend_axis() -> list[dict]:
    """Fuzzer backends derived from dwarf/scripts/*_campaign.py filenames."""
    scripts_dir = _project_root() / "dwarf" / "scripts"
    if not scripts_dir.is_dir():
        return []
    found = set()
    for p in scripts_dir.glob("*_campaign.py"):
        stem = p.stem.removesuffix("_campaign")
        slug = stem.replace("_", "-")
        if slug:
            found.add(slug)
    return [
        {"slug": slug, "label": _label_for(slug, _FUZZER_BACKEND_LABELS)}
        for slug in sorted(found)
    ]


def cbor_shape_axis() -> list[dict]:
    """CBOR target shapes derived from dwarf/targets/manifests/*-cbor-decode-*.yaml."""
    manifests_dir = _project_root() / "dwarf" / "targets" / "manifests"
    if not manifests_dir.is_dir():
        return []
    found = set()
    marker = "-cbor-decode-"
    for p in manifests_dir.glob("*-cbor-decode-*.yaml"):
        stem = p.stem
        idx = stem.find(marker)
        if idx >= 0:
            tail = stem[idx + len(marker):]
            if tail:
                found.add(tail)
    return [{"slug": slug, "label": slug} for slug in sorted(found)]


def _empty_cell() -> dict:
    """No run recorded for this (row, col) combination."""
    return {"count": 0, "state": "empty", "artifact_kind": ""}


def _zero_cell(artifact_kind: str = "scenario") -> dict:
    """Slice 2 of dispatch 11 — runs exist for this cell but produced
    0% coverage. Distinct from empty (no run at all)."""
    return {"count": 0, "state": "zero", "artifact_kind": artifact_kind, "pct": 0.0}


def _supported_cell(count: int, artifact_kind: str, *, pct: float | None = None) -> dict:
    """Runs exist with N% coverage. ``pct`` is optional — when present
    the template renders the percent; when None it falls back to the
    legacy 'N artifact_kinds' display."""
    cell: dict = {"count": count, "state": "supported", "artifact_kind": artifact_kind}
    if pct is not None:
        cell["pct"] = float(pct)
    return cell


def mini_protocol_coverage() -> dict:
    """rows: mini-protocols. columns: implementations. cells: scenario count."""
    from profile_manager.data.scenarios import _list_scenarios_for_compare
    rows = mini_protocol_axis()
    columns = implementation_axis()
    scenarios = _list_scenarios_for_compare()
    evidence = mini_protocol_cell_evidence()
    cells: dict[tuple[str, str], dict] = {}
    # Pre-index each scenario's protocol set + declared target implementation
    # once, so the row×col loop is a set membership test rather than a rescan.
    indexed = [
        (_protocols_in_text(s["id"]), s.get("target_impl"))
        for s in scenarios
    ]
    for row in rows:
        for col in columns:
            count = sum(
                1 for protos, impl in indexed
                if row["slug"] in protos and impl == col["slug"]
            )
            cell = (
                _supported_cell(count, "scenario") if count > 0 else _empty_cell()
            )
            cell.update(evidence.get((row["slug"], col["slug"]), {"status": "no_run"}))
            cells[(row["slug"], col["slug"])] = cell
    return {
        "title": "Mini-protocol coverage",
        "caption": (
            "Scenarios that exercise each Cardano mini-protocol per "
            "implementation, counted by each scenario's declared "
            "<code>target.implementation</code> and every mini-protocol named "
            "in its id, derived from <code>dwarf/scenarios/</code> at request "
            "time."
        ),
        "columns": columns,
        "rows": rows,
        "cells": cells,
        "anchor_paths": ["dwarf/scenarios/", "dwarf/targets/manifests/"],
    }


# Primary-category taxonomy for the scenario census. Priority-ordered:
# the first matching rule wins, so every scenario lands in exactly one
# category and the census sums to the full catalog. Slugs are stable ids;
# labels are display-only.
_CENSUS_CATEGORIES: list[tuple[str, str, tuple[str, ...]]] = [
    ("decode-parser", "Decode / parser robustness",
     ("cbor", "cov-block", "cov-header", "cov-tx-", "cov-ledger", "cov-apply",
      "edge-cases", "parser", "malformed", "serdes")),
    ("mini-protocol-wire", "Mini-protocol wire",
     ("mini-protocol", "cov-handshake", "cov-keepalive", "cov-txsub")),
    ("runtime-protocol", "Runtime protocol behavior",
     ("blockfetch", "chainsync", "txsubmission", "handshake", "keepalive",
      "keep-alive", "peersharing")),
    ("consensus-chain", "Consensus / chain",
     ("consensus", "rollback", "fork", "vrf", "nonce", "epoch", "genesis",
      "threshold", "chainhold", "leader", "praos", "slot-forging", "overlay")),
    ("era-hardfork", "Era / hard-fork",
     ("era", "hard-fork", "hardfork", "-hf-", "boundary")),
    ("plutus-scripts", "Plutus / scripts",
     ("plutus", "exunits", "isvalid", "phase2")),
    ("topology-eclipse", "Topology / eclipse / Sybil",
     ("eclipse", "sybil", "topology", "mesh", "partition", "churn",
      "promotion", "duplex", "concentration", "big-ledger", "node-",
      "byzantine")),
    ("mempool-n2c", "Mempool / node-to-client",
     ("mempool", "local-query", "local-submit", "localtx", "localstate",
      "lsq", "local-")),
    ("resource-dos", "Resource / DoS",
     ("resource", "rss", "disk", "cpu", "slow-loris", "baseline",
      "starvation", "impairment", "time-skew", "mux", "overrun")),
    ("lifecycle-robustness", "Lifecycle / robustness",
     ("snapshot", "restart", "credential", "forensic", "observability",
      "checkpoint", "bootstrap", "panic", "recovery", "syscall", "pcap",
      "bundle", "lifecycle", "state", "migration", "validation-path",
      "real-adapter", "assumption", "upstream")),
]
_CENSUS_FALLBACK = ("other", "Other")


def _scenario_category(scenario_id: str | None) -> tuple[str, str]:
    """Classify a scenario into exactly one primary census category.

    Priority-ordered first-match: decode/parser is tested before the
    protocol rules so a 'blockfetch-invalid-block-cbor' scenario counts
    as decode robustness, not runtime protocol behavior.
    """
    s = (scenario_id or "").lower()
    for slug, label, keywords in _CENSUS_CATEGORIES:
        if any(k in s for k in keywords):
            return slug, label
    return _CENSUS_FALLBACK


def scenario_census() -> dict:
    """rows: primary category. columns: implementations + Total. cells: scenario count.

    Every scenario in the catalog lands in exactly one row, so the Total
    column reconciles to the full catalog size — this is the accounting
    view that shows nothing is dropped.
    """
    from collections import Counter
    from profile_manager.data.scenarios import _list_scenarios_for_compare
    scenarios = _list_scenarios_for_compare()
    impl_cols = implementation_axis()
    columns = impl_cols + [{"slug": "__total", "label": "Total"}]

    per_cat_impl: dict[tuple[str, str], int] = {}
    cat_total: Counter = Counter()
    label_by_slug: dict[str, str] = {}
    for s in scenarios:
        slug, label = _scenario_category(s["id"])
        label_by_slug[slug] = label
        impl = s.get("target_impl") or "unknown"
        per_cat_impl[(slug, impl)] = per_cat_impl.get((slug, impl), 0) + 1
        cat_total[slug] += 1

    # Rows: only categories that actually contain scenarios, biggest first.
    rows = [
        {"slug": slug, "label": label_by_slug[slug]}
        for slug in sorted(cat_total, key=lambda k: (-cat_total[k], label_by_slug[k]))
    ]
    cells: dict[tuple[str, str], dict] = {}
    for row in rows:
        for col in columns:
            if col["slug"] == "__total":
                count = cat_total[row["slug"]]
            else:
                count = per_cat_impl.get((row["slug"], col["slug"]), 0)
            cells[(row["slug"], col["slug"])] = (
                _supported_cell(count, "scenario") if count > 0 else _empty_cell()
            )
    total = sum(cat_total.values())
    return {
        "title": "Scenario census",
        "caption": (
            f"All {total} scenarios in <code>dwarf/scenarios/</code>, each "
            "counted once by primary category (priority-ordered) and by "
            "declared <code>target.implementation</code>. The Total column "
            "reconciles to the full catalog. Amaru additionally participates "
            "in differential runtime scenarios whose declared target is "
            "cardano-node, so its direct-target count here is a floor, not a "
            "ceiling."
        ),
        "columns": columns,
        "rows": rows,
        "cells": cells,
        "anchor_paths": ["dwarf/scenarios/"],
    }


def cbor_surface_axis() -> list[dict]:
    """CBOR decode surfaces named anywhere in any scenario id, sorted by slug."""
    from profile_manager.data.scenarios import _list_scenarios_for_compare
    found: set[str] = set()
    for entry in _list_scenarios_for_compare():
        found |= _cbor_shapes_in_text(entry["id"])
    return [
        {"slug": slug, "label": _label_for(slug, _CBOR_SHAPE_LABELS)}
        for slug in sorted(found)
    ]


def cbor_surface_coverage() -> dict:
    """rows: CBOR decode surfaces. columns: implementations. cells: scenario count."""
    from profile_manager.data.scenarios import _list_scenarios_for_compare
    rows = cbor_surface_axis()
    columns = implementation_axis()
    scenarios = _list_scenarios_for_compare()
    # Pre-index each scenario's CBOR shape set + declared target implementation.
    indexed = [
        (_cbor_shapes_in_text(s["id"]), s.get("target_impl"))
        for s in scenarios
    ]
    cells: dict[tuple[str, str], dict] = {}
    for row in rows:
        for col in columns:
            count = sum(
                1 for shapes, impl in indexed
                if row["slug"] in shapes and impl == col["slug"]
            )
            cells[(row["slug"], col["slug"])] = (
                _supported_cell(count, "scenario") if count > 0 else _empty_cell()
            )
    return {
        "title": "CBOR decode-surface coverage",
        "caption": (
            "Scenarios that fuzz each Cardano CBOR decode surface per "
            "implementation, counted by each scenario's declared "
            "<code>target.implementation</code> and the CBOR shape named in "
            "its id, derived from <code>dwarf/scenarios/</code> at request "
            "time."
        ),
        "columns": columns,
        "rows": rows,
        "cells": cells,
        "anchor_paths": ["dwarf/scenarios/", "dwarf/targets/manifests/"],
    }


def fault_family_coverage() -> dict:
    """rows: fault families. columns: implementations. cells: count of scenarios+scripts."""
    rows = fault_family_axis()
    columns = implementation_axis()
    cells: dict[tuple[str, str], dict] = {}

    scripts_dir = _project_root() / "dwarf" / "scripts"
    script_names: list[str] = []
    if scripts_dir.is_dir():
        script_names = [p.name for p in scripts_dir.glob("runtime_*_check.py")]

    from profile_manager.data.scenarios import _list_scenarios_for_compare
    scenarios = _list_scenarios_for_compare()

    for row in rows:
        family = row["slug"]
        # Build candidate token set: the full slug, plus the last-2-segment suffix
        # for compound slugs (>2 segments). The suffix variant catches scenario-id
        # naming that drops a leading namespace (e.g. 'local_port_delay' fault
        # primitive vs 'port-delay' scenario id).
        parts = family.split("_")
        family_tokens = {family.replace("_", "-")}
        if len(parts) > 2:
            family_tokens.add("-".join(parts[-2:]))
        family_tokens_underscored = {family.replace("-", "_")}
        if len(parts) > 2:
            family_tokens_underscored.add("_".join(parts[-2:]))
        for col in columns:
            count = 0
            for s in scenarios:
                sid = s["id"]
                runtime = (s.get("runtime") or "").lower()
                if runtime != "devnet":
                    continue
                if not any(token in sid for token in family_tokens):
                    continue
                count += 1
            for name in script_names:
                if any(token in name for token in family_tokens_underscored):
                    count += 1
            cells[(row["slug"], col["slug"])] = (
                _supported_cell(count, "scenario+script") if count > 0 else _empty_cell()
            )
    return {
        "title": "Fault family coverage",
        "caption": (
            "Adversarial conditions exercised per implementation, derived from "
            "<code>dwarf/primitives/fault/*.schema.json</code> against "
            "<code>dwarf/scripts/runtime_*_check.py</code> and "
            "<code>dwarf/scenarios/m3-runtime-*</code> at request time."
        ),
        "columns": columns,
        "rows": rows,
        "cells": cells,
        "anchor_paths": [
            "dwarf/primitives/fault/",
            "dwarf/scripts/",
            "dwarf/scenarios/",
        ],
    }


def fuzzer_backend_coverage() -> dict:
    """rows: fuzzer backends. columns: CBOR target shapes. cells: target manifest count."""
    rows = fuzzer_backend_axis()
    columns = cbor_shape_axis()
    cells: dict[tuple[str, str], dict] = {}

    manifests_dir = _project_root() / "dwarf" / "targets" / "manifests"
    manifest_names: list[str] = []
    if manifests_dir.is_dir():
        manifest_names = [p.name for p in manifests_dir.glob("*-cbor-decode-*.yaml")]

    covered_backends: set[str] = set()
    for row in rows:
        backend = row["slug"]
        for col in columns:
            shape = col["slug"]
            count = sum(
                1 for name in manifest_names
                if f"-cbor-decode-{shape}." in name
                and (
                    (backend == "afl" and "cargo-fuzz" not in name)
                    or (backend == "cargo-fuzz")
                )
            )
            if count > 0:
                covered_backends.add(backend)
            cells[(row["slug"], col["slug"])] = (
                _supported_cell(count, "manifest") if count > 0 else _empty_cell()
            )
    # This matrix is per-CBOR-shape, so only backends that actually fuzz a CBOR
    # decoder can populate. Drop rows that back no shape at all (the runtime_*
    # campaign scripts and the aflpp coverage harness are not CBOR-shape fuzzers)
    # so the table shows real coverage instead of a wall of empty rows.
    rows = [row for row in rows if row["slug"] in covered_backends]
    cells = {key: val for key, val in cells.items() if key[0] in covered_backends}
    return {
        "title": "Fuzzer backend coverage",
        "caption": (
            "Per-backend per-CBOR-shape coverage, derived from "
            "<code>dwarf/targets/manifests/*-cbor-decode-*.yaml</code> and "
            "<code>dwarf/scripts/*_campaign.py</code> at request time. "
            "Backend-to-manifest attribution is best-effort by filename in "
            "this retained M2 catalog."
        ),
        "columns": columns,
        "rows": rows,
        "cells": cells,
        "anchor_paths": [
            "dwarf/targets/manifests/",
            "dwarf/scripts/",
        ],
    }
