"""Collectors and calibration gates for the exact DWARF-patched Amaru target."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from profile_manager.measurement_correlation import correlate_measurement_events
from profile_manager.measurement_precision import precise_microseconds
from profile_manager.measurement_report import distribution_summary


AMARU_SOURCE_REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"
AMARU_FIXED_SOURCE_REVISION = "d3a6dafcced78f5809a96619e883cf04911d2bdc"
AMARU_20260918_SOURCE_REVISION = "aedfe797a5b8ef00d8b362be40b47a52c3b4a379"
AMARU_MEASUREMENT_PATCH_SHA256 = (
    "f0e1aebca9adf2713d4d9f6f8ba33f20b0d04c3b35de6127d4a1e027a68b50af"
)
AMARU_NANOSECOND_PATCH_SHA256 = (
    "4c22d7b0c29a705d1471dcfb6ee09a306c936ce83fd47f808fb2bbb8c2c75de0"
)
AMARU_BLOCK_APPLICATION_NANOSECOND_PATCH_SHA256 = (
    "042f6b1840bc6a30e65d77ce702e1be9967b77564ecfb9c77c1e5c25520aad00"
)
# The nanoseconds-v3 instrumentation rebased onto 10.11.20260918; only the
# patch context changed, so the digest differs from the b159172 patch.
AMARU_20260918_NANOSECOND_V3_PATCH_SHA256 = (
    "47787f152bc7bdcd645d5d32125f6658847812c447fd375ede44d31b546620f6"
)
AMARU_20260918_NANOSECOND_V4_PATCH_SHA256 = (
    "46777913e69114e7a43419414cdfe36e21a526d7f344741df403e55c7b32ec9d"
)
HEADER_VALIDATION_MEASUREMENT_ID = "amaru-patched-header-validation"
AMARU_MEASUREMENT_REVISIONS = {
    AMARU_MEASUREMENT_PATCH_SHA256: "whole-microseconds-v1",
    AMARU_NANOSECOND_PATCH_SHA256: "nanoseconds-v2",
    AMARU_BLOCK_APPLICATION_NANOSECOND_PATCH_SHA256: "nanoseconds-v3",
    AMARU_20260918_NANOSECOND_V3_PATCH_SHA256: "nanoseconds-v3",
    AMARU_20260918_NANOSECOND_V4_PATCH_SHA256: "nanoseconds-v4",
}
AMARU_MEASUREMENT_SOURCES = {
    AMARU_MEASUREMENT_PATCH_SHA256: frozenset({AMARU_SOURCE_REVISION}),
    AMARU_NANOSECOND_PATCH_SHA256: frozenset(
        {AMARU_SOURCE_REVISION, AMARU_FIXED_SOURCE_REVISION}
    ),
    AMARU_BLOCK_APPLICATION_NANOSECOND_PATCH_SHA256: frozenset(
        {AMARU_SOURCE_REVISION}
    ),
    AMARU_20260918_NANOSECOND_V3_PATCH_SHA256: frozenset(
        {AMARU_20260918_SOURCE_REVISION}
    ),
    AMARU_20260918_NANOSECOND_V4_PATCH_SHA256: frozenset(
        {AMARU_20260918_SOURCE_REVISION}
    ),
}
DEFAULT_MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_PATHS = 16
PATCHED_MEASUREMENT_IDS = (
    "amaru-patched-protocol-decode",
    "amaru-patched-blockfetch-queues",
    "amaru-patched-txsubmission-residence",
)

_EVENT_KINDS = {
    "header_validation": "header-validation",
    "protocol_ingress": "protocol-ingress",
    "protocol_decode": "protocol-decode",
    "handshake_negotiation": "handshake-negotiation",
    "blockfetch_handler": "blockfetch-handler",
    "blockfetch_block_decode": "blockfetch-block-decode",
    "blockfetch_queue_residence": "blockfetch-queue-residence",
    "txsubmission_depth": "txsubmission-depth",
    "txsubmission_residence": "txsubmission-residence",
    "txsubmission_blocking_residence": "txsubmission-blocking-residence",
}
_TIMING_FIELDS = (
    ("elapsed_nanos", "elapsed_micros"),
    ("decode_nanos", "decode_micros"),
    ("total_nanos", "total_micros"),
    ("handler_nanos", "handler_micros"),
    ("advertised_to_terminal_nanos", "advertised_to_terminal_micros"),
    ("advertised_to_body_nanos", "advertised_to_body_micros"),
    ("body_to_terminal_nanos", "body_to_terminal_micros"),
)



def _bounded_read(path: Path, *, max_bytes: int, start: int = 0) -> tuple[bytes, bool]:
    size = path.stat().st_size
    if start > size:
        start = 0
    available = max(0, size - start)
    with path.open("rb") as stream:
        stream.seek(start)
        data = stream.read(max_bytes)
    return data, available > len(data)


def _event_name(record: Mapping[str, Any]) -> str | None:
    span = record.get("span")
    if isinstance(span, dict) and isinstance(span.get("name"), str):
        return span["name"].lower()
    fields = record.get("fields")
    if isinstance(fields, dict) and isinstance(fields.get("message"), str):
        return fields["message"].split(".")[-1].lower()
    return None


def _is_measurement_record(record: Mapping[str, Any]) -> bool:
    fields = record.get("fields")
    target = record.get("target")
    message = fields.get("message") if isinstance(fields, dict) else None
    if target == "amaru::consensus::measurement":
        return isinstance(message, str) and message == "measurement.header_validation"
    return target == "amaru::protocols::measurement" or (
        target == "amaru::protocols"
        and isinstance(message, str)
        and message.startswith("measurement.")
    )


def _normalize_event(record: dict[str, Any]) -> dict[str, Any] | None:
    fields = record.get("fields")
    target = record.get("target")
    if not _is_measurement_record(record):
        return None
    name = (
        "header_validation"
        if target == "amaru::consensus::measurement"
        else _event_name(record)
    )
    if name not in _EVENT_KINDS or not isinstance(fields, dict):
        return None
    if name == "header_validation":
        if target != "amaru::consensus::measurement":
            raise ValueError("invalid-header-validation-target")
        if fields.get("boundary") != "consensus-validate-header":
            raise ValueError("invalid-header-validation-boundary")
        header_hash = fields.get("header_hash")
        if not isinstance(header_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", header_hash):
            raise ValueError("invalid-header-validation-hash")
        allowed_errors = {
            "accepted": {"none"},
            "rejected": {"praos-assertion"},
            "unavailable": {"nonces", "consensus-prerequisite"},
        }
        outcome, error_kind = fields.get("outcome"), fields.get("error_kind")
        if (
            not isinstance(outcome, str)
            or not isinstance(error_kind, str)
            or error_kind not in allowed_errors.get(outcome, set())
        ):
            raise ValueError("invalid-header-validation-outcome")
        if "elapsed_nanos" not in fields:
            raise ValueError("missing-header-validation-nanoseconds")
    normalized_fields = dict(fields)
    for nanos_field, micros_field in _TIMING_FIELDS:
        if nanos_field not in fields and micros_field not in fields:
            continue
        nanos, micros = precise_microseconds(
            fields,
            nanos_field=nanos_field,
            micros_field=micros_field,
        )
        if nanos is not None:
            normalized_fields[nanos_field] = nanos
        if micros is not None:
            normalized_fields[micros_field] = micros
    event: dict[str, Any] = {
        "kind": _EVENT_KINDS[name],
        "timestamp": record.get("timestamp"),
        "target": target,
        "name": name,
        "fields": normalized_fields,
    }
    for source, destination in (
        ("header_hash", "header_hash"),
        ("peer", "peer_id"),
        ("id", "tx_id"),
        ("request_id", "request_id"),
        ("message_type", "message_type"),
    ):
        if fields.get(source) is not None:
            event[destination] = str(fields[source])
    if record.get("id") is not None:
        event["span_id"] = str(record["id"])
    if record.get("parent_id") is not None:
        event["parent_span_id"] = str(record["parent_id"])
    return event


def _load_sources(
    sources: Iterable[tuple[str, bytes, bool]],
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    ignored_count = 0
    source_count = 0
    truncated_sources = []
    raw_sources = []
    for name, raw, truncated in sources:
        raw_sources.append((name, raw))
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
            try:
                normalized = _normalize_event(record) if isinstance(record, dict) else None
            except ValueError as error:
                rejected[str(error)] += 1
                continue
            if normalized is None:
                if isinstance(record, dict) and _is_measurement_record(record):
                    rejected["unsupported-event"] += 1
                else:
                    ignored_count += 1
            else:
                events.append(normalized)
    return {
        "schema_version": "v1",
        "source_revision": AMARU_SOURCE_REVISION,
        "patch_set_sha256": AMARU_MEASUREMENT_PATCH_SHA256,
        "source_record_count": source_count,
        "normalized_record_count": len(events),
        "ignored_record_count": ignored_count,
        "rejected_record_count": sum(rejected.values()),
        "rejection_reasons": dict(sorted(rejected.items())),
        "truncated_sources": sorted(truncated_sources),
        "events": events,
        "_raw_sources": raw_sources,
    }


def load_amaru_patched_telemetry(
    json_trace_paths: Iterable[str | Path],
    *,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> dict[str, Any]:
    paths = [Path(path) for path in json_trace_paths]
    if not paths or len(paths) > MAX_SOURCE_PATHS:
        raise ValueError(f"between 1 and {MAX_SOURCE_PATHS} trace paths are required")
    if max_source_bytes <= 0 or max_source_bytes > 64 * 1024 * 1024:
        raise ValueError("max_source_bytes must be within 1..67108864")
    sources = []
    for path in paths:
        raw, truncated = _bounded_read(path, max_bytes=max_source_bytes)
        sources.append((str(path), raw, truncated))
    result = _load_sources(sources)
    result.pop("_raw_sources", None)
    return result


def _samples(
    events: Iterable[dict[str, Any]], kind: str, field: str, *, unit: str = "us"
) -> dict[str, Any]:
    return distribution_summary(
        [
            {"value": event["fields"].get(field), "unit": unit}
            for event in events
            if event["kind"] == kind and event["fields"].get(field) is not None
        ],
        unit=unit,
    )


def _by_outcome(
    events: Iterable[dict[str, Any]],
    kind: str,
    field: str,
    outcome_field: str = "outcome",
    *,
    unit: str = "us",
) -> dict[str, dict[str, Any]]:
    matching = [
        event
        for event in events
        if event["kind"] == kind
        and event["fields"].get(field) is not None
        and isinstance(event["fields"].get(outcome_field), str)
        and event["fields"][outcome_field]
    ]
    outcomes = sorted({event["fields"][outcome_field] for event in matching})
    return {
        outcome: distribution_summary(
            [
                {"value": event["fields"][field], "unit": unit}
                for event in matching
                if event["fields"][outcome_field] == outcome
            ],
            unit=unit,
        )
        for outcome in outcomes
    }


def _build_measurements(
    measurement_id: str, events: list[dict[str, Any]]
) -> dict[str, Any]:
    if measurement_id == HEADER_VALIDATION_MEASUREMENT_ID:
        verdicts = [
            event for event in events
            if event["kind"] == "header-validation"
            and event["fields"]["outcome"] in {"accepted", "rejected"}
        ]
        unavailable = [
            event for event in events
            if event["kind"] == "header-validation"
            and event["fields"]["outcome"] == "unavailable"
        ]
        return {
            "header_validation": _samples(verdicts, "header-validation", "elapsed_micros"),
            "header_validation_by_outcome": _by_outcome(
                verdicts, "header-validation", "elapsed_micros"
            ),
            "unavailable_prerequisites": _samples(
                unavailable, "header-validation", "elapsed_micros"
            ),
            "unavailable_by_error_kind": _by_outcome(
                unavailable, "header-validation", "elapsed_micros", "error_kind"
            ),
        }
    if measurement_id == "amaru-patched-protocol-decode":
        handshake_events = [
            event
            for event in events
            if event["fields"].get("protocol_id") == "0"
            and event["fields"].get("role") == "responder"
        ]
        return {
            "protocol_decode": _samples(events, "protocol-decode", "decode_micros"),
            "protocol_decode_by_decode_outcome": _by_outcome(
                events, "protocol-decode", "decode_micros", "decode_outcome"
            ),
            "protocol_total": _samples(events, "protocol-decode", "total_micros"),
            "protocol_total_by_state_outcome": _by_outcome(
                events, "protocol-decode", "total_micros", "state_outcome"
            ),
            "handshake_ingress": _samples(
                handshake_events, "protocol-ingress", "elapsed_micros"
            ),
            "handshake_ingress_by_outcome": _by_outcome(
                handshake_events, "protocol-ingress", "elapsed_micros"
            ),
            "handshake_decode": _samples(
                handshake_events, "protocol-decode", "decode_micros"
            ),
            "handshake_decode_by_decode_outcome": _by_outcome(
                handshake_events,
                "protocol-decode",
                "decode_micros",
                "decode_outcome",
            ),
            "handshake_state_by_outcome": _by_outcome(
                handshake_events,
                "protocol-decode",
                "total_micros",
                "state_outcome",
            ),
            "handshake_negotiation": _samples(
                handshake_events, "handshake-negotiation", "elapsed_micros"
            ),
            "handshake_negotiation_by_outcome": _by_outcome(
                handshake_events, "handshake-negotiation", "elapsed_micros"
            ),
        }
    if measurement_id == "amaru-patched-blockfetch-queues":
        return {
            "handler": _samples(events, "blockfetch-handler", "handler_micros"),
            "handler_by_outcome": _by_outcome(
                events, "blockfetch-handler", "handler_micros"
            ),
            "block_decode": _samples(
                events, "blockfetch-block-decode", "elapsed_micros"
            ),
            "block_decode_by_outcome": _by_outcome(
                events, "blockfetch-block-decode", "elapsed_micros"
            ),
            "queue_residence": _samples(
                events, "blockfetch-queue-residence", "elapsed_micros"
            ),
            "queue_residence_by_outcome": _by_outcome(
                events, "blockfetch-queue-residence", "elapsed_micros"
            ),
            "inflight_depth": _samples(
                events, "blockfetch-handler", "inflight_depth", unit="requests"
            ),
            "pending_depth": _samples(
                events, "blockfetch-handler", "pending_depth", unit="requests"
            ),
        }
    if measurement_id == "amaru-patched-txsubmission-residence":
        return {
            "advertised_to_terminal": _samples(
                events, "txsubmission-residence", "advertised_to_terminal_micros"
            ),
            "advertised_to_terminal_by_outcome": _by_outcome(
                events, "txsubmission-residence", "advertised_to_terminal_micros"
            ),
            "advertised_to_body": _samples(
                events, "txsubmission-residence", "advertised_to_body_micros"
            ),
            "body_to_terminal": _samples(
                events, "txsubmission-residence", "body_to_terminal_micros"
            ),
            "pending_depth": _samples(
                events, "txsubmission-depth", "pending_depth", unit="transactions"
            ),
            "inflight_depth": _samples(
                events, "txsubmission-depth", "inflight_depth", unit="transactions"
            ),
            "unacked_depth": _samples(
                events, "txsubmission-depth", "unacked_depth", unit="transactions"
            ),
            "blocking_residence": _samples(
                events, "txsubmission-blocking-residence", "elapsed_micros"
            ),
            "blocking_residence_by_outcome": _by_outcome(
                events, "txsubmission-blocking-residence", "elapsed_micros"
            ),
        }
    return {}


def resolve_amaru_measurement_revision(
    source_revision: Any, patch_set_sha256: Any
) -> str | None:
    measurement_revision = AMARU_MEASUREMENT_REVISIONS.get(patch_set_sha256)
    allowed_sources = AMARU_MEASUREMENT_SOURCES.get(patch_set_sha256, frozenset())
    if measurement_revision is None or source_revision not in allowed_sources:
        return None
    return measurement_revision


def supports_amaru_header_validation(identity: Mapping[str, Any]) -> bool:
    """Recognize the exact patch contract, without claiming its target is built."""
    return (
        identity.get("implementation") == "amaru"
        and identity.get("mode") == "patched"
        and identity.get("source_revision") == AMARU_20260918_SOURCE_REVISION
        and identity.get("patch_set_sha256") == AMARU_20260918_NANOSECOND_V4_PATCH_SHA256
    )


def _validate_identity(identity: Mapping[str, Any]) -> str:
    if identity.get("implementation") != "amaru" or identity.get("mode") != "patched":
        raise ValueError("patched collector requires an Amaru target in patched mode")
    candidate = identity.get("experimental_instrumentation")
    if candidate is not None and identity.get("identity_kind") == "operator-local-config":
        from profile_manager.experimental_instrumentation import validate_candidate
        validate_candidate(candidate, identity)
        if identity.get("patch_set_sha256") != candidate["patch_set_sha256"]:
            raise ValueError("experimental patch-set identity does not match target")
        return candidate["measurement_revision"]
    measurement_revision = resolve_amaru_measurement_revision(
        identity.get("source_revision"), identity.get("patch_set_sha256")
    )
    if measurement_revision is None:
        raise ValueError(
            "patched collector source revision and patch-set identity do not match an audited target"
        )
    digest = identity.get("image_digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ValueError("patched collector requires an immutable image digest")
    return measurement_revision


class AmaruPatchedCollector:
    """Collect native patched-node events from runtime-owned trace paths."""

    def __init__(
        self,
        entry: dict[str, Any],
        *,
        json_trace_paths: Iterable[str | Path],
        target_identity: Mapping[str, Any],
        include_existing: bool = False,
        allow_missing_at_start: bool = False,
        max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    ) -> None:
        self.entry = entry
        self.measurement_id = entry["id"]
        self.json_paths = [Path(path) for path in json_trace_paths]
        self.target_identity = json.loads(json.dumps(target_identity))
        self.include_existing = include_existing
        self.allow_missing_at_start = allow_missing_at_start
        self.max_source_bytes = max_source_bytes
        self._offsets: dict[Path, int] = {}
        self._markers: list[dict[str, Any]] = []

    def prepare(self, context) -> None:
        if self.measurement_id not in (*PATCHED_MEASUREMENT_IDS, HEADER_VALIDATION_MEASUREMENT_ID):
            raise ValueError(f"unsupported patched measurement: {self.measurement_id}")
        if not self.json_paths or len(self.json_paths) > MAX_SOURCE_PATHS:
            raise ValueError(f"between 1 and {MAX_SOURCE_PATHS} trace paths are required")
        if self.max_source_bytes <= 0 or self.max_source_bytes > 64 * 1024 * 1024:
            raise ValueError("max_source_bytes must be within 1..67108864")
        self.measurement_revision = _validate_identity(self.target_identity)
        if self.measurement_id == HEADER_VALIDATION_MEASUREMENT_ID:
            if not supports_amaru_header_validation(self.target_identity):
                raise ValueError("header validation requires the exact nanoseconds-v4 source and patch set")
            for field in ("image_digest", "executable_digest", "build_result_sha256", "runtime_probe_log_sha256"):
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(self.target_identity.get(field) or "")):
                    raise ValueError(f"header validation requires immutable {field} provenance")
        for path in self.json_paths:
            if not path.is_file() and not self.allow_missing_at_start:
                raise FileNotFoundError(f"Amaru patched telemetry source is unavailable: {path}")
        context.collector_dir.mkdir(parents=True, exist_ok=True)

    def start(self, context) -> None:
        self._offsets = {
            path: (
                0
                if self.include_existing or not path.exists()
                else path.stat().st_size
            )
            for path in self.json_paths
        }

    def on_marker(self, marker, context) -> None:
        self._markers.append(dict(marker))

    def stop(self, context) -> None:
        return None

    def finalize(self, context) -> dict[str, Any]:
        sources = []
        for path in self.json_paths:
            raw, truncated = _bounded_read(
                path, max_bytes=self.max_source_bytes, start=self._offsets.get(path, 0)
            )
            sources.append((str(path), raw, truncated))
        loaded = _load_sources(sources)
        raw_sources = loaded.pop("_raw_sources")
        raw_path = context.artifact_path("raw/amaru-json.ndjson")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(b"\n".join(raw for _name, raw in raw_sources))
        normalized_path = context.artifact_path("normalized.ndjson")
        normalized_path.write_text(
            "".join(
                json.dumps(event, sort_keys=True) + "\n"
                for event in loaded["events"]
            ),
            encoding="utf-8",
        )
        correlations = correlate_measurement_events(loaded["events"])
        context.write_json("correlations.json", correlations)
        result = {
            "schema_version": "v1",
            "measurement_id": self.measurement_id,
            "source_revision": self.target_identity["source_revision"],
            "patch_set_sha256": self.target_identity["patch_set_sha256"],
            "measurement_revision": self.measurement_revision,
            "target": self.target_identity,
            "evidence_class": "patched-node",
            "performance_authority": "requires-paired-calibration",
            "measurements": _build_measurements(
                self.measurement_id, loaded["events"]
            ),
            "export": {
                "source_record_count": loaded["source_record_count"],
                "normalized_record_count": loaded["normalized_record_count"],
                "ignored_record_count": loaded["ignored_record_count"],
                "rejected_record_count": loaded["rejected_record_count"],
                "rejection_reasons": loaded["rejection_reasons"],
                "truncated_sources": loaded["truncated_sources"],
                "incomplete": bool(
                    loaded["rejected_record_count"] or loaded["truncated_sources"]
                ),
            },
            "artifacts": {
                "raw_json": raw_path.relative_to(context.run_dir).as_posix(),
                "normalized": normalized_path.relative_to(context.run_dir).as_posix(),
                "correlations": context.artifact_path("correlations.json")
                .relative_to(context.run_dir)
                .as_posix(),
                "result": context.artifact_path("result.json")
                .relative_to(context.run_dir)
                .as_posix(),
            },
        }
        if self.measurement_id == HEADER_VALIDATION_MEASUREMENT_ID:
            outcomes = {
                event["fields"]["outcome"] for event in loaded["events"]
                if event["kind"] == "header-validation"
            }
            observed = {"accepted", "rejected"} <= outcomes
            result["exercise"] = {
                "status": (
                    "pending-control-evidence"
                    if observed and not result["export"]["incomplete"]
                    else "incomplete"
                ),
                "required_outcomes_observed": observed,
                "measurement_complete": False,
                "normal_control": "requires-retained-same-boundary-control",
                "correlation": ["run_id", "header_hash"],
                "reason": "Timing samples do not prove normal controls, frozen node/scenario identity or campaign completeness.",
            }
        context.write_json("result.json", result)
        return result


def build_amaru_patched_factories(
    *,
    json_trace_paths: Iterable[str | Path],
    target_identity: Mapping[str, Any],
    include_existing: bool = False,
    allow_missing_at_start: bool = False,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> dict[str, Any]:
    paths = tuple(Path(path) for path in json_trace_paths)
    identity = json.loads(json.dumps(target_identity))

    def factory(entry: dict[str, Any]) -> AmaruPatchedCollector:
        return AmaruPatchedCollector(
            entry,
            json_trace_paths=paths,
            target_identity=identity,
            include_existing=include_existing,
            allow_missing_at_start=allow_missing_at_start,
            max_source_bytes=max_source_bytes,
        )

    ids = list(PATCHED_MEASUREMENT_IDS)
    if supports_amaru_header_validation(identity):
        ids.append(HEADER_VALIDATION_MEASUREMENT_ID)
    return {measurement_id: factory for measurement_id in ids}


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def paired_overhead_calibration(
    stock: Mapping[str, Any],
    patched: Mapping[str, Any],
    *,
    minimum_samples: int = 30,
    maximum_absolute_percent_delta: float = 5.0,
    expected_implementation: str = "amaru",
    expected_source_revision: str = AMARU_SOURCE_REVISION,
    expected_patch_set_sha256: str = AMARU_MEASUREMENT_PATCH_SHA256,
) -> dict[str, Any]:
    """Compare common real-node metrics only after strict paired-run parity gates."""
    overhead_limit = _finite_number(maximum_absolute_percent_delta)
    if overhead_limit is None or overhead_limit < 0:
        raise ValueError("maximum_absolute_percent_delta must be finite and non-negative")
    statistic_scope = ("mean", "median", "p95", "p99")
    reasons: list[str] = []
    stock_target = stock.get("target") if isinstance(stock.get("target"), dict) else {}
    patched_target = patched.get("target") if isinstance(patched.get("target"), dict) else {}
    if stock_target.get("mode") != "stock" or patched_target.get("mode") != "patched":
        reasons.append("target modes must be stock and patched")
    identity_fields = ("implementation", "version", "source_revision")
    if any(stock_target.get(field) != patched_target.get(field) for field in identity_fields):
        reasons.append("stock and patched source identity must match")
    if patched_target.get("implementation") != expected_implementation:
        reasons.append("patched implementation is not the audited implementation")
    if patched_target.get("source_revision") != expected_source_revision:
        reasons.append("patched source revision is not the audited revision")
    if patched_target.get("patch_set_sha256") != expected_patch_set_sha256:
        reasons.append("patched patch-set identity is not the audited patch")
    if stock.get("workload_identity") != patched.get("workload_identity"):
        reasons.append("paired workload identity must match exactly")
    parity_fields = (
        ("runner", "runner identity"),
        ("timing_policy", "timing policy"),
        ("hardware", "hardware identity"),
    )
    for field, label in parity_fields:
        left = stock.get(field)
        right = patched.get(field)
        if (
            not isinstance(left, Mapping)
            or not left
            or not isinstance(right, Mapping)
            or not right
            or left != right
        ):
            reasons.append(f"{label} must match exactly")
    stock_attempts = stock.get("attempts")
    patched_attempts = patched.get("attempts")
    if (
        not isinstance(stock_attempts, Mapping)
        or not isinstance(patched_attempts, Mapping)
        or stock_attempts.get("total") != patched_attempts.get("total")
        or stock_attempts.get("outcomes") != patched_attempts.get("outcomes")
    ):
        reasons.append("terminal outcome counts must match exactly")

    stock_metrics = stock.get("measurements") if isinstance(stock.get("measurements"), dict) else {}
    patched_metrics = patched.get("measurements") if isinstance(patched.get("measurements"), dict) else {}
    common = sorted(set(stock_metrics) & set(patched_metrics))
    if not common:
        reasons.append("no common measurement exists for calibration")

    metrics: dict[str, Any] = {}
    for name in common:
        left = stock_metrics[name]
        right = patched_metrics[name]
        if not isinstance(left, dict) or not isinstance(right, dict):
            continue
        left_count = left.get("sample_count")
        right_count = right.get("sample_count")
        if (
            not isinstance(left_count, int)
            or not isinstance(right_count, int)
            or left_count < minimum_samples
            or right_count < minimum_samples
        ):
            reasons.append(
                f"minimum sample count {minimum_samples} not met for {name}"
            )
            continue
        if left.get("unit") != right.get("unit"):
            reasons.append(f"measurement unit mismatch for {name}")
            continue
        row: dict[str, Any] = {
            "unit": left.get("unit"),
            "stock_sample_count": left_count,
            "patched_sample_count": right_count,
        }
        valid = True
        for statistic in statistic_scope:
            baseline = _finite_number(left.get(statistic))
            instrumented = _finite_number(right.get(statistic))
            if baseline is None or instrumented is None or baseline == 0:
                valid = False
                reasons.append(f"finite non-zero {statistic} is required for {name}")
                break
            row[f"stock_{statistic}"] = baseline
            row[f"patched_{statistic}"] = instrumented
            row[f"{statistic}_percent_delta"] = round(
                ((instrumented - baseline) / baseline) * 100, 9
            )
        if valid:
            metrics[name] = row

    available = not reasons and bool(metrics)
    observed_deltas = [
        abs(float(row[f"{statistic}_percent_delta"]))
        for row in metrics.values()
        for statistic in statistic_scope
    ]
    maximum_observed_delta = (
        round(max(observed_deltas), 9) if observed_deltas else None
    )
    return {
        "schema_version": "v1",
        "status": "available" if available else "unavailable",
        "performance_authority": (
            "common-metric-calibrated" if available else "not-calibrated"
        ),
        "calibration_scope": sorted(metrics) if available else [],
        "claim_boundary": (
            "Only the listed common externally observed metrics are calibrated; "
            "patched internal measurements require a surface-specific paired workload."
        ),
        "minimum_samples": minimum_samples,
        "runner": stock.get("runner") if available else None,
        "timing_policy": stock.get("timing_policy") if available else None,
        "hardware": stock.get("hardware") if available else None,
        "attempts": stock.get("attempts") if available else None,
        "workload_identity": stock.get("workload_identity") if available else None,
        "metrics": metrics if available else {},
        "observer_overhead": {
            "acceptance_limit_absolute_percent_delta": overhead_limit,
            "gate_behavior": "informational-non-gating",
            "maximum_observed_absolute_percent_delta": (
                maximum_observed_delta if available else None
            ),
            "statistic_scope": list(statistic_scope),
            "within_limit": (
                maximum_observed_delta <= overhead_limit
                if available and maximum_observed_delta is not None
                else None
            ),
        },
        "reasons": reasons,
    }
