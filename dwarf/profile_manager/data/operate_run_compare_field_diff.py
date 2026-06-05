"""Field-level structural diff for the bundle inspector overlay.

Item #18 — extends /operate/compare/runs with a flat side-by-side
diff over ``manifest.json`` + assertion stats + curated telemetry,
classifying every leaf as ``added`` / ``removed`` / ``mutated`` /
``same`` and flagging telemetry drift past a relative threshold.

Why field-level, not raw-text-diff:
  Two bundles will always disagree on run-id, timestamps, env hash,
  and absolute paths under /home/. A naive text diff is dominated by
  that noise. Walking *named* leaves and ignoring the noise paths
  yields rows operators can act on.

Why a curated path set, not full reflection:
  Bundles also embed entire ``queue_entries`` arrays and seed inputs
  that explode the diff into thousands of rows the page can't render
  usefully. The curated set covers the fields a triager actually reads
  (verdict, target, scenario, assertion counts, AFL queue/crash/hang
  counters, exec rates) — everything else stays accessible via the
  per-run inspector.

Threshold:
  Telemetry numeric drift is flagged when |Δ| / max(|left|, |right|, 1)
  exceeds ``TELEMETRY_DRIFT_THRESHOLD`` (default 0.20 — ±20%). Smaller
  deltas still surface as ``mutated`` but without the drift highlight.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


TELEMETRY_DRIFT_THRESHOLD = 0.20


def _runs_dir() -> Path:
    env = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "runs"


def _safe_run_id(run_id: str) -> bool:
    return bool(run_id) and "/" not in run_id and ".." not in run_id


def _read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# Manifest leaves the diff extracts. Per-bundle uniques (run_id,
# started_at, env_sha256, host paths, telemetry log paths) are
# excluded — they would noise out the actual semantic delta.
_MANIFEST_PATHS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("scenario.id", ("scenario", "id")),
    ("scenario.spec_version", ("scenario", "spec_version")),
    ("target.implementation", ("target", "implementation")),
    ("target.version", ("target", "version")),
    ("runtime", ("runtime",)),
    ("exit_status", ("exit_status",)),
    ("framework.version", ("framework", "version")),
    ("assertion_summary.total", ("assertion_summary", "total")),
    ("assertion_summary.pass", ("assertion_summary", "pass")),
    ("assertion_summary.fail", ("assertion_summary", "fail")),
    ("resource_snapshot.wall_time_seconds", ("resource_snapshot", "wall_time_seconds")),
    ("resource_snapshot.process_rss.delta_bytes", ("resource_snapshot", "process_rss", "delta_bytes")),
)

# AFL/AFL++ summary leaves. Same shape under outputs/aflpp/summary.json
# and outputs/afl/summary.json.
_AFL_SUMMARY_PATHS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("queue_count", ("queue_count",)),
    ("crash_count", ("crash_count",)),
    ("hang_count", ("hang_count",)),
    ("host_arch", ("host_arch",)),
    ("target_arch", ("target_arch",)),
    ("lifecycle.bucket_count", ("lifecycle", "bucket_count")),
    ("lifecycle.record_count", ("lifecycle", "record_count")),
    ("lifecycle.replay_queue_count", ("lifecycle", "replay_queue_count")),
    ("lifecycle.compare_queue_count", ("lifecycle", "compare_queue_count")),
)

# Numeric telemetry fields where a relative threshold is meaningful.
# Booleans and small enumerations stay on equality only.
_TELEMETRY_NUMERIC_FIELDS: frozenset[str] = frozenset({
    "resource_snapshot.wall_time_seconds",
    "resource_snapshot.process_rss.delta_bytes",
    "queue_count",
    "lifecycle.bucket_count",
    "lifecycle.record_count",
    "lifecycle.replay_queue_count",
    "lifecycle.compare_queue_count",
    "execs_per_sec",
    "bitmap_cvg",
})

# Fields where any non-zero is operationally meaningful — a delta
# from 0 to N is always a semantic event, not just drift.
_NONZERO_SEMANTIC_FIELDS: frozenset[str] = frozenset({
    "crash_count",
    "hang_count",
    "assertion_summary.fail",
    "assertions.fail_count",
})


def _resolve_path(doc: Any, path: tuple[str, ...]) -> Any:
    cur: Any = doc
    for seg in path:
        if not isinstance(cur, dict) or seg not in cur:
            return _MISSING
        cur = cur[seg]
    return cur


_MISSING = object()


def _classify(left: Any, right: Any, *, path: str) -> dict[str, Any]:
    """Classify one (path, left, right) tuple into a render-ready row."""
    if left is _MISSING and right is _MISSING:
        return {"path": path, "kind": "absent", "left": None, "right": None,
                "threshold_breach": False, "semantic": None}
    if left is _MISSING:
        return {"path": path, "kind": "added", "left": None, "right": right,
                "threshold_breach": False, "semantic": _semantic_for(path, None, right)}
    if right is _MISSING:
        return {"path": path, "kind": "removed", "left": left, "right": None,
                "threshold_breach": False, "semantic": _semantic_for(path, left, None)}
    if left == right:
        return {"path": path, "kind": "same", "left": left, "right": right,
                "threshold_breach": False, "semantic": None}
    breach = _is_threshold_breach(path, left, right)
    return {"path": path, "kind": "mutated", "left": left, "right": right,
            "threshold_breach": breach,
            "semantic": _semantic_for(path, left, right)}


def _is_threshold_breach(path: str, left: Any, right: Any) -> bool:
    """True if the relative delta exceeds the configured threshold.

    Only fires for numeric telemetry fields that have been opted in via
    _TELEMETRY_NUMERIC_FIELDS — applying it everywhere would mark
    nominal/categorical mutations as drift, which is misleading."""
    if path not in _TELEMETRY_NUMERIC_FIELDS:
        return False
    try:
        l = float(left)
        r = float(right)
    except (TypeError, ValueError):
        return False
    denom = max(abs(l), abs(r), 1.0)
    return abs(l - r) / denom > TELEMETRY_DRIFT_THRESHOLD


def _semantic_for(path: str, left: Any, right: Any) -> str | None:
    """Tag a row with a semantic class the view can highlight."""
    if path == "exit_status":
        if left and right and left != right:
            return "pass_fail_flip"
    if path in _NONZERO_SEMANTIC_FIELDS:
        try:
            l = int(left or 0)
            r = int(right or 0)
        except (TypeError, ValueError):
            return None
        if (l == 0) != (r == 0):
            return "nonzero_event"
    return None


def _diff_section(
    left_doc: Any,
    right_doc: Any,
    paths: tuple[tuple[str, tuple[str, ...]], ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, p in paths:
        l = _resolve_path(left_doc, p) if left_doc is not None else _MISSING
        r = _resolve_path(right_doc, p) if right_doc is not None else _MISSING
        rows.append(_classify(l, r, path=label))
    return rows


def _aflpp_summary(run_dir: Path) -> tuple[str | None, dict[str, Any] | None]:
    """Return (engine, summary_doc) for whichever AFL summary exists."""
    for engine in ("aflpp", "afl"):
        doc = _read_json(run_dir / "outputs" / engine / "summary.json")
        if isinstance(doc, dict):
            return engine, doc
    return None, None


def _aflpp_extras(run_dir: Path) -> dict[str, Any]:
    """Pull execs_per_sec + bitmap_cvg from fuzzer_stats — these aren't
    in the JSON summaries but are the operator's primary KPIs."""
    out: dict[str, Any] = {}
    for engine in ("aflpp", "afl"):
        fs = run_dir / "outputs" / engine / "default" / "fuzzer_stats"
        if not fs.is_file():
            continue
        try:
            text = fs.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return out
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().rstrip("%")
            if key == "execs_per_sec":
                try:
                    out["execs_per_sec"] = float(val)
                except ValueError:
                    pass
            elif key == "bitmap_cvg":
                try:
                    out["bitmap_cvg"] = float(val)
                except ValueError:
                    pass
        return out
    return out


