"""Collectors for stock Amaru b159172 telemetry exports.

The collector consumes bounded Amaru JSON traces and OTLP JSON exports. Point
event durations come from fields emitted by Amaru. Completed span durations
come from either OTLP start/end times or matching enter/exit records emitted by
Amaru's own JSON tracing layer; host-side log arrival time is never used.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from profile_manager.measurement_correlation import correlate_measurement_events
from profile_manager.measurement_precision import precise_microseconds
from profile_manager.measurement_report import distribution_summary


AMARU_SOURCE_REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"
DEFAULT_MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_PATHS = 16


_JSON_EVENT_KINDS = {
    ("amaru::consensus::perf::header", "lifecycle"): "header-lifecycle",
    ("amaru::consensus::perf::fork", "switch"): "fork-switch",
    ("amaru::mempool::transaction", "received"): "mempool-received",
    ("amaru::mempool::transaction", "accepted"): "mempool-accepted",
    ("amaru::mempool::transaction", "rejected"): "mempool-rejected",
    ("amaru::mempool::transaction", "evicted"): "mempool-evicted",
    ("amaru::mempool::state", "update"): "mempool-state",
    ("amaru::ledger::rules", "phase_one"): "ledger-phase-one",
    ("amaru::ledger::transaction::script", "execute"): "plutus-execution",
    ("amaru::protocols::keepalive::peer", "round_trip"): "keepalive-round-trip",
    ("amaru::protocols::manager::peer", "connected"): "peer-connected",
    ("amaru::protocols::mux", "failed"): "mux-failed",
    ("amaru::ledger::tip", "update"): "chain-tip",
}

# The pinned binary's JSON formatter retains the Rust module as ``target`` and
# places the logical schema path in ``fields.message``.  Keep this separate
# from the logical target/name representation used by trace fixtures and OTLP.
_JSON_MESSAGE_KINDS = {
    ("amaru::consensus", "perf.header.lifecycle"): "header-lifecycle",
    ("amaru::consensus", "perf.fork.switch"): "fork-switch",
    ("amaru::consensus", "mempool.transaction.received"): "mempool-received",
    ("amaru::consensus", "mempool.transaction.accepted"): "mempool-accepted",
    ("amaru::consensus", "mempool.transaction.rejected"): "mempool-rejected",
    ("amaru::consensus", "mempool.transaction.evicted"): "mempool-evicted",
    ("amaru::consensus", "mempool.state.update"): "mempool-state",
    ("amaru::ledger", "rules.phase_one"): "ledger-phase-one",
    ("amaru::ledger", "transaction.script.execute"): "plutus-execution",
    ("amaru::protocols", "keepalive.peer.round_trip"): "keepalive-round-trip",
    ("amaru::protocols", "manager.peer.connected"): "peer-connected",
    ("amaru::protocols", "mux.failed"): "mux-failed",
    ("amaru::ledger", "tip.update"): "chain-tip",
    ("amaru::ledger", "measurement.block_apply"): "block-apply",
}

_JSON_SPAN_KINDS = {
    ("amaru::ledger", "block.prepare"): "block-prepare",
    ("amaru::ledger", "block.apply"): "block-apply",
    ("amaru::ledger", "epoch_transition.apply"): "epoch-transition",
    ("amaru::ledger", "transaction.validate"): "transaction-validation",
    ("amaru::ledger", "transaction.script.execute"): "plutus-execution",
}

_OTLP_SPAN_KINDS = {
    ("amaru::ledger::block", "prepare"): "block-prepare",
    ("amaru::ledger::block", "apply"): "block-apply",
    ("amaru::ledger::epoch_transition", "apply"): "epoch-transition",
    ("amaru::ledger::transaction", "validate"): "transaction-validation",
}

STOCK_MEASUREMENT_IDS = (
    "amaru-stock-header-lifecycle",
    "amaru-stock-fork-switch",
    "amaru-stock-mempool",
    "amaru-stock-ledger-rules",
    "amaru-stock-plutus-execution",
    "amaru-stock-block-epoch",
    "amaru-stock-network",
)


def _bounded_read(path: Path, *, max_bytes: int, start: int = 0) -> tuple[bytes, bool]:
    size = path.stat().st_size
    if start > size:
        start = 0
    available = max(0, size - start)
    with path.open("rb") as fp:
        fp.seek(start)
        data = fp.read(max_bytes)
    return data, available > len(data)


def _event_name(record: dict[str, Any]) -> str | None:
    span = record.get("span")
    if isinstance(span, dict) and isinstance(span.get("name"), str):
        return span["name"].lower()
    fields = record.get("fields")
    if isinstance(fields, dict) and isinstance(fields.get("message"), str):
        return fields["message"].split(".")[-1].lower()
    return None


def _normalize_json_event(record: dict[str, Any]) -> dict[str, Any] | None:
    target = record.get("target")
    fields = record.get("fields")
    name = _event_name(record)
    if not isinstance(target, str) or not isinstance(fields, dict) or name is None:
        return None
    message = fields.get("message")
    kind = (
        _JSON_MESSAGE_KINDS.get((target, message))
        if isinstance(message, str)
        else None
    ) or _JSON_EVENT_KINDS.get((target, name))
    if kind is None:
        return None
    event: dict[str, Any] = {
        "kind": kind,
        "timestamp": record.get("timestamp"),
        "target": target,
        "name": name,
        "fields": fields,
    }
    if message == "measurement.block_apply":
        elapsed_nanos, duration_micros = precise_microseconds(
            fields,
            nanos_field="elapsed_nanos",
            micros_field="elapsed_micros",
        )
        event["duration_micros"] = duration_micros
        event["timing_source"] = "patched-monotonic-nanoseconds"
        if elapsed_nanos is not None:
            event["elapsed_nanos"] = elapsed_nanos
    span = record.get("span")
    if isinstance(span, dict):
        event["span_name"] = span.get("name")
    if record.get("id") is not None:
        event["span_id"] = str(record["id"])
    if record.get("parent_id") is not None:
        event["parent_span_id"] = str(record["parent_id"])
    transaction_id = fields.get("id") or fields.get("tx_id") or fields.get("transaction_id")
    if transaction_id is not None:
        event["tx_id"] = str(transaction_id)
    if fields.get("header_hash") is not None:
        event["header_hash"] = str(fields["header_hash"])
    if fields.get("block_hash") is not None:
        event["block_hash"] = str(fields["block_hash"])
    if fields.get("peer") is not None:
        event["peer_id"] = str(fields["peer"])
    return event


def _json_span_kind(record: dict[str, Any]) -> str | None:
    target = record.get("target")
    span = record.get("span")
    if not isinstance(target, str) or not isinstance(span, dict):
        return None
    name = span.get("name")
    if not isinstance(name, str):
        return None
    return _JSON_SPAN_KINDS.get((target, name.lower()))


def _json_span_key(record: dict[str, Any]) -> tuple[str, str, str, str]:
    span = record.get("span") or {}
    return (
        str(record.get("target") or ""),
        str(span.get("name") or "").lower(),
        str(record.get("id") or ""),
        str(record.get("parent_id") or ""),
    )


def _normalize_json_span(
    start: dict[str, Any], end: dict[str, Any], *, kind: str
) -> dict[str, Any] | None:
    started_at = _timestamp_datetime(start.get("timestamp"))
    ended_at = _timestamp_datetime(end.get("timestamp"))
    if started_at is None or ended_at is None or ended_at < started_at:
        return None
    elapsed = ended_at - started_at
    duration_micros = float(
        elapsed.days * 86_400_000_000
        + elapsed.seconds * 1_000_000
        + elapsed.microseconds
    )
    start_fields = start.get("fields") if isinstance(start.get("fields"), dict) else {}
    end_fields = end.get("fields") if isinstance(end.get("fields"), dict) else {}
    fields = {
        key: value
        for key, value in {**start_fields, **end_fields}.items()
        if key != "message"
    }
    span = start.get("span") or {}
    event: dict[str, Any] = {
        "kind": kind,
        "target": start.get("target"),
        "name": str(span.get("name") or "").lower(),
        "timestamp": end.get("timestamp"),
        "start_timestamp": start.get("timestamp"),
        "end_timestamp": end.get("timestamp"),
        "duration_micros": duration_micros,
        "timing_source": "paired-node-json-span-events",
        "fields": fields,
    }
    if start.get("id") is not None:
        event["span_id"] = str(start["id"])
    if start.get("parent_id") is not None:
        event["parent_span_id"] = str(start["parent_id"])
    if fields.get("id") is not None:
        event["tx_id"] = str(fields["id"])
    if fields.get("header_hash") is not None:
        event["header_hash"] = str(fields["header_hash"])
    if fields.get("block_hash") is not None:
        event["block_hash"] = str(fields["block_hash"])
    if fields.get("peer") is not None:
        event["peer_id"] = str(fields["peer"])
    return event


def _otlp_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "boolValue", "intValue", "doubleValue"):
        if key not in value:
            continue
        result = value[key]
        if key == "intValue":
            try:
                return int(result)
            except (TypeError, ValueError):
                return result
        return result
    return value


def _otlp_attributes(items: Any) -> dict[str, Any]:
    attributes = {}
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and isinstance(item.get("key"), str):
            attributes[item["key"]] = _otlp_value(item.get("value"))
    return attributes


def _normalize_otlp_span(
    span: dict[str, Any], *, resource: dict[str, Any]
) -> dict[str, Any] | None:
    attributes = _otlp_attributes(span.get("attributes"))
    target = attributes.get("code.namespace") or attributes.get("target")
    name = span.get("name")
    if not isinstance(target, str) or not isinstance(name, str):
        return None
    kind = _OTLP_SPAN_KINDS.get((target, name.lower()))
    if kind is None:
        return None
    try:
        start = int(span["startTimeUnixNano"])
        end = int(span["endTimeUnixNano"])
    except (KeyError, TypeError, ValueError):
        return None
    if end < start:
        return None
    event: dict[str, Any] = {
        "kind": kind,
        "target": target,
        "name": name.lower(),
        "duration_micros": (end - start) / 1000,
        "start_time_unix_nano": start,
        "end_time_unix_nano": end,
        "fields": attributes,
        "resource": resource,
        "timing_source": "otlp-completed-span",
    }
    for source, destination in (
        ("traceId", "trace_id"),
        ("spanId", "span_id"),
        ("parentSpanId", "parent_span_id"),
    ):
        if span.get(source):
            event[destination] = str(span[source])
    transaction_id = attributes.get("tx_id") or attributes.get("transaction_id")
    if transaction_id is not None:
        event["tx_id"] = str(transaction_id)
    if attributes.get("header_hash") is not None:
        event["header_hash"] = str(attributes["header_hash"])
    if attributes.get("block_hash") is not None:
        event["block_hash"] = str(attributes["block_hash"])
    if attributes.get("peer") is not None:
        event["peer_id"] = str(attributes["peer"])
    return event


def _iter_otlp_spans(document: dict[str, Any]) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    for resource_spans in document.get("resourceSpans") or []:
        if not isinstance(resource_spans, dict):
            continue
        resource = _otlp_attributes((resource_spans.get("resource") or {}).get("attributes"))
        scopes = resource_spans.get("scopeSpans") or resource_spans.get("instrumentationLibrarySpans") or []
        for scope in scopes:
            if not isinstance(scope, dict):
                continue
            for span in scope.get("spans") or []:
                if isinstance(span, dict):
                    yield span, resource


def _load_amaru_telemetry_bytes(
    *,
    json_sources: Iterable[tuple[str, bytes, bool]],
    otlp_sources: Iterable[tuple[str, bytes, bool]],
) -> dict[str, Any]:
    events = []
    rejected = Counter()
    ignored = Counter()
    source_count = 0
    truncated_sources = []
    raw_sources: dict[str, list[tuple[str, bytes]]] = {"json": [], "otlp": []}
    pending_json_spans: dict[
        tuple[str, str, str, str], list[tuple[str, dict[str, Any]]]
    ] = {}
    for name, raw, truncated in json_sources:
        raw_sources["json"].append((name, raw))
        if truncated:
            truncated_sources.append(name)
        for line in raw.splitlines():
            if not line.strip():
                continue
            source_count += 1
            try:
                record = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                rejected["invalid-json"] += 1
                continue
            if isinstance(record, dict):
                span_kind = _json_span_kind(record)
                fields = record.get("fields")
                marker = fields.get("message") if isinstance(fields, dict) else None
                if span_kind is not None and marker in {"enter", "exit"}:
                    key = _json_span_key(record)
                    if marker == "enter":
                        pending_json_spans.setdefault(key, []).append((span_kind, record))
                    else:
                        starts = pending_json_spans.get(key) or []
                        if not starts:
                            rejected["unmatched-json-span-exit"] += 1
                        else:
                            started_kind, start = starts.pop()
                            if not starts:
                                pending_json_spans.pop(key, None)
                            normalized_span = _normalize_json_span(
                                start, record, kind=started_kind
                            )
                            if normalized_span is None:
                                rejected["invalid-json-span-timing"] += 1
                            else:
                                events.append(normalized_span)
                    continue
            normalized = _normalize_json_event(record) if isinstance(record, dict) else None
            if normalized is None:
                ignored["unsupported-json-event"] += 1
            else:
                events.append(normalized)
    incomplete_spans = sum(len(starts) for starts in pending_json_spans.values())
    if incomplete_spans:
        rejected["incomplete-json-span"] += incomplete_spans
    for name, raw, truncated in otlp_sources:
        raw_sources["otlp"].append((name, raw))
        if truncated:
            truncated_sources.append(name)
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            source_count += 1
            rejected["invalid-json"] += 1
            continue
        if not isinstance(document, dict):
            source_count += 1
            rejected["invalid-otlp-envelope"] += 1
            continue
        for span, resource in _iter_otlp_spans(document):
            source_count += 1
            normalized = _normalize_otlp_span(span, resource=resource)
            if normalized is None:
                rejected["unsupported-or-incomplete-otlp-span"] += 1
            else:
                events.append(normalized)
    return {
        "schema_version": "v1",
        "source_revision": AMARU_SOURCE_REVISION,
        "source_record_count": source_count,
        "normalized_record_count": len(events),
        "ignored_record_count": sum(ignored.values()),
        "ignored_reasons": dict(sorted(ignored.items())),
        "rejected_record_count": sum(rejected.values()),
        "rejection_reasons": dict(sorted(rejected.items())),
        "truncated_sources": sorted(truncated_sources),
        "events": events,
        "_raw_sources": raw_sources,
    }


def load_amaru_telemetry(
    *,
    json_trace_paths: Iterable[str | Path] = (),
    otlp_trace_paths: Iterable[str | Path] = (),
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> dict[str, Any]:
    if max_source_bytes <= 0 or max_source_bytes > 64 * 1024 * 1024:
        raise ValueError("max_source_bytes must be within 1..67108864")
    json_paths = [Path(path) for path in json_trace_paths]
    otlp_paths = [Path(path) for path in otlp_trace_paths]
    if len(json_paths) + len(otlp_paths) > MAX_SOURCE_PATHS:
        raise ValueError(f"at most {MAX_SOURCE_PATHS} telemetry source paths are allowed")
    json_sources = []
    otlp_sources = []
    for path in json_paths:
        raw, truncated = _bounded_read(path, max_bytes=max_source_bytes)
        json_sources.append((str(path), raw, truncated))
    for path in otlp_paths:
        raw, truncated = _bounded_read(path, max_bytes=max_source_bytes)
        otlp_sources.append((str(path), raw, truncated))
    result = _load_amaru_telemetry_bytes(
        json_sources=json_sources, otlp_sources=otlp_sources
    )
    result.pop("_raw_sources", None)
    return result


def _samples(events: Iterable[dict[str, Any]], kind: str, field: str, unit: str) -> dict[str, Any]:
    return distribution_summary(
        [
            {"value": event["fields"].get(field), "unit": unit}
            for event in events
            if event["kind"] == kind and event["fields"].get(field) is not None
        ],
        unit=unit,
    )


def _samples_by_outcome(
    events: Iterable[dict[str, Any]], kind: str, field: str, unit: str
) -> dict[str, dict[str, Any]]:
    matching = [
        event
        for event in events
        if event["kind"] == kind
        and event["fields"].get(field) is not None
        and isinstance(event["fields"].get("outcome"), str)
        and event["fields"]["outcome"]
    ]
    outcomes = sorted({event["fields"]["outcome"] for event in matching})
    return {
        outcome: distribution_summary(
            [
                {"value": event["fields"][field], "unit": unit}
                for event in matching
                if event["fields"]["outcome"] == outcome
            ],
            unit=unit,
        )
        for outcome in outcomes
    }


def _header_arrival_samples(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Reject current release traces whose arrival delta mixes clock domains."""
    matching = [event for event in events if event["kind"] == "header-lifecycle"]
    usable = [
        event
        for event in matching
        if event["fields"].get("slot_start_to_header_micros") is not None
        and not (
            event["fields"].get("message") == "perf.header.lifecycle"
            and event["fields"].get("slot_start_clock_domain") != "shared"
        )
    ]
    summary = _samples(
        usable, "header-lifecycle", "slot_start_to_header_micros", "us"
    )
    if matching and not usable:
        summary["reason"] = (
            "the pinned Amaru release exports header receipt in the global epoch "
            "and slot start in chain-relative time; mixed clock domains cannot "
            "produce an arrival latency"
        )
    return summary


