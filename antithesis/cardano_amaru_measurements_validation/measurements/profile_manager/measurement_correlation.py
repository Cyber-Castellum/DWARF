"""Deterministic correlation of node, workload, fault, and window evidence."""
from __future__ import annotations

import json
from typing import Any, Iterable


_IDENTIFIERS = {
    "transactions": ("tx_id", "tx_hash"),
    "headers": ("header_hash", "header_id"),
    "blocks": ("block_hash", "block_id"),
    "peers": ("peer_id",),
    "traces": ("trace_id",),
    "spans": ("span_id", "parent_span_id"),
}


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _window_at(elapsed: float | None, windows: Iterable[dict[str, Any]]) -> str | None:
    if elapsed is None:
        return None
    for window in windows:
        start = _as_number(window.get("start"))
        end = _as_number(window.get("end"))
        if start is not None and elapsed >= start and (end is None or elapsed < end):
            return window.get("phase_id") or window.get("window_id")
    return None


def _faults_at(elapsed: float | None, faults: Iterable[dict[str, Any]]) -> list[str]:
    if elapsed is None:
        return []
    active = []
    for fault in faults:
        start = _as_number(fault.get("start"))
        end = _as_number(fault.get("end"))
        if start is not None and elapsed >= start and (end is None or elapsed < end):
            fault_id = fault.get("fault_id")
            if isinstance(fault_id, str):
                active.append(fault_id)
    return active


def correlate_measurement_events(
    events: Iterable[dict[str, Any]],
    *,
    windows: Iterable[dict[str, Any]] = (),
    faults: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Index supported identifiers and attach temporal scenario context."""
    window_list = list(windows)
    fault_list = list(faults)
    indexes: dict[str, dict[str, list[int]]] = {
        group: {} for group in _IDENTIFIERS
    }
    normalized = []
    uncorrelated = []
    for event_index, source in enumerate(events):
        event = json.loads(json.dumps(source))
        event["event_index"] = event_index
        elapsed = _as_number(event.get("elapsed_seconds"))
        event["window"] = _window_at(elapsed, window_list)
        event["active_faults"] = _faults_at(elapsed, fault_list)
        found = False
        for group, fields in _IDENTIFIERS.items():
            seen_values = set()
            for field in fields:
                value = event.get(field)
                if value is None:
                    continue
                value = str(value)
                if not value or value in seen_values:
                    continue
                indexes[group].setdefault(value, []).append(event_index)
                seen_values.add(value)
                found = True
        if not found:
            uncorrelated.append(
                {
                    "event_index": event_index,
                    "kind": event.get("kind"),
                    "reason": "no supported correlation identifier",
                }
            )
        normalized.append(event)
    return {
        "schema_version": "v1",
        "events": normalized,
        "indexes": indexes,
        "uncorrelated": uncorrelated,
    }

