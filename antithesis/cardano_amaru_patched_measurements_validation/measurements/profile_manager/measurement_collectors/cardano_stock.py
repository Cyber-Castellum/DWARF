"""Bounded collectors for Cardano-node 11.1.2 machine-formatted traces."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from profile_manager.measurement_correlation import correlate_measurement_events
from profile_manager.measurement_report import distribution_summary


CARDANO_SOURCE_REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"
DEFAULT_MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_PATHS = 16
STOCK_MEASUREMENT_IDS = (
    "cardano-stock-chain-lifecycle",
    "cardano-stock-blockfetch",
    "cardano-stock-txsubmission-mempool",
    "cardano-stock-ledger-block-epoch",
    "cardano-stock-plutus-execution",
    "cardano-stock-network",
)


def _bounded_read(path: Path, *, start: int, max_bytes: int) -> tuple[bytes, bool]:
    size = path.stat().st_size
    if start > size:
        start = 0
    available = max(0, size - start)
    with path.open("rb") as stream:
        stream.seek(start)
        body = stream.read(max_bytes)
    return body, available > len(body)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _tx_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    for key in ("txid", "txId", "id", "hash"):
        if value.get(key) is not None:
            return str(value[key])
    return None


def _block_hash(data: dict[str, Any]) -> str | None:
    for key in ("tipBlockHash", "block", "hash"):
        value = data.get(key)
        if isinstance(value, str) and value not in {"Genesis", "genesis"}:
            return value
    for key in ("newtip", "tip"):
        value = data.get(key)
        if isinstance(value, dict):
            for nested in ("hash", "block"):
                if value.get(nested) is not None:
                    return str(value[nested])
    return None


def _normalize_record(record: dict[str, Any]) -> dict[str, Any] | None:
    namespace = record.get("ns")
    data = record.get("data")
    if not isinstance(namespace, str) or not isinstance(data, dict):
        return None
    kind = str(data.get("kind") or "")
    normalized_kind = None
    if kind == "DownloadedHeader":
        normalized_kind = "chainsync-downloaded-header"
    elif kind == "RolledBack":
        normalized_kind = "chainsync-rolled-back"
    elif kind == "ValidatedHeader":
        normalized_kind = "chainsync-validated-header"
    elif kind == "ChainSyncServer.Update":
        normalized_kind = "chainsync-server-update"
    elif kind == "AddedToCurrentChain":
        normalized_kind = "chain-adopted"
    elif kind == "TraceAddBlockEvent.SwitchedToAFork":
        normalized_kind = "chain-fork-switch"
    elif kind == "CompletedBlockFetch":
        normalized_kind = "blockfetch-completed"
    elif kind == "BlockFetchServer":
        normalized_kind = "blockfetch-served"
    elif kind in {"AddedFetchRequest", "SendFetchRequest", "StartedFetchBatch", "CompletedFetchBatch", "RejectedFetchBatch", "ClientTerminating"}:
        normalized_kind = "blockfetch-" + kind.lower()
    elif kind == "TraceMempoolAttemptingAdd":
        normalized_kind = "mempool-attempt"
    elif kind == "TraceMempoolAddedTx":
        normalized_kind = "mempool-accepted"
    elif kind == "TraceMempoolRejectedTx":
        normalized_kind = "mempool-rejected"
    elif kind in {"TraceMempoolRemoveTxs", "TraceMempoolManuallyRemovedTxs"}:
        normalized_kind = "mempool-removed"
    elif kind == "TraceMempoolSynced":
        normalized_kind = "mempool-synced"
    elif kind == "AddSample":
        normalized_kind = "keepalive-sample"
    elif kind == "TraceAdoptedBlock":
        normalized_kind = "forge-adopted-block"
    elif kind in {"TraceDidntAdoptBlock", "TraceForgedInvalidBlock"}:
        normalized_kind = "forge-rejected-block"
    elif kind == "LedgerMetrics":
        normalized_kind = "ledger-metrics"
    elif kind == "ResourceStats" or namespace == "Resources":
        normalized_kind = "resource-stats"
    elif namespace.startswith("TxSubmission."):
        normalized_kind = "txsubmission-event"
    if normalized_kind is None:
        return None

    event: dict[str, Any] = {
        "kind": normalized_kind,
        "timestamp": record.get("at"),
        "namespace": namespace,
        "node": record.get("host"),
        "thread": record.get("thread"),
        "fields": data,
    }
    transaction_id = _tx_id(data.get("tx"))
    if transaction_id is not None:
        event["tx_id"] = transaction_id
    block_hash = _block_hash(data)
    if block_hash is not None:
        event["block_hash"] = block_hash
    peer = data.get("peer") or data.get("address")
    if peer is not None:
        event["peer_id"] = str(peer)
    slot = data.get("slot")
    if slot is None and isinstance(data.get("newtip"), dict):
        slot = data["newtip"].get("slot")
    if slot is not None:
        event["slot"] = slot
    if normalized_kind == "blockfetch-completed":
        delay = _number(data.get("delay"))
        if delay is not None and delay >= 0:
            event["duration_micros"] = delay * 1_000_000
            event["timing_source"] = "node-emitted-blockfetch-delay"
        size = _number(data.get("size"))
        if size is not None:
            event["size_bytes"] = size
    elif normalized_kind == "keepalive-sample":
        rtt = _number(data.get("rtt"))
        if rtt is not None and rtt >= 0:
            event["duration_micros"] = rtt * 1_000_000
            event["timing_source"] = "node-emitted-keepalive-rtt"
    elif normalized_kind == "mempool-synced":
        duration = _number(data.get("enclosingTime"))
        if duration is not None and duration >= 0:
            event["duration_micros"] = duration * 1_000_000
            event["timing_source"] = "node-emitted-mempool-sync-duration"
    mempool = data.get("mempoolSize")
    if isinstance(mempool, dict):
        for source, destination in (("numTxs", "mempool_tx_count"), ("bytes", "mempool_bytes"), ("numBytes", "mempool_bytes")):
            number = _number(mempool.get(source))
            if number is not None:
                event[destination] = number
    return event


def _load_cardano_trace_bytes(
    sources: Iterable[tuple[str, bytes, bool]],
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    ignored = Counter()
    rejected = Counter()
    seen: set[str] = set()
    duplicate_count = 0
    source_count = 0
    truncated_sources: list[str] = []
    raw_sources: list[tuple[str, bytes]] = []
    for name, body, truncated in sources:
        raw_sources.append((name, body))
        if truncated:
            truncated_sources.append(name)
        for line in body.splitlines():
            if not line.strip():
                continue
            source_count += 1
            try:
                record = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                rejected["invalid-json"] += 1
                continue
            if not isinstance(record, dict):
                rejected["non-object"] += 1
                continue
            normalized = _normalize_record(record)
            if normalized is None:
                ignored["unsupported-event"] += 1
                continue
            fingerprint = json.dumps(record, sort_keys=True, separators=(",", ":"))
            if fingerprint in seen:
                duplicate_count += 1
                continue
            seen.add(fingerprint)
            events.append(normalized)
    return {
        "events": events,
        "source_record_count": source_count,
        "normalized_record_count": len(events),
        "ignored_record_count": sum(ignored.values()),
        "ignored_reasons": dict(ignored),
        "rejected_record_count": sum(rejected.values()),
        "rejection_reasons": dict(rejected),
        "duplicate_record_count": duplicate_count,
        "truncated_sources": truncated_sources,
        "_raw_sources": raw_sources,
    }


def _unavailable(unit: str, reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "unit": unit, "value": None, "reason": reason}


def _count(events: list[dict[str, Any]], kinds: set[str], unit: str = "events") -> dict[str, Any]:
    value = sum(event["kind"] in kinds for event in events)
    return {"status": "available", "unit": unit, "value": value}


def _distribution(events: list[dict[str, Any]], *, kinds: set[str], field: str, unit: str) -> dict[str, Any]:
    return distribution_summary(
        [
            {"value": event[field], "unit": unit}
            for event in events
            if event["kind"] in kinds and event.get(field) is not None
        ],
        unit=unit,
    )


def _build_measurements(measurement_id: str, events: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, str]]:
    if measurement_id == "cardano-stock-chain-lifecycle":
        return {
            "chain_events": _count(events, {"chainsync-downloaded-header", "chainsync-rolled-back", "chainsync-validated-header", "chainsync-server-update", "chain-adopted", "chain-fork-switch", "forge-adopted-block"}),
            "adopted_blocks": _count(events, {"chain-adopted", "forge-adopted-block"}, "blocks"),
            "rollbacks": _count(events, {"chainsync-rolled-back", "chain-fork-switch"}),
        }, {"chain_events": "stock node trace events, not inferred chain correctness"}
    if measurement_id == "cardano-stock-blockfetch":
        return {
            "blockfetch_duration": _distribution(events, kinds={"blockfetch-completed"}, field="duration_micros", unit="us"),
            "blockfetch_bytes": {
                "status": "available",
                "unit": "bytes",
                "value": sum(event.get("size_bytes", 0) for event in events if event["kind"] == "blockfetch-completed"),
            },
            "served_blocks": _count(events, {"blockfetch-served"}, "blocks"),
            "handler_queue_residence": _unavailable("us", "stock Cardano-node does not emit the audited internal handler queue boundary"),
        }, {"blockfetch_duration": "node-emitted CompletedBlockFetch delay"}
    if measurement_id == "cardano-stock-txsubmission-mempool":
        return {
            "mempool_accepted": _count(events, {"mempool-accepted"}, "transactions"),
            "mempool_rejected": _count(events, {"mempool-rejected"}, "transactions"),
            "mempool_tx_count": _distribution(events, kinds={"mempool-accepted", "mempool-rejected", "mempool-removed"}, field="mempool_tx_count", unit="transactions"),
            "mempool_bytes": _distribution(events, kinds={"mempool-accepted", "mempool-rejected", "mempool-removed"}, field="mempool_bytes", unit="bytes"),
            "mempool_sync_duration": _distribution(events, kinds={"mempool-synced"}, field="duration_micros", unit="us"),
            "txsubmission_residence": _unavailable("us", "stock Cardano-node does not emit a continuous request-to-mempool residence interval"),
        }, {"mempool_accepted": "stock node admission events", "mempool_rejected": "stock node rejection events"}
    if measurement_id == "cardano-stock-ledger-block-epoch":
        return {
            "adopted_blocks": _count(events, {"chain-adopted", "forge-adopted-block"}, "blocks"),
            "ledger_samples": _count(events, {"ledger-metrics"}),
            "epoch_transition_duration": _unavailable("us", "stock traces do not expose one authoritative epoch-transition interval"),
        }, {"adopted_blocks": "stock ChainDB and forge events"}
    if measurement_id == "cardano-stock-plutus-execution":
        return {
            "transaction_outcomes": _count(events, {"mempool-accepted", "mempool-rejected"}, "transactions"),
            "plutus_vm_duration": _unavailable("us", "stock Cardano-node does not expose authoritative UPLC wall-clock stage timing"),
        }, {"transaction_outcomes": "node outcomes only; workload execution budgets are correlated separately"}
    if measurement_id == "cardano-stock-network":
        return {
            "keepalive_rtt": _distribution(events, kinds={"keepalive-sample"}, field="duration_micros", unit="us"),
            "protocol_events": _count(events, {"chainsync-server-update", "blockfetch-completed", "blockfetch-served", "txsubmission-event", "keepalive-sample"}),
        }, {"protocol_events": "selected stock traces, not a complete protocol transcript"}
    return {}, {}


class CardanoStockCollector:
    def __init__(
        self,
        entry: dict[str, Any],
        *,
        trace_paths: Iterable[str | Path],
        include_existing: bool = False,
        allow_missing_at_start: bool = False,
        max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    ) -> None:
        self.entry = entry
        self.measurement_id = entry["id"]
        self.trace_paths = [Path(path) for path in trace_paths]
        if len(self.trace_paths) > MAX_SOURCE_PATHS:
            raise ValueError(f"at most {MAX_SOURCE_PATHS} telemetry source paths are allowed")
        if max_source_bytes <= 0 or max_source_bytes > 64 * 1024 * 1024:
            raise ValueError("max_source_bytes must be within 1..67108864")
        self.include_existing = include_existing
        self.allow_missing_at_start = allow_missing_at_start
        self.max_source_bytes = max_source_bytes
        self._offsets: dict[Path, int] = {}

    def prepare(self, context) -> None:
        for path in self.trace_paths:
            if not path.is_file():
                if not self.allow_missing_at_start:
                    raise FileNotFoundError(f"Cardano telemetry source is unavailable: {path}")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
        context.collector_dir.mkdir(parents=True, exist_ok=True)

    def start(self, context) -> None:
        self._offsets = {
            path: 0 if self.include_existing else path.stat().st_size
            for path in self.trace_paths
        }

    def on_marker(self, marker, context) -> None:
        return None

    def stop(self, context) -> None:
        return None

    def finalize(self, context) -> dict[str, Any]:
        sources = []
        for path in self.trace_paths:
            body, truncated = _bounded_read(
                path,
                start=self._offsets.get(path, 0),
                max_bytes=self.max_source_bytes,
            )
            sources.append((str(path), body, truncated))
        loaded = _load_cardano_trace_bytes(sources)
        raw_sources = loaded.pop("_raw_sources")
        raw_path = context.artifact_path("raw/cardano-node.ndjson")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(b"\n".join(body for _name, body in raw_sources))
        normalized_path = context.artifact_path("normalized.ndjson")
        normalized_path.write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in loaded["events"]),
            encoding="utf-8",
        )
        correlations = correlate_measurement_events(loaded["events"])
        context.write_json("correlations.json", correlations)
        measurements, claims = _build_measurements(self.measurement_id, loaded["events"])
        result = {
            "schema_version": "v1",
            "measurement_id": self.measurement_id,
            "source_revision": CARDANO_SOURCE_REVISION,
            "measurements": measurements,
            "claims": claims,
            "export": {
                key: loaded[key]
                for key in (
                    "source_record_count", "normalized_record_count", "ignored_record_count",
                    "ignored_reasons", "rejected_record_count", "rejection_reasons",
                    "duplicate_record_count", "truncated_sources",
                )
            },
            "artifacts": {
                "raw": raw_path.relative_to(context.run_dir).as_posix(),
                "normalized": normalized_path.relative_to(context.run_dir).as_posix(),
                "correlations": context.artifact_path("correlations.json").relative_to(context.run_dir).as_posix(),
                "result": context.artifact_path("result.json").relative_to(context.run_dir).as_posix(),
            },
        }
        result["export"]["incomplete"] = bool(
            result["export"]["rejected_record_count"] or result["export"]["truncated_sources"]
        )
        context.write_json("result.json", result)
        return result


def build_cardano_stock_factories(
    *,
    trace_paths: Iterable[str | Path],
    include_existing: bool = False,
    allow_missing_at_start: bool = False,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> dict[str, Any]:
    paths = tuple(Path(path) for path in trace_paths)

    def factory(entry: dict[str, Any]) -> CardanoStockCollector:
        return CardanoStockCollector(
            entry,
            trace_paths=paths,
            include_existing=include_existing,
            allow_missing_at_start=allow_missing_at_start,
            max_source_bytes=max_source_bytes,
        )

    return {measurement_id: factory for measurement_id in STOCK_MEASUREMENT_IDS}


class CardanoHeaderValidationCollector:
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
    ) -> None:
        self.entry = entry
        self.measurement_id = entry["id"]
        self.trace_paths = [Path(path) for path in trace_paths]
        self.allow_missing_at_start = allow_missing_at_start
        self.max_source_bytes = max_source_bytes
        self._offsets: dict[Path, int] = {}

    def prepare(self, context) -> None:
        for path in self.trace_paths:
            if not path.is_file():
                if not self.allow_missing_at_start:
                    raise FileNotFoundError(f"Cardano telemetry source is unavailable: {path}")
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
        from scripts.header_validation_parse import parse_cardano_header_events, verdict_by_hash

        lines: list[str] = []
        for path in self.trace_paths:
            body, _truncated = _bounded_read(
                path, start=self._offsets.get(path, 0), max_bytes=self.max_source_bytes
            )
            lines.extend(body.decode("utf-8", "replace").splitlines())
        events = parse_cardano_header_events(lines)
        latest = list(verdict_by_hash(events).values())
        if not latest:
            result = {
                "schema_version": "v1",
                "measurement_id": self.measurement_id,
                "source_revision": CARDANO_SOURCE_REVISION,
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
                "source_revision": CARDANO_SOURCE_REVISION,
                "measurements": {
                    "header_validation": {"status": "available", "unit": "count", "value": counts}
                },
                "verdicts": counts,
                "by_reason": dict(by_reason),
                "events": latest[:500],
            }
        context.write_json("result.json", result)
        return result