def _header_arrival_samples_by_outcome(
    events: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    usable = [
        event
        for event in events
        if not (
            event["kind"] == "header-lifecycle"
            and event["fields"].get("message") == "perf.header.lifecycle"
            and event["fields"].get("slot_start_clock_domain") != "shared"
        )
    ]
    return _samples_by_outcome(
        usable, "header-lifecycle", "slot_start_to_header_micros", "us"
    )


def _span_samples(events: Iterable[dict[str, Any]], kind: str) -> dict[str, Any]:
    matching = [
        event
        for event in events
        if event["kind"] == kind and event.get("duration_micros") is not None
    ]
    precise = [
        event
        for event in matching
        if event.get("timing_source") == "patched-monotonic-nanoseconds"
    ]
    if precise:
        matching = precise
    summary = distribution_summary(
        [
            {"value": event.get("duration_micros"), "unit": "us"}
            for event in matching
        ],
        unit="us",
    )
    timing_sources = sorted(
        {
            str(event["timing_source"])
            for event in matching
            if event.get("timing_source")
        }
    )
    if timing_sources:
        summary["timing_sources"] = timing_sources
    return summary


def _unavailable_span(kind: str, summary: dict[str, Any]) -> dict[str, Any]:
    if summary["status"] != "unavailable":
        return summary
    return {
        **summary,
        "reason": f"no completed stock telemetry span was exported for {kind}",
    }


def _count(events: Iterable[dict[str, Any]], kind: str) -> dict[str, Any]:
    return {
        "status": "available",
        "unit": "events",
        "value": sum(1 for event in events if event["kind"] == kind),
    }


def _timestamp_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _mempool_terminal_latency(events: list[dict[str, Any]]) -> dict[str, Any]:
    received: dict[str, datetime] = {}
    samples = []
    unmatched_terminal_count = 0
    for event in events:
        tx_id = event.get("tx_id")
        timestamp = _timestamp_datetime(event.get("timestamp"))
        if not isinstance(tx_id, str) or timestamp is None:
            continue
        if event["kind"] == "mempool-received":
            received[tx_id] = timestamp
            continue
        if event["kind"] not in {
            "mempool-accepted",
            "mempool-rejected",
            "mempool-evicted",
        }:
            continue
        started = received.get(tx_id)
        if started is None or timestamp < started:
            unmatched_terminal_count += 1
            continue
        elapsed = timestamp - started
        elapsed_micros = (
            elapsed.days * 86_400_000_000
            + elapsed.seconds * 1_000_000
            + elapsed.microseconds
        )
        samples.append(
            {
                "transaction_id": tx_id,
                "outcome": event["kind"].removeprefix("mempool-"),
                "elapsed_micros": float(elapsed_micros),
            }
        )
    outcomes = sorted({sample["outcome"] for sample in samples})
    return {
        "all": distribution_summary(
            [
                {"value": sample["elapsed_micros"], "unit": "us"}
                for sample in samples
            ],
            unit="us",
        ),
        "by_outcome": {
            outcome: distribution_summary(
                [
                    {"value": sample["elapsed_micros"], "unit": "us"}
                    for sample in samples
                    if sample["outcome"] == outcome
                ],
                unit="us",
            )
            for outcome in outcomes
        },
        "samples": samples,
        "unmatched_terminal_count": unmatched_terminal_count,
        "boundary": "mempool received to terminal accepted/rejected/evicted trace",
    }


def _build_measurements(measurement_id: str, events: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    claims: dict[str, Any] = {}
    if measurement_id == "amaru-stock-header-lifecycle":
        claims["exclusive_adopt_duration"] = "unavailable"
        return {
            "header_slot_start_to_reception": _header_arrival_samples(events),
            "header_slot_start_to_reception_by_outcome": _header_arrival_samples_by_outcome(events),
            "header_reception_to_fetch_request": _samples(events, "header-lifecycle", "block_fetch_wait_micros", "us"),
            "header_reception_to_fetch_request_by_outcome": _samples_by_outcome(
                events, "header-lifecycle", "block_fetch_wait_micros", "us"
            ),
            "block_fetch": _samples(events, "header-lifecycle", "block_fetch_micros", "us"),
            "block_fetch_by_outcome": _samples_by_outcome(
                events, "header-lifecycle", "block_fetch_micros", "us"
            ),
            "header_reception_to_terminal_forward": _samples(events, "header-lifecycle", "forward_micros", "us"),
            "header_reception_to_terminal_forward_by_outcome": _samples_by_outcome(
                events, "header-lifecycle", "forward_micros", "us"
            ),
        }, claims
    if measurement_id == "amaru-stock-fork-switch":
        return {
            "fork_switch": _samples(events, "fork-switch", "duration_micros", "us"),
            "fork_switch_by_outcome": _samples_by_outcome(
                events, "fork-switch", "duration_micros", "us"
            ),
        }, claims
    if measurement_id == "amaru-stock-mempool":
        rejected = Counter(
            str(event["fields"].get("reason") or "unknown")
            for event in events if event["kind"] == "mempool-rejected"
        )
        rejected_summary = _count(events, "mempool-rejected")
        rejected_summary["by_reason"] = dict(sorted(rejected.items()))
        return {
            "mempool_received": _count(events, "mempool-received"),
            "mempool_accepted": _count(events, "mempool-accepted"),
            "mempool_rejected": rejected_summary,
            "mempool_evicted": _count(events, "mempool-evicted"),
            "mempool_tx_count": _samples(events, "mempool-state", "tx_count", "tx"),
            "mempool_size_bytes": _samples(events, "mempool-state", "size_bytes", "bytes"),
            "mempool_terminal_latency": _mempool_terminal_latency(events),
        }, claims
    if measurement_id == "amaru-stock-ledger-rules":
        measurements = {
            "transfer_validation": _unavailable_span(
                "transaction-validation", _span_samples(events, "transaction-validation")
            )
        }
        fields = sorted({
            field
            for event in events if event["kind"] == "ledger-phase-one"
            for field in event["fields"] if field.endswith("_micros")
        })
        for field in fields:
            name = "ledger_rule_" + field.removesuffix("_micros")
            measurements[name] = _samples(events, "ledger-phase-one", field, "us")
        return measurements, claims
    if measurement_id == "amaru-stock-plutus-execution":
        return {
            "virtual_machine_acquire_arena": _samples(events, "plutus-execution", "acquire_arena_micros", "us"),
            "virtual_machine_decode_script": _samples(events, "plutus-execution", "decode_script_micros", "us"),
            "virtual_machine_build_program": _samples(events, "plutus-execution", "build_uplc_program_micros", "us"),
            "virtual_machine_evaluate": _samples(events, "plutus-execution", "evaluate_uplc_program_micros", "us"),
        }, claims
    if measurement_id == "amaru-stock-block-epoch":
        return {
            "block_prepare": _unavailable_span("block-prepare", _span_samples(events, "block-prepare")),
            "block_application": _unavailable_span("block-apply", _span_samples(events, "block-apply")),
            "epoch_transition": _unavailable_span("epoch-transition", _span_samples(events, "epoch-transition")),
        }, claims
    if measurement_id == "amaru-stock-network":
        return {
            "keepalive_round_trip": _samples(events, "keepalive-round-trip", "round_trip_micros", "us"),
            "peer_connected": _count(events, "peer-connected"),
            "mux_failed": _count(events, "mux-failed"),
            "chain_tip_updates": _count(events, "chain-tip"),
        }, claims
    return {}, claims


class AmaruStockCollector:
    """Lifecycle adapter over trusted runtime-provided Amaru telemetry paths."""

    def __init__(
        self,
        entry: dict[str, Any],
        *,
        json_trace_paths: Iterable[str | Path],
        otlp_trace_paths: Iterable[str | Path],
        include_existing: bool = False,
        allow_missing_at_start: bool = False,
        max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
        source_revision: str | None = AMARU_SOURCE_REVISION,
    ) -> None:
        self.entry = entry
        self.measurement_id = entry["id"]
        self.source_revision = source_revision
        self.json_paths = [Path(path) for path in json_trace_paths]
        self.otlp_paths = [Path(path) for path in otlp_trace_paths]
        if len(self.json_paths) + len(self.otlp_paths) > MAX_SOURCE_PATHS:
            raise ValueError(f"at most {MAX_SOURCE_PATHS} telemetry source paths are allowed")
        if max_source_bytes <= 0 or max_source_bytes > 64 * 1024 * 1024:
            raise ValueError("max_source_bytes must be within 1..67108864")
        self.include_existing = include_existing
        self.allow_missing_at_start = allow_missing_at_start
        self.max_source_bytes = max_source_bytes
        self._offsets: dict[Path, int] = {}
        self._markers: list[dict[str, Any]] = []

    def prepare(self, context) -> None:
        for path in [*self.json_paths, *self.otlp_paths]:
            if not path.is_file():
                if not self.allow_missing_at_start:
                    raise FileNotFoundError(
                        f"Amaru telemetry source is unavailable: {path}"
                    )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
        context.collector_dir.mkdir(parents=True, exist_ok=True)

    def start(self, context) -> None:
        self._offsets = {
            path: 0 if self.include_existing else path.stat().st_size
            for path in [*self.json_paths, *self.otlp_paths]
        }

    def on_marker(self, marker, context) -> None:
        self._markers.append(dict(marker))

    def stop(self, context) -> None:
        return None

    def finalize(self, context) -> dict[str, Any]:
        json_sources = []
        otlp_sources = []
        for path in self.json_paths:
            raw, truncated = _bounded_read(
                path, max_bytes=self.max_source_bytes, start=self._offsets.get(path, 0)
            )
            json_sources.append((str(path), raw, truncated))
        for path in self.otlp_paths:
            raw, truncated = _bounded_read(
                path, max_bytes=self.max_source_bytes, start=self._offsets.get(path, 0)
            )
            otlp_sources.append((str(path), raw, truncated))
        loaded = _load_amaru_telemetry_bytes(
            json_sources=json_sources, otlp_sources=otlp_sources
        )
        loaded["source_revision"] = self.source_revision
        raw_sources = loaded.pop("_raw_sources")
        raw_dir = context.artifact_path("raw")
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "amaru-json.ndjson").write_bytes(
            b"\n".join(raw for _name, raw in raw_sources["json"])
        )
        (raw_dir / "otlp.jsonl").write_bytes(
            b"\n".join(raw for _name, raw in raw_sources["otlp"])
        )
        normalized_path = context.artifact_path("normalized.ndjson")
        normalized_path.write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in loaded["events"]),
            encoding="utf-8",
        )
        correlations = correlate_measurement_events(loaded["events"])
        context.write_json("correlations.json", correlations)
        measurements, claims = _build_measurements(
            self.measurement_id, loaded["events"]
        )
        result = {
            "schema_version": "v1",
            "measurement_id": self.measurement_id,
            "source_revision": self.source_revision,
            "measurements": measurements,
            "claims": claims,
            "export": {
                "source_record_count": loaded["source_record_count"],
                "normalized_record_count": loaded["normalized_record_count"],
                "ignored_record_count": loaded["ignored_record_count"],
                "ignored_reasons": loaded["ignored_reasons"],
                "rejected_record_count": loaded["rejected_record_count"],
                "rejection_reasons": loaded["rejection_reasons"],
                "truncated_sources": loaded["truncated_sources"],
                "incomplete": bool(
                    loaded["rejected_record_count"] or loaded["truncated_sources"]
                ),
            },
            "artifacts": {
                "raw_json": context.artifact_path("raw/amaru-json.ndjson").relative_to(context.run_dir).as_posix(),
                "raw_otlp": context.artifact_path("raw/otlp.jsonl").relative_to(context.run_dir).as_posix(),
                "normalized": normalized_path.relative_to(context.run_dir).as_posix(),
                "correlations": context.artifact_path("correlations.json").relative_to(context.run_dir).as_posix(),
                "result": context.artifact_path("result.json").relative_to(context.run_dir).as_posix(),
            },
        }
        context.write_json("result.json", result)
        return result


