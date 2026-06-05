"""Cross-impl comparison data extractors for /operate/compare.

Reads cross-impl-comparison.json artifacts from runs enumerated by the
existing data.runs.recent_runs_payload. Groups by scenario family using
views.scenarios._scenario_family — single source of truth, no parallel
implementation.

No hand-curated lists. No fabrication. Render only what the source JSON
contains: per-active-peer normalization rows render only when the
`normalization` field is present; asymmetry markers render only when
`metadata.asymmetry` is present.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from profile_manager.views.scenarios import _scenario_family

# Re-export so other modules (and the test suite's import-invariant test)
# can rely on a single source of truth for the family algorithm.
scenario_family = _scenario_family


def _bundle_inspector_url(run_id: str) -> str:
    """URL of the per-run bundle inspector page.

    Slice 26 ported the inspector to /operate/runs/<id> (current-gen
    chrome). Legacy /runs/<id> is still served as an alias inside the
    dashboard handler, but new links are emitted at the canonical URL.
    Single source of truth — do NOT inline URL building elsewhere.
    """
    return f"/operate/runs/{run_id}"


def _classify_metric(name: str) -> str:
    """Bucket a compare_diff metric name by time-window prefix.

    Returns one of: "fault_window", "postfault_window", "whole_run".
    Buckets are derived purely from the metric name; no lookup table.
    """
    if name.startswith("preview_fault_window_"):
        return "fault_window"
    if name.startswith("preview_postfault_window_"):
        return "postfault_window"
    return "whole_run"


def _human_label_for_metric(name: str) -> str:
    """Strip the "preview_" namespace and prettify the metric slug.

    "preview_fault_window_chain_bytes_delta" -> "Fault Window Chain Bytes Delta"
    """
    label = name
    if label.startswith("preview_"):
        label = label[len("preview_"):]
    return label.replace("_", " ").title()


def _enrich_metric(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Translate one compare_diff entry into a render-ready dict.

    Keeps verbatim values from the source JSON; adds derived fields:
    `name`, `label`, `bucket`, plus pass-through `amaru_raw`,
    `cardano_raw`, `divergence`, `divergence_pct`, optional
    `normalization`, optional `metadata.asymmetry` (surfaced as
    `asymmetry` for template convenience).
    """
    pct = raw.get("divergence_pct")
    if isinstance(pct, float) and not math.isfinite(pct):
        pct = None
    asymmetry = (raw.get("metadata") or {}).get("asymmetry")
    enriched: dict[str, Any] = {
        "name": name,
        "label": _human_label_for_metric(name),
        "bucket": _classify_metric(name),
        "amaru_raw": raw.get("amaru"),
        "cardano_raw": raw.get("cardano-node"),
        "divergence": bool(raw.get("divergence")),
        "divergence_pct": pct,
        "asymmetry": asymmetry,
    }
    if "normalization" in raw:
        enriched["normalization"] = {
            "basis": raw["normalization"].get("basis"),
            "amaru_active_peers": raw["normalization"].get("amaru_active_peers"),
            "cardano_node_active_peers": raw["normalization"].get("cardano-node_active_peers"),
            "amaru_per_active_peer": raw.get("amaru_per_active_peer"),
            "cardano_node_per_active_peer": raw.get("cardano-node_per_active_peer"),
            "raw_divergence": bool(raw.get("raw_divergence", raw.get("divergence"))),
        }
    return enriched


def _empty_window() -> dict[str, Any]:
    return {"metrics": [], "divergent_count": 0, "total_count": 0}


def _empty_windows() -> dict[str, dict[str, Any]]:
    return {
        "whole_run": _empty_window(),
        "fault_window": _empty_window(),
        "postfault_window": _empty_window(),
    }


def _comparison_from_run(run_dir: Path) -> dict[str, Any] | None:
    """Read {run_dir}/cross-impl-comparison.json and return an enriched dict.

    Returns None if the file is missing or the JSON is malformed. Never raises.
    """
    json_path = run_dir / "cross-impl-comparison.json"
    if not json_path.is_file():
        return None
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    runs_block = payload.get("runs") or {}
    amaru_run = runs_block.get("amaru") or {}
    cn_run = runs_block.get("cardano-node") or {}

    windows: dict[str, dict[str, Any]] = _empty_windows()
    compare_diff = payload.get("compare_diff") or {}
    for metric_name, metric_raw in compare_diff.items():
        if not isinstance(metric_raw, dict):
            continue
        enriched = _enrich_metric(metric_name, metric_raw)
        bucket = enriched["bucket"]
        windows[bucket]["metrics"].append(enriched)
        windows[bucket]["total_count"] += 1
        if enriched["divergence"]:
            windows[bucket]["divergent_count"] += 1
    for win in windows.values():
        win["metrics"].sort(key=lambda m: m["name"])

    scenario_id = payload.get("scenario_id") or ""
    return {
        "scenario_id": scenario_id,
        "family": _scenario_family(scenario_id),
        "result": payload.get("result") or ("AGREED" if payload.get("agreed") else "DIVERGED"),
        "agreed": bool(payload.get("agreed")),
        "seed": payload.get("seed"),
        "amaru": {
            "run_id": amaru_run.get("run_id"),
            "run_id_url": _bundle_inspector_url(amaru_run["run_id"]) if amaru_run.get("run_id") else None,
            "exit_status": amaru_run.get("exit_status"),
            "assertion_summary": amaru_run.get("assertion_summary"),
        },
        "cardano_node": {
            "run_id": cn_run.get("run_id"),
            "run_id_url": _bundle_inspector_url(cn_run["run_id"]) if cn_run.get("run_id") else None,
            "exit_status": cn_run.get("exit_status"),
            "assertion_summary": cn_run.get("assertion_summary"),
        },
        "windows": windows,
        "source": "run-artifact",
        "evidence_path": None,
    }


