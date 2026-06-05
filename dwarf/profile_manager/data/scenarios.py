"""Pure scenario catalog helpers for the dashboard data layer."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any


# Slice 45 — per-file mtime-keyed cache. Re-parsing 391 scenario YAMLs
# on every /operate/scenarios request is wasteful; the cache is
# invalidated when the directory mtime OR any individual file mtime
# changes, so editing/adding/removing a scenario rebuilds only what
# changed (one YAML, not all of them).
_SCENARIO_CACHE_LOCK = threading.Lock()
_SCENARIO_DIR_MTIME: dict[str, int] = {}
_SCENARIO_FILE_CACHE: dict[str, tuple[int, dict[str, Any]]] = {}
_SCENARIO_LIST_CACHE: dict[str, tuple[int, list[dict[str, Any]]]] = {}


def _scenarios_dir():
    env = os.environ.get("ADA2_DWARF_SCENARIOS_DIR")
    if env:
        return Path(env)
    # data/scenarios.py -> data/ -> profile_manager/ -> dwarf/
    return Path(__file__).resolve().parents[2] / "scenarios"


def _humanize_scenario_id(scenario_id):
    """Turn 'amaru-cbor-tx-body-fuzz' into ('Amaru', 'transaction body')."""
    if not scenario_id:
        return None, None
    impl = None
    if scenario_id.startswith("amaru-"):
        impl = "Amaru"
        rest = scenario_id[len("amaru-"):]
    elif scenario_id.startswith("cardano-node-"):
        impl = "cardano-node"
        rest = scenario_id[len("cardano-node-"):]
    else:
        return None, None
    rest = rest.removeprefix("cbor-")
    rest = rest.removesuffix("-fuzz")
    parser_map = {
        "tx-body": "transaction body",
        "block-header": "block header",
        "certificate": "certificate",
        "auxiliary-data": "auxiliary data",
        "block": "block",
    }
    return impl, parser_map.get(rest, rest.replace("-", " "))


def _parse_scenario_file(path: Path) -> dict[str, Any] | None:
    """Load + project a single scenario YAML into a render-ready row.
    Returns None on parse error so the caller can drop bad files."""
    from profile_manager import scenario as scen
    try:
        s = scen.load_scenario(path)
    except Exception:  # noqa: BLE001
        return None
    return {
        "id": s.id,
        "title": s.title,
        "path": str(path),
        "runtime": s.runtime,
        "related_milestones": list(getattr(s, "related_milestones", []) or []),
        "m1_trace": dict(getattr(s, "m1_trace", {}) or {}),
        "evidence_intent": getattr(s, "evidence_intent", None),
        "promotion_blockers": list(getattr(s, "promotion_blockers", []) or []),
    }


def _scenario_dir_mtime_ns(scenarios_dir: Path) -> int:
    try:
        return scenarios_dir.stat().st_mtime_ns
    except OSError:
        return 0


def invalidate_scenario_cache() -> None:
    """Drop every cached entry — useful for tests."""
    with _SCENARIO_CACHE_LOCK:
        _SCENARIO_DIR_MTIME.clear()
        _SCENARIO_FILE_CACHE.clear()
        _SCENARIO_LIST_CACHE.clear()


def _list_scenarios_for_compare():
    """Return scenario rows from dwarf/scenarios/.

    Slice 45 — hot reload via per-file mtime cache. The directory's
    mtime is checked first; if it hasn't changed AND every file's
    mtime matches the cached one, the prior list is reused. Otherwise
    each YAML is rescanned and only changed/new files re-parsed —
    unchanged files keep their cached parse output. Removing a file
    drops its cache entry on the next call.
    """
    scenarios_dir = _scenarios_dir()
    if not scenarios_dir.is_dir():
        return []
    dir_key = str(scenarios_dir)
    dir_mtime = _scenario_dir_mtime_ns(scenarios_dir)

    paths = sorted(p for p in scenarios_dir.glob("*.yaml") if p.parent.name != "pending")
    file_mtimes: list[tuple[Path, int]] = []
    for p in paths:
        try:
            file_mtimes.append((p, p.stat().st_mtime_ns))
        except OSError:
            continue

    with _SCENARIO_CACHE_LOCK:
        cached_dir_mtime = _SCENARIO_DIR_MTIME.get(dir_key)
        cached_list = _SCENARIO_LIST_CACHE.get(dir_key)
        if (
            cached_dir_mtime == dir_mtime
            and cached_list is not None
            and cached_list[0] == sum(m for _, m in file_mtimes)
        ):
            return list(cached_list[1])

        out: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for path, mtime in file_mtimes:
            key = str(path)
            seen_paths.add(key)
            entry = _SCENARIO_FILE_CACHE.get(key)
            if entry is not None and entry[0] == mtime:
                out.append(entry[1])
                continue
            parsed = _parse_scenario_file(path)
            if parsed is None:
                _SCENARIO_FILE_CACHE.pop(key, None)
                continue
            _SCENARIO_FILE_CACHE[key] = (mtime, parsed)
            out.append(parsed)
        # Drop cache entries for removed files.
        for stale in [k for k in _SCENARIO_FILE_CACHE if k not in seen_paths and k.startswith(dir_key)]:
            _SCENARIO_FILE_CACHE.pop(stale, None)
        _SCENARIO_DIR_MTIME[dir_key] = dir_mtime
        _SCENARIO_LIST_CACHE[dir_key] = (sum(m for _, m in file_mtimes), list(out))
        return out