def build_amaru_stock_factories(
    *,
    json_trace_paths: Iterable[str | Path],
    otlp_trace_paths: Iterable[str | Path],
    include_existing: bool = False,
    allow_missing_at_start: bool = False,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    source_revision: str | None = AMARU_SOURCE_REVISION,
) -> dict[str, Any]:
    """Bind trusted deployment-owned inputs to each stock collector factory."""
    json_paths = tuple(Path(path) for path in json_trace_paths)
    otlp_paths = tuple(Path(path) for path in otlp_trace_paths)

    def factory(entry: dict[str, Any]) -> AmaruStockCollector:
        return AmaruStockCollector(
            entry,
            json_trace_paths=json_paths,
            otlp_trace_paths=otlp_paths,
            include_existing=include_existing,
            allow_missing_at_start=allow_missing_at_start,
            max_source_bytes=max_source_bytes,
            source_revision=source_revision,
        )

    return {measurement_id: factory for measurement_id in STOCK_MEASUREMENT_IDS}


class AmaruHeaderValidationCollector:
    """Report per-header validation verdict counts and reasons from stock traces.

    Fail-closed: when no header-validation verdict is present in the telemetry
    window, the measurement is ``unavailable`` with a reason, never a zero pass.
    """

    def __init__(
        self,
        entry: dict[str, Any],
        *,
        trace_paths: Iterable[str | Path],
        allow_missing_at_start: bool = False,
        max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
        source_revision: str | None = AMARU_SOURCE_REVISION,
    ) -> None:
        self.entry = entry
        self.measurement_id = entry["id"]
        self.trace_paths = [Path(path) for path in trace_paths]
        self.allow_missing_at_start = allow_missing_at_start
        self.max_source_bytes = max_source_bytes
        self.source_revision = source_revision
        self._offsets: dict[Path, int] = {}

    def prepare(self, context) -> None:
        for path in self.trace_paths:
            if not path.is_file():
                if not self.allow_missing_at_start:
                    raise FileNotFoundError(f"Amaru telemetry source is unavailable: {path}")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
        context.collector_dir.mkdir(parents=True, exist_ok=True)

    def start(self, context) -> None:
        self._offsets = {path: path.stat().st_size for path in self.trace_paths}

    def on_marker(self, marker, context) -> None:
        return None

    def stop(self, context) -> None:
        return None

    def finalize(self, context) -> dict[str, Any]:
        from scripts.header_validation_parse import parse_amaru_header_events, verdict_by_hash

        lines: list[str] = []
        for path in self.trace_paths:
            body, _truncated = _bounded_read(
                path, start=self._offsets.get(path, 0), max_bytes=self.max_source_bytes
            )
            lines.extend(body.decode("utf-8", "replace").splitlines())
        events = parse_amaru_header_events(lines)
        latest = list(verdict_by_hash(events).values())
        if not latest:
            result = {
                "schema_version": "v1",
                "measurement_id": self.measurement_id,
                "source_revision": self.source_revision,
                "measurements": {
                    "header_validation": {
                        "status": "unavailable",
                        "reason": "no header-validation verdict observed in telemetry",
                        "unit": "count",
                        "value": None,
                    }
                },
                "verdicts": {"accepted": 0, "rejected": 0},
                "by_reason": {},
                "events": [],
            }
        else:
            verdicts = Counter(event["verdict"] for event in latest)
            by_reason = Counter(
                event["reason"] for event in latest
                if event["verdict"] == "rejected" and event["reason"]
            )
            counts = {"accepted": verdicts.get("accepted", 0), "rejected": verdicts.get("rejected", 0)}
            result = {
                "schema_version": "v1",
                "measurement_id": self.measurement_id,
                "source_revision": self.source_revision,
                "measurements": {
                    "header_validation": {"status": "available", "unit": "count", "value": counts}
                },
                "verdicts": counts,
                "by_reason": dict(by_reason),
                "events": latest[:500],
            }
        context.write_json("result.json", result)
        return result
