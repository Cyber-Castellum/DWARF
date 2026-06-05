"""Item C (Phase 4.3 D-1) — floor-preview re-evaluation tile.

For every assertion a bundle ran, this module re-runs the SAME
production evaluator class against the bundle's preserved telemetry
and reports its current verdict. The dashboard surfaces the gap
between the bundle's recorded result (assertions.json) and the
floor-preview result.

Hard contract: this module **imports** from primitives.py and
scenario.py. It does NOT re-implement floor logic. If a primitive's
evaluator changes, the preview changes with it. If the production API
forces an instantiation pattern this module cannot satisfy cleanly,
the affected row reports kind="error" — never a forked answer.

Implementation notes:
- ``primitives.instantiate`` requires a registry; we load the default
  registry once at module import time.
- Primitives expose two evaluator shapes: ``evaluate_outcomes(outcomes)``
  (takes pre-extracted iteration outcomes) and ``evaluate(handle)``
  (takes a runner handle with ``.run_dir``). Both are called the same
  way the runner calls them (scenario.py:628-631).
- ``_extract_outcomes_from_dir`` is the production extractor that
  feeds ``evaluate_outcomes``; we reuse it verbatim. (This is the
  F-017 ``event="iteration"``-only blind spot — the floor-preview
  reproduces it faithfully because that's what the runner does.)
- A minimal fake handle exposes only ``.run_dir`` because every
  ``evaluate`` codepath we have to reproduce ultimately falls back
  to reading log.ndjson via ``_events_from_handle`` when there is
  no in-memory event list on the handle. If a primitive needs
  more handle surface, the call raises and we report kind="error".

Caching:
- In-memory dict keyed by (run_dir.absolute, evaluator_signature).
- ``evaluator_signature`` = mtime_ns of primitives.py + scenario.py.
  Deliberately coarse: any change to either invalidates every cached
  entry. Acceptable because cache lookup is O(1) and re-evaluation
  is fast (single-bundle scope).
- Per-bundle entry also keys on assertions.json + log.ndjson mtime,
  so re-saved bundles invalidate their own slot.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


_CACHE_LOCK = threading.RLock()
_PREVIEW_CACHE: dict[tuple, list[dict]] = {}


def _registry():
    """Lazy-loaded production registry. Reload on cache miss only."""
    from profile_manager import primitives
    from profile_manager.scenario import DEFAULT_REGISTRY_PATH
    return primitives.load_registry(DEFAULT_REGISTRY_PATH)


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _evaluator_signature() -> int:
    """Sum of (primitives.py, scenario.py) mtime_ns. Any change to
    either invalidates the cache."""
    from profile_manager import primitives, scenario
    return _mtime(Path(primitives.__file__)) + _mtime(Path(scenario.__file__))


class _DashboardHandle:
    """Minimal handle surface — only ``.run_dir``. Production evaluators
    that need more (e.g. an in-memory event list) fall back to reading
    log.ndjson off disk via ``_events_from_handle``, which is exactly
    what we want for retro re-evaluation."""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir


def _evaluate_one(primitive_name: str, params: dict, recorded_result: str | None,
                  run_dir: Path, runtime: str | None, impl: str | None,
                  outcomes_from_dir) -> dict:
    """Run one assertion's production evaluator against a bundle.

    Returns a row dict with kind in {match, gap, error}, recorded
    result, preview result, and the evaluator's payload (note +
    evaluated_value) when applicable.
    """
    from profile_manager import primitives
    try:
        prim = primitives.instantiate(
            _registry(), name=primitive_name, params=params or {},
            runtime=runtime, target_implementation=impl,
        )
    except Exception as exc:  # noqa: BLE001 — surface registry errors
        return {
            "primitive": primitive_name,
            "params": params or {},
            "recorded_result": recorded_result,
            "preview_result": None,
            "kind": "error",
            "error": f"instantiate failed: {type(exc).__name__}: {exc}",
        }
    try:
        if hasattr(prim, "evaluate_outcomes"):
            live = prim.evaluate_outcomes(outcomes_from_dir)
        else:
            live = prim.evaluate(_DashboardHandle(run_dir))
    except Exception as exc:  # noqa: BLE001 — defensive
        return {
            "primitive": primitive_name,
            "params": params or {},
            "recorded_result": recorded_result,
            "preview_result": None,
            "kind": "error",
            "error": f"evaluate failed: {type(exc).__name__}: {exc}",
        }
    preview = live.get("result") if isinstance(live, dict) else None
    if preview is None:
        return {
            "primitive": primitive_name,
            "params": params or {},
            "recorded_result": recorded_result,
            "preview_result": None,
            "kind": "error",
            "error": "evaluator returned no result",
        }
    kind = "match" if preview == recorded_result else "gap"
    return {
        "primitive": primitive_name,
        "params": params or {},
        "recorded_result": recorded_result,
        "preview_result": preview,
        "kind": kind,
        "note": (live.get("note") if isinstance(live, dict) else None) or "",
        "evaluated_value": (live.get("evaluated_value") if isinstance(live, dict) else None) or {},
    }


def floor_preview(run_dir: Path) -> list[dict]:
    """Return one row per recorded assertion with the production
    evaluator's current verdict alongside.

    Cached per (run_dir, primitives+scenario mtime, assertions/log
    mtime). Defensive on every failure mode: returns an empty list for
    missing bundles, single error rows for malformed assertions.json.
    """
    if not run_dir.is_dir():
        return []
    assertions_path = run_dir / "assertions.json"
    log_path = run_dir / "log.ndjson"
    cache_key = (
        str(run_dir.absolute()),
        _evaluator_signature(),
        _mtime(assertions_path),
        _mtime(log_path),
    )
    with _CACHE_LOCK:
        cached = _PREVIEW_CACHE.get(cache_key)
        if cached is not None:
            return [dict(r) for r in cached]
    rows = _compute(run_dir)
    with _CACHE_LOCK:
        _PREVIEW_CACHE[cache_key] = [dict(r) for r in rows]
    return rows


def _compute(run_dir: Path) -> list[dict]:
    assertions = _read_json(run_dir / "assertions.json")
    if not isinstance(assertions, list):
        return []
    manifest = _read_json(run_dir / "manifest.json") or {}
    runtime = manifest.get("runtime")
    impl = (manifest.get("target") or {}).get("implementation")
    # Late import to avoid scenario.py loading at module import time.
    from profile_manager.scenario import _extract_outcomes_from_dir
    outcomes = _extract_outcomes_from_dir(run_dir)
    rows: list[dict] = []
    for entry in assertions:
        if not isinstance(entry, dict):
            continue
        primitive_name = entry.get("primitive") or ""
        if not primitive_name:
            continue
        rows.append(_evaluate_one(
            primitive_name=primitive_name,
            params=entry.get("params"),
            recorded_result=entry.get("result"),
            run_dir=run_dir,
            runtime=runtime,
            impl=impl,
            outcomes_from_dir=outcomes,
        ))
    return rows


def floor_preview_summary(rows: list[dict]) -> dict:
    """Roll up rows into counts: gap / match / error / total."""
    total = len(rows)
    gap = sum(1 for r in rows if r["kind"] == "gap")
    match = sum(1 for r in rows if r["kind"] == "match")
    error = sum(1 for r in rows if r["kind"] == "error")
    return {"total": total, "gap": gap, "match": match, "error": error,
            "any_gap": gap > 0}


def clear_cache() -> None:
    """Tests use this to start from a clean cache state."""
    with _CACHE_LOCK:
        _PREVIEW_CACHE.clear()


def cache_size() -> int:
    """Tests inspect cache occupancy via this helper."""
    with _CACHE_LOCK:
        return len(_PREVIEW_CACHE)