def _assertion_counts(doc: Any) -> dict[str, int]:
    if not isinstance(doc, list):
        return {"total": 0, "pass": 0, "fail": 0, "other": 0}
    total = 0
    p = 0
    f = 0
    o = 0
    for a in doc:
        if not isinstance(a, dict):
            continue
        total += 1
        result = a.get("result")
        if result == "pass":
            p += 1
        elif result == "fail":
            f += 1
        else:
            o += 1
    return {"total": total, "pass": p, "fail": f, "other": o}


def _assertion_primitives(doc: Any) -> list[str]:
    """Sorted list of assertion primitives present — drives the
    "primitive list change" semantic check."""
    if not isinstance(doc, list):
        return []
    out: set[str] = set()
    for a in doc:
        if isinstance(a, dict):
            prim = a.get("primitive")
            if isinstance(prim, str):
                out.add(prim)
    return sorted(out)


def _assertion_stats_rows(left_doc: Any, right_doc: Any) -> list[dict[str, Any]]:
    l_counts = _assertion_counts(left_doc)
    r_counts = _assertion_counts(right_doc)
    l_prims = _assertion_primitives(left_doc)
    r_prims = _assertion_primitives(right_doc)
    rows: list[dict[str, Any]] = []
    for key in ("total", "pass", "fail", "other"):
        rows.append(_classify(
            l_counts[key], r_counts[key],
            path=f"assertions.{key}_count",
        ))
    # Primitive list — render as comma-joined string for the row.
    rows.append(_classify(
        ", ".join(l_prims) or "—",
        ", ".join(r_prims) or "—",
        path="assertions.primitives",
    ))
    # Tag the primitives row with a semantic class when the sets differ.
    if rows[-1]["kind"] == "mutated":
        rows[-1]["semantic"] = "primitive_list_change"
    return rows