def _forensic_runs_dir() -> Path:
    """Mirror data.runs._forensic_runs_dir without re-importing it (private).

    Source of truth: ADA2_DWARF_RUNS_DIR env var, else dwarf/runs/.
    """
    env = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "runs"


def _retained_evidence_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "evidence" / "m2-first-executions"


def _display_evidence_path(path: Path) -> str:
    dwarf_root = Path(__file__).resolve().parents[2]
    try:
        return f"dwarf/{path.relative_to(dwarf_root)}"
    except ValueError:
        return str(path)


def _run_url_if_present(runs_dir: Path, run_id: str | None) -> str | None:
    if not run_id:
        return None
    return _bundle_inspector_url(run_id) if (runs_dir / run_id).is_dir() else None


_RESULT_RE = re.compile(r"^Result:\s*(?:\*\*)?(AGREED|DIVERGED)(?:\*\*)?\s*$", re.MULTILINE)
_SCENARIO_RE = re.compile(r"^Scenario:\s*`([^`]+)`\s*$", re.MULTILINE)
_SEED_RE = re.compile(r"^Seed:\s*`([^`]+)`\s*$", re.MULTILINE)
_IMPL_RE = re.compile(
    r"^-\s*(amaru|cardano-node):\s*`([^`]+)`\s*(?:->|→)\s*exit\s*`([^`]+)`(?:,\s*assertions\s*(.+))?$",
    re.MULTILINE,
)


def _comparison_from_retained_markdown(path: Path, *, runs_dir: Path) -> dict[str, Any] | None:
    """Parse retained M2 cross-impl markdown into the compare-card schema.

    Retained evidence markdown predates cross-impl-comparison.json. It is
    source evidence, but not necessarily a complete dashboard run bundle, so
    run links are emitted only when the matching run directory is present.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    result_match = _RESULT_RE.search(text)
    scenario_match = _SCENARIO_RE.search(text)
    if not result_match or not scenario_match:
        return None

    runs: dict[str, dict[str, Any]] = {"amaru": {}, "cardano-node": {}}
    for match in _IMPL_RE.finditer(text):
        impl, run_id, exit_status, assertions = match.groups()
        runs[impl] = {
            "run_id": run_id,
            "exit_status": exit_status,
            "assertion_summary": assertions.strip() if assertions else None,
        }

    scenario_id = scenario_match.group(1)
    result = result_match.group(1)
    seed_match = _SEED_RE.search(text)
    amaru = runs["amaru"]
    cn = runs["cardano-node"]
    return {
        "scenario_id": scenario_id,
        "family": _scenario_family(scenario_id),
        "result": result,
        "agreed": result == "AGREED",
        "seed": seed_match.group(1) if seed_match else None,
        "amaru": {
            "run_id": amaru.get("run_id"),
            "run_id_url": _run_url_if_present(runs_dir, amaru.get("run_id")),
            "exit_status": amaru.get("exit_status"),
            "assertion_summary": amaru.get("assertion_summary"),
        },
        "cardano_node": {
            "run_id": cn.get("run_id"),
            "run_id_url": _run_url_if_present(runs_dir, cn.get("run_id")),
            "exit_status": cn.get("exit_status"),
            "assertion_summary": cn.get("assertion_summary"),
        },
        "windows": _empty_windows(),
        "source": "retained-evidence",
        "evidence_path": _display_evidence_path(path),
    }


def _retained_evidence_comparisons(*, evidence_dir: Path, runs_dir: Path) -> list[dict[str, Any]]:
    if not evidence_dir.is_dir():
        return []
    out = []
    for path in sorted(evidence_dir.glob("**/cross-impl-comparison.md")):
        comparison = _comparison_from_retained_markdown(path, runs_dir=runs_dir)
        if comparison is not None:
            out.append(comparison)
    return out


def latest_compare_per_scenario(
    *,
    runs_dir: Path | None = None,
    evidence_dir: Path | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Walk recent runs, keep one comparison per scenario_id (latest by run order).

    Uses data.runs.recent_runs_payload to enumerate runs. Filters to runs
    whose dir contains cross-impl-comparison.json. For each scenario_id,
    keeps the entry whose run appears first in recent_runs (which is sorted
    descending by ended_at then run_id by the upstream extractor).

    Returns a list of enriched comparison dicts sorted by family ascending
    then scenario_id ascending for stable rendering ("other" sorted last).
    """
    from profile_manager.data.runs import recent_runs_payload  # local: avoid circular import at module load

    using_default_runs = runs_dir is None
    base = Path(runs_dir) if runs_dir is not None else _forensic_runs_dir()
    payload = recent_runs_payload(runs_dir=base, limit=limit)
    out: dict[str, dict[str, Any]] = {}
    for run in payload.get("recent_runs") or []:
        rid = run.get("run_id")
        if not rid:
            continue
        run_dir = base / rid
        comparison = _comparison_from_run(run_dir)
        if comparison is None:
            continue
        sid = comparison["scenario_id"]
        if not sid:
            continue
        if sid not in out:
            out[sid] = comparison
    evidence_base = (
        Path(evidence_dir)
        if evidence_dir is not None
        else (_retained_evidence_dir() if using_default_runs else None)
    )
    if evidence_base is not None:
        for comparison in _retained_evidence_comparisons(evidence_dir=evidence_base, runs_dir=base):
            sid = comparison["scenario_id"]
            if sid and sid not in out:
                out[sid] = comparison
    return sorted(
        out.values(),
        key=lambda c: (1 if c["family"] == "other" else 0, c["family"], c["scenario_id"]),
    )


