"""Item E (Phase 4.3 D-1) — retro-classification view for /operate/audit.

Pure data extractor. For every local bundle on disk:
  - Run the item-A thinness rules (data/thinness_signals.py) to find
    suspicious telemetry shapes.
  - Map any rule fires to a retro-classification per the rule's
    finding (still-broken vs broken-differently per the audit-fix
    tracker's classification semantics).
  - When no rules fire, apply *positive* heuristics over the bundle
    that confidently classify "fixed" (e.g. AFL++ bundle with
    cycles_done >= 1 and execs_done >= 1000; library run with
    iteration/case rows >= 100). Otherwise: "unknown" — the page
    explicitly does NOT guess "fixed" without positive evidence.
  - DO NOT re-evaluate floors live. That's Phase 4.3 design-option C
    (separate dispatch). This page is a heuristic surface over data
    that's already on disk.

Classification semantics (tracker rules, in severity order):
  still-broken      — would FAIL under the tightened floor today.
                      Per F-007/F-003/F-AFLNET shape.
  broken-differently — passes today on a different vacuous shape than
                      the floor caught. Per F-009 missing-bytes,
                      F-008 single-node-clean.
  unknown           — no rule fires, no positive heuristic confirms;
                      page caveats explicitly.
  fixed             — no rule fires AND a positive heuristic confirms
                      strong execution signal.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


# ---- classification primitives ----

_RULE_TO_CLASSIFICATION: dict[str, str] = {
    "R-F007": "still-broken",
    "R-F003": "still-broken",
    "R-AFLNET": "still-broken",
    "R-F008": "broken-differently",
    "R-F009": "broken-differently",
}


CLASSIFICATIONS = ("still-broken", "broken-differently", "unknown", "fixed")
_SEVERITY_ORDER = {c: i for i, c in enumerate(CLASSIFICATIONS)}


def _classification_for_matches(matches: list[dict]) -> str:
    """Pick the most-severe classification across all fired rules."""
    if not matches:
        return "unknown"  # caller may upgrade to "fixed"
    best = "broken-differently"
    for m in matches:
        c = _RULE_TO_CLASSIFICATION.get(m["rule"], "broken-differently")
        if _SEVERITY_ORDER[c] < _SEVERITY_ORDER[best]:
            best = c
    return best


# ---- positive-evidence heuristics ----

_BITMAP_RE = re.compile(r"^cycles_done\s*:\s*(\d+)\s*$", re.M)
_EXECS_RE = re.compile(r"^execs_done\s*:\s*(\d+)\s*$", re.M)
_ITER_EVENT_NAMES = frozenset({"iteration_outcome", "iteration", "case"})


def _aflpp_strong_signal(run_dir: Path) -> dict | None:
    """Returns {cycles_done, execs_done} when the AFL++ stats indicate
    a real campaign; None otherwise (no stats file or weak signal)."""
    fs = run_dir / "outputs" / "aflpp" / "default" / "fuzzer_stats"
    if not fs.is_file():
        return None
    try:
        text = fs.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    cycles = _BITMAP_RE.search(text)
    execs = _EXECS_RE.search(text)
    if not (cycles and execs):
        return None
    try:
        c = int(cycles.group(1))
        e = int(execs.group(1))
    except ValueError:
        return None
    if c >= 1 and e >= 1000:
        return {"cycles_done": c, "execs_done": e}
    return None


def _library_iteration_signal(run_dir: Path) -> dict | None:
    """Count iteration/case rows in log.ndjson. Returns
    {iteration_rows, ok_rows} when iteration_rows >= 100 AND ok_rows
    >= 1; None otherwise.

    Both conditions matter for the "fixed" classification: 100+ rows
    confirms the campaign actually ran, and at least one ok-outcome row
    confirms the parser cleanly accepted at least one input (eliminates
    the F-009 ok=0 vacuous-pass shape — d56fe54b cited in the tracker:
    5000 iteration rows but every outcome=clean_error, assertion passed
    on matched=0/ok=0, which is NOT a confident "fixed" classification).
    """
    log = run_dir / "log.ndjson"
    if not log.is_file():
        return None
    iter_rows = 0
    ok_rows = 0
    try:
        with log.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("event") in _ITER_EVENT_NAMES:
                    iter_rows += 1
                    if (e.get("payload") or {}).get("outcome") == "ok":
                        ok_rows += 1
    except OSError:
        return None
    if iter_rows >= 100 and ok_rows >= 1:
        return {"iteration_rows": iter_rows, "ok_rows": ok_rows}
    return None


# ---- run iteration + classification ----

def _read_manifest(run_dir: Path) -> dict | None:
    p = run_dir / "manifest.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _runs_root() -> Path:
    env = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "dwarf" / "runs"


def _telemetry_summary(run_dir: Path, manifest: dict,
                       matches: list[dict],
                       positive_signal: dict | None) -> str:
    """One-line summary the row renders inline. Cites the most-relevant
    numbers for the classification."""
    parts: list[str] = []
    asum = manifest.get("assertion_summary") or {}
    if asum:
        parts.append(
            f"assertions {asum.get('pass', 0)}/{asum.get('total', 0)}"
            f" ({asum.get('fail', 0)} fail)"
        )
    for m in matches:
        for ev in m.get("evidence") or []:
            parts.append(f"{ev['label']}={ev['value']}")
    if positive_signal:
        for k, v in positive_signal.items():
            parts.append(f"{k}={v}")
    return " · ".join(parts)


def _assertion_family(manifest: dict, run_dir: Path) -> str:
    """Pull a coarse assertion family for the filter UI. Reads
    assertions.json defensively."""
    p = run_dir / "assertions.json"
    if not p.is_file():
        return "—"
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "—"
    if not isinstance(body, list):
        return "—"
    primitives = sorted({
        a.get("primitive") for a in body
        if isinstance(a, dict) and a.get("primitive")
    })
    if not primitives:
        return "—"
    if len(primitives) == 1:
        return primitives[0]
    return f"{primitives[0]} +{len(primitives) - 1}"


def classify_one(run_dir: Path) -> dict[str, Any]:
    """Classify one bundle. Pure helper — testable without iteration."""
    from profile_manager.data.thinness_signals import detect_thinness
    manifest = _read_manifest(run_dir) or {}
    try:
        matches = detect_thinness(run_dir)
    except Exception:  # noqa: BLE001 — defensive
        matches = []
    classification = _classification_for_matches(matches)
    positive: dict | None = None
    if classification == "unknown":
        # No thinness fired — try positive heuristics. Order matters:
        # AFL++ stats are strongest evidence; iteration-row count is
        # next; otherwise stay unknown.
        positive = _aflpp_strong_signal(run_dir)
        if positive is None:
            positive = _library_iteration_signal(run_dir)
        if positive is not None:
            classification = "fixed"
    return {
        "run_id": run_dir.name,
        "classification": classification,
        "rules_fired": [m["rule"] for m in matches],
        "matches": matches,
        "scenario_id": (manifest.get("scenario") or {}).get("id") or "",
        "runtime": manifest.get("runtime") or "",
        "exit_status": manifest.get("exit_status") or "",
        "ended_at": manifest.get("ended_at") or "",
        "assertion_family": _assertion_family(manifest, run_dir),
        "telemetry_summary": _telemetry_summary(run_dir, manifest, matches, positive),
        "run_url": f"/operate/runs/{run_dir.name}",
    }


def operate_audit_payload(*, runs_dir: Path | None = None,
                          limit: int = 200) -> dict[str, Any]:
    """Build the render-ready /operate/audit payload.

    Walks up to ``limit`` newest runs (by directory name — same
    timestamp prefix that operate_coverage_trend uses), classifies
    each, and projects counts + sorted-by-severity rows.
    """
    base = runs_dir if runs_dir is not None else _runs_root()
    if not base.is_dir():
        return {
            "rows": [],
            "total": 0,
            "counts": {c: 0 for c in CLASSIFICATIONS},
            "empty": True,
        }
    candidates: list[Path] = []
    for child in base.iterdir():
        if not child.is_dir():
            continue
        if not (child / "manifest.json").is_file():
            continue
        candidates.append(child)
    # Newest first by directory name (timestamp prefix).
    candidates.sort(key=lambda p: p.name, reverse=True)
    candidates = candidates[:limit]
    rows: list[dict[str, Any]] = []
    for c in candidates:
        rows.append(classify_one(c))
    # Severity sort: still-broken first, fixed last; tie-break newest first.
    rows.sort(key=lambda r: (
        _SEVERITY_ORDER.get(r["classification"], 99),
        # Reverse-name as secondary so newest within the same bucket
        # surfaces first.
        tuple(-ord(ch) for ch in r["run_id"]),
    ))
    counts = {c: 0 for c in CLASSIFICATIONS}
    for r in rows:
        counts[r["classification"]] = counts.get(r["classification"], 0) + 1
    return {
        "rows": rows,
        "total": len(rows),
        "counts": counts,
        "empty": not rows,
    }


def apply_audit_filters(rows: list[dict[str, Any]], *,
                        classification: str = "",
                        family: str = "") -> list[dict[str, Any]]:
    """Filter rows for the page UI. Empty filter = pass-through."""
    out = list(rows)
    if classification:
        out = [r for r in out if r["classification"] == classification]
    if family:
        needle = family.lower()
        out = [r for r in out if needle in (r["assertion_family"] or "").lower()]
    return out