def _summarise(rows: list[dict[str, Any]]) -> dict[str, int]:
    out = {"added": 0, "removed": 0, "mutated": 0, "same": 0,
           "absent": 0, "drift": 0, "semantic": 0}
    for r in rows:
        out[r["kind"]] = out.get(r["kind"], 0) + 1
        if r.get("threshold_breach"):
            out["drift"] += 1
        if r.get("semantic"):
            out["semantic"] += 1
    return out


def field_diff_payload(
    left_id: str,
    right_id: str,
    *,
    runs_dir: Path | None = None,
) -> dict[str, Any]:
    """Build the render-ready field-diff payload.

    Both run-ids must be safe (no '/', no '..') and resolve to
    directories on disk. Missing bundles return ``{found: False}`` so
    the view layer can render a not-found state without re-checking.
    """
    if not _safe_run_id(left_id) or not _safe_run_id(right_id):
        return {"found": False, "left_id": left_id, "right_id": right_id,
                "left_present": False, "right_present": False}
    base = Path(runs_dir) if runs_dir is not None else _runs_dir()
    left_dir = base / left_id
    right_dir = base / right_id
    left_present = left_dir.is_dir()
    right_present = right_dir.is_dir()
    if not (left_present and right_present):
        return {"found": False, "left_id": left_id, "right_id": right_id,
                "left_present": left_present, "right_present": right_present}

    left_manifest = _read_json(left_dir / "manifest.json") or {}
    right_manifest = _read_json(right_dir / "manifest.json") or {}
    left_assertions = _read_json(left_dir / "assertions.json")
    right_assertions = _read_json(right_dir / "assertions.json")
    left_engine, left_aflpp = _aflpp_summary(left_dir)
    right_engine, right_aflpp = _aflpp_summary(right_dir)
    left_extras = _aflpp_extras(left_dir)
    right_extras = _aflpp_extras(right_dir)

    manifest_rows = _diff_section(left_manifest, right_manifest, _MANIFEST_PATHS)
    afl_rows: list[dict[str, Any]] = []
    if left_aflpp is not None or right_aflpp is not None:
        afl_rows = _diff_section(left_aflpp or {}, right_aflpp or {}, _AFL_SUMMARY_PATHS)
    extras_rows: list[dict[str, Any]] = []
    if left_extras or right_extras:
        for key in ("execs_per_sec", "bitmap_cvg"):
            extras_rows.append(_classify(
                left_extras.get(key, _MISSING),
                right_extras.get(key, _MISSING),
                path=key,
            ))
    assertion_rows = _assertion_stats_rows(left_assertions, right_assertions)

    sections = [
        {"name": "manifest", "label": "Manifest", "rows": manifest_rows,
         "summary": _summarise(manifest_rows)},
        {"name": "assertions", "label": "Assertions", "rows": assertion_rows,
         "summary": _summarise(assertion_rows)},
    ]
    if afl_rows or extras_rows:
        afl_label = "AFL++ telemetry" if left_engine == "aflpp" or right_engine == "aflpp" else "AFL telemetry"
        sections.append({
            "name": "afl",
            "label": afl_label,
            "rows": afl_rows + extras_rows,
            "summary": _summarise(afl_rows + extras_rows),
        })

    overall = _summarise([r for s in sections for r in s["rows"]])
    identical = (
        overall["added"] == 0
        and overall["removed"] == 0
        and overall["mutated"] == 0
    )
    return {
        "found": True,
        "left_id": left_id,
        "right_id": right_id,
        "left_present": True,
        "right_present": True,
        "sections": sections,
        "overall": overall,
        "identical": identical,
        "drift_threshold": TELEMETRY_DRIFT_THRESHOLD,
    }
