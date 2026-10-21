#!/usr/bin/env python3

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _append_ndjson(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")


def run_dir() -> Path | None:
    value = os.environ.get("ADA2_DWARF_RUN_DIR")
    return Path(value) if value else None


def target_event_log_path() -> Path | None:
    value = os.environ.get("ADA2_DWARF_TARGET_EVENT_LOG")
    return Path(value) if value else None


def runtime_metrics_dir() -> Path | None:
    value = os.environ.get("ADA2_DWARF_RUNTIME_METRICS_DIR")
    return Path(value) if value else None


def emit_target_event(*, primitive: str, event: str, payload=None, level: str = "info", phase: str = "runtime") -> None:
    path = target_event_log_path()
    if path is None:
        return
    entry = {
        "ts": _utc_now_iso(),
        "phase": phase,
        "primitive": primitive,
        "level": level,
        "event": event,
    }
    if payload is not None:
        entry["payload"] = payload
    _append_ndjson(path, entry)


def emit_runtime_metric(name: str, *, value, meta=None) -> None:
    metrics_root = runtime_metrics_dir()
    if metrics_root is None:
        return
    entry = {
        "ts": _utc_now_iso(),
        "value": value,
    }
    if meta is not None:
        entry["meta"] = meta
    _append_ndjson(metrics_root / f"{name}.ndjson", entry)
