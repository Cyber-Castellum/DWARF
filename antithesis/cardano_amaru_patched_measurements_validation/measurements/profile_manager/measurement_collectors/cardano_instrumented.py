"""Collectors for DWARF's exact revision-locked Cardano measurement node."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from profile_manager.measurement_collectors import cardano_blockfetch_queue as queue
from profile_manager.measurement_collectors import cardano_txsubmission_residence as txsubmission
from profile_manager.measurement_correlation import correlate_measurement_events
from profile_manager.measurement_precision import precise_microseconds
from profile_manager.measurement_report import distribution_summary
from profile_manager.measurement_collectors.amaru_patched import (
    paired_overhead_calibration,
)


CARDANO_SOURCE_REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"
CARDANO_MEASUREMENT_PATCH_SHA256 = (
    "7a948067c6b957b277400675cf95e32864ed8d92cd130fbadb673775249b5cc1"
)
CARDANO_NANOSECOND_PATCH_SHA256 = (
    "1c52fa42b7fd9ee3403165a5269ae851665536d2b920ed8be6fdbe490d1ed93c"
)
CARDANO_HEADER_PATCH_SHA256 = "037a27a21b1956d019c73df09c5a60780f88a2402810c79fae81d537c515acc7"
CARDANO_BLOCKFETCH_PATCH_SHA256 = "a31c97952f26eae5f5320815b8e435f0b8a30bc0911ff1f56d4b6e9756bc6882"
CARDANO_TXSUBMISSION_PATCH_SHA256 = "95991a736b2e187ace2ec9873f0f10ffdc0ebd432269857dfbc2c74041371d0c"
CARDANO_HEADER_TOOLCHAIN = {"ghc": "9.6.7", "cabal": "3.16.0.0"}
HEADER_VALIDATION_MEASUREMENT_ID = "cardano-patched-header-validation"
CARDANO_MEASUREMENT_REVISIONS = {
    CARDANO_TXSUBMISSION_PATCH_SHA256: "nanoseconds-v5",
    CARDANO_HEADER_PATCH_SHA256: "nanoseconds-v3",
    CARDANO_BLOCKFETCH_PATCH_SHA256: "nanoseconds-v4",
    CARDANO_MEASUREMENT_PATCH_SHA256: "whole-microseconds-v1",
    CARDANO_NANOSECOND_PATCH_SHA256: "nanoseconds-v2",
}
PATCHED_MEASUREMENT_IDS = (
    "cardano-patched-protocol-decode",
    "cardano-patched-ledger-plutus-stages",
)
DEFAULT_MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_PATHS = 16
_OUTCOMES = {
    "accepted", "rejected", "completed", "decode_failure", "size_limit", "timeout"
}


def paired_cardano_overhead_calibration(
    stock: Mapping[str, Any],
    patched: Mapping[str, Any],
    *,
    minimum_samples: int = 30,
) -> dict[str, Any]:
    return paired_overhead_calibration(
        stock,
        patched,
        minimum_samples=minimum_samples,
        expected_implementation="cardano-node",
        expected_source_revision=CARDANO_SOURCE_REVISION,
        expected_patch_set_sha256=CARDANO_MEASUREMENT_PATCH_SHA256,
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


def _normalize_header(record: Mapping[str, Any]) -> dict[str, Any]:
    if (record.get("schema_version") != "v1"
            or record.get("target") != "cardano-node::measurement"
            or record.get("boundary") != "consensus-validate-header"):
        raise ValueError("invalid-header-validation-boundary")
    if record.get("outcome") == "unavailable" and record.get("error_kind") == "instrumentation-exception":
        expected = {"schema_version": "v1", "target": "cardano-node::measurement",
                    "event": "header_validation", "boundary": "consensus-validate-header",
                    "outcome": "unavailable", "error_kind": "instrumentation-exception"}
        if record != expected:
            raise ValueError("invalid-header-instrumentation-fallback")
        return {"kind": "header-validation", **expected}
    header_hash = record.get("header_hash")
    if not isinstance(header_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", header_hash):
        raise ValueError("invalid-header-validation-hash")
    outcome, error_kind, reason = record.get("outcome"), record.get("error_kind"), record.get("reason")
    allowed = {"accepted": {"none"}, "rejected": {"header-protocol", "header-envelope"},
               "unavailable": {"evaluation-exception"}}
    if (not isinstance(outcome, str) or not isinstance(error_kind, str)
            or error_kind not in allowed.get(outcome, set())
            or not isinstance(reason, str) or len(reason) > 1024
            or any(ord(c) < 32 or ord(c) > 126 for c in reason)
            or (outcome != "rejected" and reason) or (outcome == "rejected" and not reason)):
        raise ValueError("invalid-header-validation-outcome")
    nanos = record.get("elapsed_nanos")
    ended = record.get("ended_monotonic_ns")
    micros = record.get("duration_us")
    if (isinstance(nanos, bool) or not isinstance(nanos, int) or not 0 <= nanos < 2**64
            or isinstance(ended, bool) or not isinstance(ended, int) or not nanos <= ended < 2**64
            or isinstance(micros, bool) or not isinstance(micros, int) or micros != nanos // 1000):
        raise ValueError("invalid-header-validation-timing")
    return {"kind": "header-validation", "target": record["target"],
            "boundary": record["boundary"], "header_hash": header_hash,
            "outcome": outcome, "error_kind": error_kind, "reason": reason,
            "elapsed_nanos": nanos, "duration_us": nanos / 1000,
            "ended_monotonic_ns": ended}


def _normalize(record: Mapping[str, Any]) -> dict[str, Any] | None:
    if record.get("event") == "txsubmission_residence":
        return txsubmission.normalize(record)
    if record.get("event") == "blockfetch_handler_queue":
        return queue.normalize(record)
    if record.get("event") == "header_validation":
        return _normalize_header(record)
    if record.get("target") != "cardano-node::measurement":
        return None
    event_name = record.get("event")
    if event_name not in {"protocol_receive_decode", "ledger_stage"}:
        return None
    if (
        event_name == "protocol_receive_decode"
        and record.get("boundary") != "receive-plus-incremental-decode"
    ):
        return None
    elapsed_nanos, duration = precise_microseconds(
        record,
        nanos_field="elapsed_nanos",
        micros_field="duration_us",
    )
    ended = record.get("ended_monotonic_ns")
    outcome = record.get("outcome")
    if (
        duration is None
        or isinstance(ended, bool)
        or not isinstance(ended, int)
        or ended < 0
        or outcome not in _OUTCOMES
    ):
        raise ValueError("invalid-measurement-fields")
    event = {
        "kind": (
            "protocol-receive-decode"
            if event_name == "protocol_receive_decode"
            else "ledger-stage"
        ),
        "target": record["target"],
        "outcome": outcome,
        "duration_us": duration,
        "ended_monotonic_ns": ended,
    }
    if elapsed_nanos is not None:
        event["elapsed_nanos"] = elapsed_nanos
    if event_name == "protocol_receive_decode":
        event.update({
            "protocol": str(record.get("protocol") or "unknown"),
            "state": str(record.get("state") or "unknown"),
            "boundary": record["boundary"],
        })
    else:
        stage = record.get("stage")
        if stage not in {"block-application", "epoch-transition", "plutus-vm"}:
            raise ValueError("invalid-measurement-fields")
        event["stage"] = stage
    return event


def _load_sources(sources: Iterable[tuple[str, bytes, bool]]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    ignored = 0
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
            if not isinstance(record, dict):
                ignored += 1
                continue
            try:
                normalized = _normalize(record)
            except ValueError as error:
                rejected[str(error)] += 1
                continue
            if normalized is None:
                ignored += 1
            else:
                events.append(normalized)
    return {
        "schema_version": "v1",
        "source_revision": CARDANO_SOURCE_REVISION,
        "patch_set_sha256": CARDANO_MEASUREMENT_PATCH_SHA256,
        "source_record_count": source_count,
        "normalized_record_count": len(events),
        "ignored_record_count": ignored,
        "rejected_record_count": sum(rejected.values()),
        "rejection_reasons": dict(sorted(rejected.items())),
        "truncated_sources": sorted(truncated_sources),
        "events": events,
        "_raw_sources": raw_sources,
    }


def load_cardano_patched_telemetry(
    json_trace_paths: Iterable[str | Path], *, max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES
) -> dict[str, Any]:
    paths = [Path(path) for path in json_trace_paths]
    if not paths or len(paths) > MAX_SOURCE_PATHS:
        raise ValueError(f"between 1 and {MAX_SOURCE_PATHS} trace paths are required")
    if max_source_bytes <= 0 or max_source_bytes > 64 * 1024 * 1024:
        raise ValueError("max_source_bytes must be within 1..67108864")
    loaded = _load_sources(
        (str(path), *_bounded_read(path, max_bytes=max_source_bytes)) for path in paths
    )
    loaded.pop("_raw_sources", None)
    return loaded


def _distribution(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return distribution_summary(
        ({"value": event["duration_us"], "unit": "us"} for event in events),
        unit="us",
    )


def _by_outcome(events: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = sorted({event["outcome"] for event in events})
    return {
            outcome: _distribution(
                event for event in events if event["outcome"] == outcome
            )
            for outcome in outcomes
    }


def _measurements(
    measurement_id: str, events: list[dict[str, Any]]
) -> dict[str, Any]:
    if measurement_id == txsubmission.MEASUREMENT_ID:
        # This tap requires a complete multi-row join in finalize.
        return {}
    if measurement_id == "cardano-patched-protocol-decode":
        selected = [event for event in events if event["kind"] == "protocol-receive-decode"]
        return {
            "protocol_receive_decode": _distribution(selected),
            "protocol_receive_decode_by_outcome": _by_outcome(selected),
        }
    if measurement_id == HEADER_VALIDATION_MEASUREMENT_ID:
        selected = [e for e in events if e["kind"] == "header-validation"]
        verdicts = [e for e in selected if e["outcome"] in {"accepted", "rejected"}]
        unavailable = [e for e in selected if e["outcome"] == "unavailable"]
        return {"header_validation": _distribution(verdicts),
                "header_validation_by_outcome": _by_outcome(verdicts),
                "header_prerequisite_unavailable": _distribution(e for e in unavailable if "duration_us" in e)}
    result = {}
    for stage, key in (
        ("block-application", "block_application"),
        ("epoch-transition", "epoch_transition"),
        ("plutus-vm", "plutus_vm"),
    ):
        selected = [event for event in events if event.get("stage") == stage]
        result[key] = _distribution(selected)
        result[f"{key}_by_outcome"] = _by_outcome(selected)
    return result


def supports_cardano_header_validation(identity: Mapping[str, Any]) -> bool:
    """Recognize one unbuilt patch contract; this does not prove runtime readiness."""
    return (identity.get("implementation") == "cardano-node"
            and identity.get("mode") == "patched"
            and identity.get("source_revision") == CARDANO_SOURCE_REVISION
            and identity.get("patch_set_sha256") in {CARDANO_HEADER_PATCH_SHA256, CARDANO_BLOCKFETCH_PATCH_SHA256, CARDANO_TXSUBMISSION_PATCH_SHA256}
            and identity.get("toolchain") == CARDANO_HEADER_TOOLCHAIN)


def supports_cardano_blockfetch_queue(identity: Mapping[str, Any]) -> bool:
    """One exact unbuilt contract; no build, probe or runtime readiness claim."""
    return (identity.get("implementation") == "cardano-node"
            and identity.get("mode") == "patched"
            and identity.get("source_revision") == CARDANO_SOURCE_REVISION
            and identity.get("patch_set_sha256") in {CARDANO_BLOCKFETCH_PATCH_SHA256, CARDANO_TXSUBMISSION_PATCH_SHA256}
            and identity.get("toolchain") == CARDANO_HEADER_TOOLCHAIN)


def supports_cardano_txsubmission_residence(identity: Mapping[str, Any]) -> bool:
    """Exact unbuilt contract only; never a readiness or mempool admission claim."""
    return (identity.get("implementation") == "cardano-node"
            and identity.get("mode") == "patched"
            and identity.get("version") == "11.1.2"
            and identity.get("source_revision") == CARDANO_SOURCE_REVISION
            and identity.get("patch_set_sha256") == CARDANO_TXSUBMISSION_PATCH_SHA256
            and identity.get("toolchain") == CARDANO_HEADER_TOOLCHAIN)


def _validate_identity(identity: Mapping[str, Any]) -> str:
    if identity.get("implementation") != "cardano-node" or identity.get("mode") != "patched":
        raise ValueError("patched collector requires a Cardano-node target in patched mode")
    if identity.get("source_revision") != CARDANO_SOURCE_REVISION:
        raise ValueError("patched collector source revision does not match")
    measurement_revision = CARDANO_MEASUREMENT_REVISIONS.get(
        identity.get("patch_set_sha256")
    )
    if measurement_revision is None:
        raise ValueError("patched collector patch-set identity does not match")
    digest = identity.get("image_digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ValueError("patched collector requires an immutable image digest")
    return measurement_revision


class CardanoPatchedCollector:
    def __init__(
        self,
        entry: dict[str, Any],
        *,
        json_trace_paths: Iterable[str | Path],
        target_identity: Mapping[str, Any],
        include_existing: bool = False,
        max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    ) -> None:
        self.entry = entry
        self.measurement_id = entry["id"]
        self.json_paths = [Path(path) for path in json_trace_paths]
        self.target_identity = json.loads(json.dumps(target_identity))
        self.include_existing = include_existing
        self.max_source_bytes = max_source_bytes
        self._offsets: dict[Path, int] = {}

    def prepare(self, context) -> None:
        if self.measurement_id not in (*PATCHED_MEASUREMENT_IDS, HEADER_VALIDATION_MEASUREMENT_ID, queue.MEASUREMENT_ID, txsubmission.MEASUREMENT_ID):
            raise ValueError(f"unsupported patched measurement: {self.measurement_id}")
        self.measurement_revision = _validate_identity(self.target_identity)
        if self.measurement_id in {HEADER_VALIDATION_MEASUREMENT_ID, queue.MEASUREMENT_ID, txsubmission.MEASUREMENT_ID}:
            support = {queue.MEASUREMENT_ID: supports_cardano_blockfetch_queue,
                       txsubmission.MEASUREMENT_ID: supports_cardano_txsubmission_residence,
                       HEADER_VALIDATION_MEASUREMENT_ID: supports_cardano_header_validation}[self.measurement_id]
            if not support(self.target_identity):
                raise ValueError("measurement requires exact source, patch and toolchain")
            for field in ("image_digest", "executable_digest", "build_result_sha256", "runtime_probe_log_sha256"):
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(self.target_identity.get(field) or "")):
                    raise ValueError(f"measurement requires immutable {field} provenance")
        if self.max_source_bytes <= 0 or self.max_source_bytes > 64 * 1024 * 1024:
            raise ValueError("max_source_bytes must be within 1..67108864")
        if not self.json_paths or len(self.json_paths) > MAX_SOURCE_PATHS:
            raise ValueError(f"between 1 and {MAX_SOURCE_PATHS} trace paths are required")
        for path in self.json_paths:
            if not path.is_file():
                raise FileNotFoundError(f"Cardano patched telemetry source is unavailable: {path}")
        context.collector_dir.mkdir(parents=True, exist_ok=True)

    def start(self, context) -> None:
        self._offsets = {
            path: 0 if self.include_existing else path.stat().st_size
            for path in self.json_paths
        }

    def on_marker(self, marker, context) -> None:
        return None

    def stop(self, context) -> None:
        return None

    def finalize(self, context) -> dict[str, Any]:
        loaded = _load_sources(
            (
                str(path),
                *_bounded_read(
                    path,
                    max_bytes=self.max_source_bytes,
                    start=self._offsets.get(path, 0),
                ),
            )
            for path in self.json_paths
        )
        raw_sources = loaded.pop("_raw_sources")
        raw_path = context.artifact_path("raw/cardano-measurement.ndjson")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(b"\n".join(raw for _name, raw in raw_sources))
        normalized_path = context.artifact_path("normalized.ndjson")
        normalized_path.write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in loaded["events"]),
            encoding="utf-8",
        )
        correlations = correlate_measurement_events(loaded["events"])
        context.write_json("correlations.json", correlations)
        result = {
            "schema_version": "v1",
            "measurement_id": self.measurement_id,
            "source_revision": CARDANO_SOURCE_REVISION,
            "patch_set_sha256": self.target_identity["patch_set_sha256"],
            "measurement_revision": self.measurement_revision,
            "target": self.target_identity,
            "evidence_class": "patched-node",
            "performance_authority": "requires-paired-calibration",
            "measurements": _measurements(self.measurement_id, loaded["events"]),
            "export": {
                key: loaded[key]
                for key in (
                    "source_record_count", "normalized_record_count",
                    "ignored_record_count", "rejected_record_count",
                    "rejection_reasons", "truncated_sources",
                )
            },
            "artifacts": {
                "raw_json": raw_path.relative_to(context.run_dir).as_posix(),
                "normalized": normalized_path.relative_to(context.run_dir).as_posix(),
                "correlations": context.artifact_path("correlations.json").relative_to(context.run_dir).as_posix(),
                "result": context.artifact_path("result.json").relative_to(context.run_dir).as_posix(),
            },
        }
        result["export"]["incomplete"] = bool(
            loaded["rejected_record_count"] or loaded["truncated_sources"]
        )
        if self.measurement_id == HEADER_VALIDATION_MEASUREMENT_ID:
            headers = [e for e in loaded["events"] if e["kind"] == "header-validation"]
            observed = {"accepted", "rejected"} <= {e["outcome"] for e in headers}
            unavailable = sum(e["outcome"] == "unavailable" for e in headers)
            result["exercise"] = {
                "status": "pending-control-evidence" if observed and not unavailable and not result["export"]["incomplete"] else "incomplete",
                "required_outcomes_observed": observed,
                "unavailable_event_count": unavailable,
                "measurement_complete": False,
                "normal_control": "requires-retained-same-boundary-control",
                "correlation": ["run_id", "header_hash"],
                "reason": "Samples alone do not prove same-boundary controls, frozen node/scenario identity or runtime completeness.",
            }
        if self.measurement_id == queue.MEASUREMENT_ID:
            metrics, exercise, joined = queue.summarize(loaded["events"], incomplete=result["export"]["incomplete"])
            result["measurements"] = metrics
            result["exercise"] = exercise
            result["export"]["incomplete"] = result["export"]["incomplete"] or exercise["status"] == "incomplete"
            context.write_json("queue-correlations.json", joined)
            result["artifacts"]["queue_correlations"] = context.artifact_path("queue-correlations.json").relative_to(context.run_dir).as_posix()
        if self.measurement_id == txsubmission.MEASUREMENT_ID:
            metrics, exercise, joined = txsubmission.summarize(loaded["events"], incomplete=result["export"]["incomplete"])
            result["measurements"] = metrics
            result["exercise"] = exercise
            result["export"]["incomplete"] |= exercise["status"] == "incomplete"
            context.write_json("txsubmission-correlations.json", joined)
            result["artifacts"]["txsubmission_correlations"] = context.artifact_path("txsubmission-correlations.json").relative_to(context.run_dir).as_posix()
        context.write_json("result.json", result)
        return result


def build_cardano_patched_factories(
    *,
    json_trace_paths: Iterable[str | Path],
    target_identity: Mapping[str, Any],
    include_existing: bool = False,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> dict[str, Any]:
    paths = tuple(Path(path) for path in json_trace_paths)
    identity = json.loads(json.dumps(target_identity))

    def factory(entry: dict[str, Any]) -> CardanoPatchedCollector:
        return CardanoPatchedCollector(
            entry,
            json_trace_paths=paths,
            target_identity=identity,
            include_existing=include_existing,
            max_source_bytes=max_source_bytes,
        )

    ids = list(PATCHED_MEASUREMENT_IDS)
    if supports_cardano_header_validation(identity):
        ids.append(HEADER_VALIDATION_MEASUREMENT_ID)
    if supports_cardano_blockfetch_queue(identity):
        ids.append(queue.MEASUREMENT_ID)
    if supports_cardano_txsubmission_residence(identity):
        ids.append(txsubmission.MEASUREMENT_ID)
    return {measurement_id: factory for measurement_id in ids}