def _has_metric_divergence(comparison: dict[str, Any]) -> bool:
    for win in comparison.get("windows", {}).values():
        if win.get("divergent_count", 0) > 0:
            return True
    return False


def _parse_iso_timestamp(value: Any) -> datetime | None:
    """Best-effort ISO-8601 parse. Returns None on any failure."""
    if not isinstance(value, str) or not value:
        return None
    s = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def aggregate_strip(comparisons: list[dict[str, Any]], all_runs_24h_count: int) -> dict[str, int]:
    """Compute the four counts for the page-top aggregate strip.

    - total: comparisons rendered on the page (latest-per-scenario count)
    - agreed: comparisons whose top-level result is AGREED
    - diverged: total - agreed
    - metric_divergent: comparisons where any compare_diff entry is divergent
    - runs_24h: separate cadence indicator (passed in by the view)
    """
    total = len(comparisons)
    agreed = sum(1 for c in comparisons if c.get("agreed"))
    diverged = total - agreed
    metric_divergent = sum(1 for c in comparisons if _has_metric_divergence(c))
    return {
        "total": total,
        "agreed": agreed,
        "diverged": diverged,
        "metric_divergent": metric_divergent,
        "runs_24h": all_runs_24h_count,
    }


def count_runs_in_last_24h(runs_payload: dict[str, Any], *, now: datetime | None = None) -> int:
    """Count runs whose ended_at falls within the last 24 hours."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    n = 0
    for run in runs_payload.get("recent_runs") or []:
        ended = _parse_iso_timestamp(run.get("ended_at"))
        if ended is not None and ended >= cutoff:
            n += 1
    return n


def count_compare_runs_in_last_24h(
    runs_payload: dict[str, Any],
    *,
    runs_dir: Path | None = None,
    now: datetime | None = None,
) -> int:
    """Count cross-impl compare runs in last 24h.

    A compare run is one whose dir contains cross-impl-comparison.json
    (the cardano-node-side bundle, by `cardano-profile compare` convention).
    Filtered by ended_at within 24h. The bare count_runs_in_last_24h above
    counts all runs and is the wrong semantic for the page-top strip.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    base = Path(runs_dir) if runs_dir is not None else _forensic_runs_dir()
    n = 0
    for run in runs_payload.get("recent_runs") or []:
        rid = run.get("run_id")
        if not rid:
            continue
        ended = _parse_iso_timestamp(run.get("ended_at"))
        if ended is None or ended < cutoff:
            continue
        if (base / rid / "cross-impl-comparison.json").is_file():
            n += 1
    return n


def comparisons_grouped_by_family(comparisons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group an already-sorted comparison list into family sections.

    Returns [{family, count, items: [...]}] preserving the upstream sort
    order (family ascending, "other" last, scenario_id ascending).
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for c in comparisons:
        fam = c["family"]
        if fam not in groups:
            groups[fam] = []
            order.append(fam)
        groups[fam].append(c)
    return [{"family": fam, "count": len(groups[fam]), "items": groups[fam]} for fam in order]
