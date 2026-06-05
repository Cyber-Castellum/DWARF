"""File-backed schedule store for /operate/schedule.

Item #19. Each entry is a small JSON record:
  {
    "id": "<uuid4 hex>",
    "name": "<operator-supplied label>",
    "scenario_id": "<scenario.id from the catalog>",
    "scenario_path": "<absolute path passed to cardano-profile scenario run>",
    "cron": "<5-field POSIX cron expression>",
    "enabled": true,
    "status": "pending|running|paused",
    "last_fired_at": "ISO-8601 UTC or null",
    "next_fire_at":  "ISO-8601 UTC or null",
    "last_run_id":   "<bundle run-id> or null",
    "fire_count":    <int>,
    "created_at":    "ISO-8601 UTC",
  }

Storage: a single JSON file at ``<state_dir>/schedule.json`` containing
``{"entries": [...]}``. Matches the file-backed pattern used by
notifications and dashboard config — no SQLite anywhere in the codebase.

Concurrency: a process-local threading.Lock guards the in-memory list;
on disk writes are atomic via ``os.replace`` of a tmp file. Cross-process
safety is not a concern — only the dashboard process writes here.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOCK = threading.RLock()


def _state_dir() -> Path:
    env = os.environ.get("ADA2_DWARF_STATE_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "dwarf" / "state"


def _store_path() -> Path:
    return _state_dir() / "schedule.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_raw() -> dict[str, Any]:
    path = _store_path()
    if not path.is_file():
        return {"entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"entries": []}
    if not isinstance(data, dict) or "entries" not in data:
        return {"entries": []}
    if not isinstance(data["entries"], list):
        return {"entries": []}
    return data


def _write_raw(data: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def list_entries() -> list[dict[str, Any]]:
    """Snapshot of every schedule entry, newest first by created_at."""
    with _LOCK:
        raw = _read_raw()
    entries = [dict(e) for e in raw["entries"] if isinstance(e, dict)]
    entries.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return entries


def get_entry(entry_id: str) -> dict[str, Any] | None:
    with _LOCK:
        raw = _read_raw()
    for e in raw["entries"]:
        if isinstance(e, dict) and e.get("id") == entry_id:
            return dict(e)
    return None


def create_entry(*, name: str, scenario_id: str, scenario_path: str,
                 cron: str) -> dict[str, Any]:
    """Append a new entry. Returns the created record. Raises ValueError
    if any required field is empty/invalid."""
    name = (name or "").strip()
    scenario_id = (scenario_id or "").strip()
    scenario_path = (scenario_path or "").strip()
    cron = (cron or "").strip()
    if not name or not scenario_id or not scenario_path or not cron:
        raise ValueError("name, scenario_id, scenario_path, cron are all required")
    # Cron syntax is validated up front so the operator gets the error
    # at create-time, not later when the scheduler tries to fire.
    from profile_manager.data.cron import parse_cron, next_fire_after
    parse_cron(cron)
    entry = {
        "id": uuid.uuid4().hex,
        "name": name[:128],
        "scenario_id": scenario_id[:256],
        "scenario_path": scenario_path[:1024],
        "cron": cron,
        "enabled": True,
        "status": "pending",
        "last_fired_at": None,
        "next_fire_at": next_fire_after(cron, time.time()),
        "last_run_id": None,
        "fire_count": 0,
        "created_at": _utc_now_iso(),
    }
    with _LOCK:
        raw = _read_raw()
        raw["entries"].append(entry)
        _write_raw(raw)
    return dict(entry)


def update_entry(entry_id: str, **fields: Any) -> dict[str, Any] | None:
    """Partial update; only fields named in ``fields`` are written."""
    with _LOCK:
        raw = _read_raw()
        for e in raw["entries"]:
            if isinstance(e, dict) and e.get("id") == entry_id:
                e.update(fields)
                _write_raw(raw)
                return dict(e)
    return None


def delete_entry(entry_id: str) -> bool:
    with _LOCK:
        raw = _read_raw()
        before = len(raw["entries"])
        raw["entries"] = [
            e for e in raw["entries"]
            if not (isinstance(e, dict) and e.get("id") == entry_id)
        ]
        if len(raw["entries"]) == before:
            return False
        _write_raw(raw)
    return True


def pause_entry(entry_id: str) -> dict[str, Any] | None:
    return update_entry(entry_id, status="paused", enabled=False)


def resume_entry(entry_id: str) -> dict[str, Any] | None:
    """Resume a paused entry. Recomputes next_fire_at from now so the
    pause window isn't burned through in a single tick."""
    entry = get_entry(entry_id)
    if entry is None:
        return None
    from profile_manager.data.cron import next_fire_after
    return update_entry(
        entry_id,
        status="pending",
        enabled=True,
        next_fire_at=next_fire_after(entry["cron"], time.time()),
    )


def mark_running(entry_id: str) -> dict[str, Any] | None:
    return update_entry(entry_id, status="running")


def record_fire(entry_id: str, *, run_id: str | None,
                fired_at_epoch: float) -> dict[str, Any] | None:
    """Update bookkeeping after a fire completes (success or failure).
    Recomputes next_fire_at from the just-fired-at timestamp."""
    entry = get_entry(entry_id)
    if entry is None:
        return None
    from profile_manager.data.cron import next_fire_after
    fired_iso = datetime.fromtimestamp(fired_at_epoch, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return update_entry(
        entry_id,
        last_fired_at=fired_iso,
        last_run_id=run_id,
        fire_count=entry.get("fire_count", 0) + 1,
        status="pending" if entry.get("enabled", True) else "paused",
        next_fire_at=next_fire_after(entry["cron"], fired_at_epoch),
    )
