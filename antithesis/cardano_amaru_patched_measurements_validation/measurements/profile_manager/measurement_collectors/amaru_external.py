"""DWARF-side measurement taps for real Amaru workloads and lifecycle."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from profile_manager.measurement_report import backlog_summary, distribution_summary


def _read_ndjson(path: Path) -> tuple[list[dict[str, Any]], int]:
    records = []
    rejected = 0
    if not path.is_file():
        return records, rejected
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            rejected += 1
            continue
        if isinstance(value, dict):
            records.append(value)
        else:
            rejected += 1
    return records, rejected


def _iso_epoch(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _event_time(event: dict[str, Any]) -> float | None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    for value in (event.get("elapsed_seconds"), payload.get("elapsed_seconds")):
        parsed = _number(value)
        if parsed is not None:
            return parsed
    return _iso_epoch(event.get("ts"))


def _value_metric(value: float | int | None, unit: str, *, reason: str | None = None) -> dict[str, Any]:
    metric = {
        "status": "available" if value is not None else "unavailable",
        "unit": unit,
        "value": value,
    }
    if reason is not None:
        metric["reason"] = reason
    return metric


class WorkloadAccountingCollector:
    def __init__(
        self,
        entry: dict[str, Any],
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.entry = entry
        self._clock = monotonic_clock
        self._started_at = None
        self._stopped_at = None
        self._markers = []

    def prepare(self, context) -> None:
        context.collector_dir.mkdir(parents=True, exist_ok=True)

    def start(self, context) -> None:
        self._started_at = float(self._clock())

    def on_marker(self, marker, context) -> None:
        self._markers.append(dict(marker))

    def stop(self, context) -> None:
        self._stopped_at = float(self._clock())

    def _duration(self) -> float | None:
        starts = [
            _number(marker.get("elapsed_seconds"))
            for marker in self._markers
            if marker.get("phase_id") == "run" and marker.get("state") == "start"
        ]
        ends = [
            _number(marker.get("elapsed_seconds"))
            for marker in self._markers
            if marker.get("phase_id") == "run" and marker.get("state") == "end"
        ]
        starts = [value for value in starts if value is not None]
        ends = [value for value in ends if value is not None]
        if starts and ends and max(ends) > min(starts):
            return max(ends) - min(starts)
        if (
            self._started_at is not None
            and self._stopped_at is not None
            and self._stopped_at > self._started_at
        ):
            return self._stopped_at - self._started_at
        return None

    def finalize(self, context) -> dict[str, Any]:
        records, rejected_lines = _read_ndjson(context.run_dir / "log.ndjson")
        accounting = []
        fallback_used = False
        for record in records:
            phase = str(record.get("phase") or "")
            if "load" not in phase:
                continue
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            if record.get("event") == "workload_accounting":
                attempt_rows = []
                for attempt in payload.get("attempts") or []:
                    if not isinstance(attempt, dict):
                        continue
                    elapsed_nanos = _number(attempt.get("elapsed_nanos"))
                    elapsed_micros = (
                        elapsed_nanos / 1_000
                        if elapsed_nanos is not None
                        else _number(attempt.get("elapsed_micros"))
                    )
                    outcome = attempt.get("outcome")
                    if (
                        elapsed_micros is None
                        or elapsed_micros < 0
                        or not isinstance(outcome, str)
                        or not outcome
                    ):
                        continue
                    normalized_attempt = {
                        "outcome": outcome,
                        "elapsed_micros": elapsed_micros,
                    }
                    if elapsed_nanos is not None:
                        normalized_attempt["elapsed_nanos"] = int(elapsed_nanos)
                    if attempt.get("input_id") is not None:
                        normalized_attempt["input_id"] = str(attempt["input_id"])
                    if attempt.get("tx_id") is not None:
                        normalized_attempt["tx_id"] = str(attempt["tx_id"])
                    if (attempt_bytes := _number(attempt.get("bytes"))) is not None:
                        normalized_attempt["bytes"] = attempt_bytes
                    for stage in (
                        "submit_to_protocol_response",
                        "submit_to_mempool_visibility",
                        "submit_to_block_inclusion",
                        "submit_to_chain_adoption",
                    ):
                        stage_nanos = _number(attempt.get(f"{stage}_nanos"))
                        stage_micros = (
                            stage_nanos / 1_000
                            if stage_nanos is not None
                            else _number(attempt.get(f"{stage}_micros"))
                        )
                        if stage_nanos is not None:
                            normalized_attempt[f"{stage}_nanos"] = int(stage_nanos)
                        if stage_micros is not None and stage_micros >= 0:
                            normalized_attempt[f"{stage}_micros"] = stage_micros
                    attempt_rows.append(normalized_attempt)
                accounting.append(
                    {
                        "time": _event_time(record),
                        "attempted": _number(payload.get("attempted")) or 0.0,
                        "successful": _number(payload.get("successful")) or 0.0,
                        "rejected": _number(payload.get("rejected")) or 0.0,
                        "timed_out": _number(payload.get("timed_out")) or 0.0,
                        "bytes": _number(payload.get("bytes")) or 0.0,
                        "batches": _number(payload.get("batches")) or 0.0,
                        "backlog": _number(payload.get("backlog")),
                        "attempts": attempt_rows,
                    }
                )
            elif record.get("event") == "iteration":
                fallback_used = True
                outcome = str(
                    payload.get("outcome") or payload.get("status") or payload.get("result") or ""
                ).lower()
                successful = outcome in {
                    "ok", "accepted", "success", "pass", "clean_error", "clean-error"
                }
                size = _number(payload.get("input_bytes"))
                if size is None:
                    size = _number(payload.get("bytes")) or 0.0
                accounting.append(
                    {
                        "time": _event_time(record),
                        "attempted": 1.0,
                        "successful": 1.0 if successful else 0.0,
                        "rejected": 0.0 if successful else 1.0,
                        "timed_out": 0.0,
                        "bytes": size,
                        "batches": 1.0,
                        "backlog": None,
                        "attempts": (
                            [{
                                "input_id": str(payload.get("input_id") or payload.get("i") or ""),
                                "outcome": outcome or ("successful" if successful else "rejected"),
                                "elapsed_micros": elapsed,
                            }]
                            if (elapsed := _number(payload.get("elapsed_micros"))) is not None
                            and elapsed >= 0
                            else []
                        ),
                    }
                )
        duration = self._duration()
        if duration is None:
            times = [item["time"] for item in accounting if item["time"] is not None]
            if len(times) >= 2 and max(times) > min(times):
                duration = max(times) - min(times)
        attempted = sum(item["attempted"] for item in accounting)
        successful = sum(item["successful"] for item in accounting)
        rejected = sum(item["rejected"] for item in accounting)
        timed_out = sum(item["timed_out"] for item in accounting)
        unclassified = max(0.0, attempted - successful - rejected - timed_out)
        inconsistent = successful + rejected + timed_out > attempted
        if duration is not None and duration > 0:
            offered = {
                "status": "partial" if inconsistent else "available",
                "unit": "operations/s",
                "duration_seconds": duration,
                "offered_count": attempted,
                "accepted_count": successful,
                "rejected_count": rejected,
                "timed_out_count": timed_out,
                "unclassified_count": unclassified,
                "offered_rate": attempted / duration,
                "accepted_rate": successful / duration,
                "rejected_rate": rejected / duration,
                "timed_out_rate": timed_out / duration,
                "unclassified_rate": unclassified / duration,
                "rejection_reasons": (
                    {"workload-rejected": rejected} if rejected else {}
                ),
            }
            if inconsistent:
                offered["reason"] = "successful plus rejected exceeded attempted"
        else:
            offered = {
                "status": "unavailable",
                "unit": "operations/s",
                "duration_seconds": duration,
                "offered_count": attempted,
                "accepted_count": successful,
                "rejected_count": rejected,
                "timed_out_count": timed_out,
                "unclassified_count": unclassified,
                "reason": "positive run duration was unavailable",
            }
        byte_count = sum(item["bytes"] for item in accounting)
        batch_count = sum(item["batches"] for item in accounting)
        attempts = [attempt for item in accounting for attempt in item["attempts"]]
        attempt_outcomes = sorted({attempt["outcome"] for attempt in attempts})
        attempt_latency = {
            "all": distribution_summary(
                [
                    {"value": attempt["elapsed_micros"], "unit": "us"}
                    for attempt in attempts
                ],
                unit="us",
            ),
            "by_outcome": {
                outcome: distribution_summary(
                    [
                        {"value": attempt["elapsed_micros"], "unit": "us"}
                        for attempt in attempts
                        if attempt["outcome"] == outcome
                    ],
                    unit="us",
                )
                for outcome in attempt_outcomes
            },
        }
        stage_distributions = {}
        for stage in (
            "submit_to_protocol_response",
            "submit_to_mempool_visibility",
            "submit_to_block_inclusion",
            "submit_to_chain_adoption",
        ):
            values = [
                {"value": attempt[f"{stage}_micros"], "unit": "us"}
                for attempt in attempts
                if attempt.get(f"{stage}_micros") is not None
            ]
            distribution = distribution_summary(values, unit="us")
            if not values:
                distribution["reason"] = (
                    "no authoritative correlated boundary was retained for this stage"
                )
            stage_distributions[stage] = distribution
        origin = min(
            (item["time"] for item in accounting if item["time"] is not None),
            default=0,
        )
        backlog = backlog_summary(
            [
                {
                    "elapsed_seconds": item["time"] - origin,
                    "value": item["backlog"],
                    "unit": "operations",
                }
                for item in accounting
                if item["time"] is not None and item["backlog"] is not None
            ]
        )
        measurements = {
            "offered_operations": offered,
            "successful_operations": _value_metric(successful, "operations"),
            "rejected_operations": _value_metric(rejected, "operations"),
            "timed_out_operations": _value_metric(timed_out, "operations"),
            "offered_bytes": _value_metric(byte_count, "bytes"),
            "successful_operations_per_second": _value_metric(
                successful / duration if duration else None,
                "operations/s",
                reason=None if duration else "positive run duration was unavailable",
            ),
            "rejected_operations_per_second": _value_metric(
                rejected / duration if duration else None,
                "operations/s",
                reason=None if duration else "positive run duration was unavailable",
            ),
            "offered_bytes_per_second": _value_metric(
                byte_count / duration if duration else None,
                "bytes/s",
                reason=None if duration else "positive run duration was unavailable",
            ),
            "batches_per_second": _value_metric(
                batch_count / duration if duration else None,
                "batches/s",
                reason=None if duration else "positive run duration was unavailable",
            ),
            "backlog": backlog,
            "attempt_latency": attempt_latency,
            **stage_distributions,
        }
        result = {
            "schema_version": "v1",
            "measurement_id": self.entry["id"],
            "measurements": measurements,
            "attempts": attempts,
            "claims": {
                "successful_operations": "workload completed or cleanly handled",
                "attempt_latency": "includes every timed attempt regardless of outcome",
                "transaction_submission_acceptance": (
                    "unavailable" if fallback_used else "only when explicitly emitted by workload"
                ),
            },
            "source": {
                "records": len(accounting),
                "rejected_lines": rejected_lines,
                "iteration_fallback_used": fallback_used,
            },
        }
        context.write_json("result.json", result)
        return result


class RestartReadinessCollector:
    REQUIRED_GATES = {
        "listener_ready": "listener",
        "chain_progress_ready": "chain_progress",
        "peer_role_ready": "peer_role",
    }

    def __init__(self, entry: dict[str, Any], *, target_node: str) -> None:
        self.entry = entry
        self.target_node = target_node

    def prepare(self, context) -> None:
        context.collector_dir.mkdir(parents=True, exist_ok=True)

    def start(self, context) -> None:
        return None

    def on_marker(self, marker, context) -> None:
        return None

    def stop(self, context) -> None:
        return None

    def finalize(self, context) -> dict[str, Any]:
        records, rejected_lines = _read_ndjson(
            context.run_dir / "events" / "target-hooks.ndjson"
        )
        start = None
        gates = {}
        for record in records:
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            if payload.get("target_node") != self.target_node:
                continue
            elapsed = _event_time(record)
            if elapsed is None:
                continue
            if record.get("event") == "restart_started":
                start = elapsed
                gates = {}
            elif start is not None and record.get("event") in self.REQUIRED_GATES:
                gates[self.REQUIRED_GATES[record["event"]]] = elapsed
        missing = sorted(set(self.REQUIRED_GATES.values()) - set(gates))
        ready_at = max(gates.values()) if not missing and gates else None
        value = ready_at - start if start is not None and ready_at is not None else None
        readiness = {
            "status": "available" if value is not None and value >= 0 else "unavailable",
            "unit": "s",
            "value": value if value is not None and value >= 0 else None,
            "target_node": self.target_node,
            "restart_started_at": start,
            "ready_at": ready_at,
            "gates": gates,
            "missing_gates": missing,
        }
        if readiness["status"] == "unavailable":
            readiness["reason"] = "listener, live chain progress, and required peer-role gates are all required"
        result = {
            "schema_version": "v1",
            "measurement_id": self.entry["id"],
            "measurements": {"restart_readiness": readiness},
            "source": {"record_count": len(records), "rejected_lines": rejected_lines},
        }
        context.write_json("result.json", result)
        return result


class SyncSpeedCollector:
    def __init__(
        self,
        entry: dict[str, Any],
        *,
        tip_probe: Callable[[], dict[str, Any]],
        monotonic_clock: Callable[[], float] = time.monotonic,
        expected_start_height: int | None = None,
        expected_end_height: int | None = None,
        peer_policy: str,
        target_node: str | None = None,
    ) -> None:
        self.entry = entry
        self.tip_probe = tip_probe
        self.clock = monotonic_clock
        self.expected_start_height = expected_start_height
        self.expected_end_height = expected_end_height
        self.peer_policy = peer_policy
        self.target_node = target_node
        self.samples = []

    def prepare(self, context) -> None:
        context.collector_dir.mkdir(parents=True, exist_ok=True)

    def sample(self) -> dict[str, Any]:
        sampled_at = float(self.clock())
        tip = self.tip_probe()
        height = tip.get("block_height")
        if isinstance(height, bool) or not isinstance(height, int) or height < 0:
            raise ValueError("tip probe did not return a non-negative block_height")
        sample = {
            "monotonic_seconds": sampled_at,
            "block_height": height,
            "block_hash": tip.get("block_hash"),
        }
        self.samples.append(sample)
        return sample

    def start(self, context) -> None:
        self.sample()

    def on_marker(self, marker, context) -> None:
        return None

    def stop(self, context) -> None:
        self.sample()

    def _controlled_range(self, context):
        path = context.run_dir / "events" / "target-hooks.ndjson"
        records, _rejected = _read_ndjson(path)
        selected = []
        found = False
        for record in records:
            if record.get("event") not in {
                "sync_range_started", "sync_range_completed"
            }:
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            event_target = payload.get("target_node")
            if self.target_node is not None and event_target not in {None, self.target_node}:
                continue
            found = True
            if self.target_node is not None and event_target != self.target_node:
                continue
            tip = payload.get("tip")
            elapsed = record.get("elapsed_seconds", payload.get("elapsed_seconds"))
            if not isinstance(tip, dict) or isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
                continue
            height = tip.get("block_height")
            if isinstance(height, bool) or not isinstance(height, int) or height < 0:
                continue
            selected.append((record["event"], {
                "monotonic_seconds": float(elapsed),
                "block_height": height,
                "block_hash": tip.get("block_hash", tip.get("hash")),
                "peer_policy": payload.get("peer_policy"),
            }))
        starts = [sample for event, sample in selected if event == "sync_range_started"]
        ends = [sample for event, sample in selected if event == "sync_range_completed"]
        if not found:
            return None
        if len(starts) != 1 or len(ends) != 1:
            return None, None
        return starts[0], ends[0]

    def finalize(self, context) -> dict[str, Any]:
        controlled = self._controlled_range(context)
        if controlled is None:
            start = self.samples[0] if self.samples else None
            end = self.samples[-1] if len(self.samples) >= 2 else None
            peer_policy = self.peer_policy
            source = "collector-tip-observations"
        else:
            start, end = controlled
            if start is None or end is None:
                start = end = None
                peer_policy = self.peer_policy
            else:
                peer_policy = start.get("peer_policy")
                if peer_policy is None or peer_policy != end.get("peer_policy"):
                    start = end = None
                    peer_policy = self.peer_policy
            source = "controlled-sync-range-events"
        duration = (
            end["monotonic_seconds"] - start["monotonic_seconds"]
            if start is not None and end is not None
            else None
        )
        height_delta = (
            end["block_height"] - start["block_height"]
            if start is not None and end is not None
            else None
        )
        range_match = bool(
            start is not None
            and end is not None
            and (self.expected_start_height is None or start["block_height"] == self.expected_start_height)
            and (self.expected_end_height is None or end["block_height"] == self.expected_end_height)
        )
        valid = bool(duration is not None and duration > 0 and height_delta is not None and height_delta >= 0 and range_match)
        speed = {
            "status": "available" if valid else "unavailable",
            "unit": "blocks/s",
            "value": height_delta / duration if valid else None,
            "start_block_height": start["block_height"] if start else None,
            "end_block_height": end["block_height"] if end else None,
            "start_block_hash": start.get("block_hash") if start else None,
            "end_block_hash": end.get("block_hash") if end else None,
            "duration_seconds": duration,
            "controlled_range_match": range_match,
            "expected_start_height": self.expected_start_height,
            "expected_end_height": self.expected_end_height,
            "peer_policy": peer_policy,
            "clock": "monotonic",
            "source": source,
        }
        if not valid:
            speed["reason"] = "two monotonic tip observations and the configured controlled range are required"
        result = {
            "schema_version": "v1",
            "measurement_id": self.entry["id"],
            "measurements": {"sync_speed": speed},
            "samples": self.samples,
        }
        context.write_json("samples.json", self.samples)
        context.write_json("result.json", result)
        return result
