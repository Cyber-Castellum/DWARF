#!/usr/bin/env python3

import shutil
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_common import (  # noqa: E402
    PROFILE_A_CONFIG,
    derive_chainsync_point,
    derive_range,
    point_slot,
    point_span,
    query_tip,
    run_blockfetch,
    run_chainsync_fetch,
    target_port,
)
from runtime_telemetry import emit_runtime_metric, emit_target_event  # noqa: E402


NODES = ("node1", "node2", "node3")


def _emit_fetch_event(event: str, *, protocol: str, node: str, success: bool, extra=None, level=None) -> None:
    payload = {"protocol": protocol, "node": node, "success": success}
    if extra:
        payload.update(extra)
    emit_target_event(
        primitive="runtime_fetch_check",
        event=event,
        payload=payload,
        level=level or ("info" if success else "error"),
    )


def _emit_fetch_metric(name: str, *, value, node: str, protocol: str, extra=None) -> None:
    meta = {"node": node, "protocol": protocol}
    if extra:
        meta.update(extra)
    emit_runtime_metric(name, value=value, meta=meta)


def _emit_tip_state(*, protocol: str, node: str, tip: dict, port: int | None = None, mode: str | None = None) -> None:
    meta = {"protocol": protocol, "node": node}
    event_payload = {"protocol": protocol, "node": node, "tip_slot": tip["slot"], "tip_block": tip["block"], "sync_progress": tip["syncProgress"]}
    if port is not None:
        meta["port"] = port
        event_payload["port"] = port
    if mode is not None:
        meta["mode"] = mode
        event_payload["mode"] = mode
    emit_runtime_metric(f"{protocol}_tip_slot", value=tip["slot"], meta=meta)
    emit_runtime_metric(f"{protocol}_tip_block", value=tip["block"], meta=meta)
    emit_target_event(
        primitive="runtime_fetch_check",
        event=f"{protocol}_tip_observed",
        payload=event_payload,
    )


def _run_blockfetch_with_refresh(peer_port: int, from_point: str, to_point: str, *, max_attempts: int = 8):
    current_from, current_to = from_point, to_point
    last_result = None
    for attempt in range(max_attempts):
        if attempt > 0:
            current_from, current_to = derive_range(PROFILE_A_CONFIG)
        result = run_blockfetch(PROFILE_A_CONFIG, peer_port, current_from, current_to)
        last_result = result
        if result.returncode == 0:
            return result, current_from, current_to, attempt
    return last_result, current_from, current_to, max_attempts - 1


def _run_chainsync_with_refresh(peer_port: int, point: str, output_dir: Path, *, max_attempts: int = 8):
    current_point = point
    last_error = None
    for attempt in range(max_attempts):
        if attempt > 0:
            current_point = derive_chainsync_point(PROFILE_A_CONFIG)
        try:
            result = run_chainsync_fetch(PROFILE_A_CONFIG, peer_port, current_point, output_dir)
            return result, current_point, attempt
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise last_error


def run_chainsync_burst() -> int:
    point = derive_chainsync_point(PROFILE_A_CONFIG)
    point_slot_value = point_slot(point)
    peer = "node1"
    port = target_port(PROFILE_A_CONFIG, peer)
    tip = query_tip(PROFILE_A_CONFIG, peer)
    emit_target_event(
        primitive="runtime_fetch_check",
        event="chainsync_burst_started",
        payload={"point": point, "point_slot": point_slot_value, "peer": peer, "port": port, "tip_slot": tip["slot"]},
    )
    emit_runtime_metric("chainsync_point_length", value=len(point), meta={"mode": "burst"})
    if point_slot_value is not None:
        emit_runtime_metric("chainsync_point_slot", value=point_slot_value, meta={"mode": "burst", "node": peer})
        emit_runtime_metric("chainsync_point_tip_gap", value=max(0, tip["slot"] - point_slot_value), meta={"mode": "burst", "node": peer})
    _emit_tip_state(protocol="chainsync", node=peer, tip=tip, port=port, mode="burst")
    passes = 0
    total_elapsed_ms = 0
    with tempfile.TemporaryDirectory(prefix="dwarf-chainsync-burst-") as tmp:
        tmp_root = Path(tmp)
        for i in range(1, 6):
            out_dir = tmp_root / f"iter-{i}"
            out_dir.mkdir(parents=True, exist_ok=True)
            started_at = time.monotonic()
            result, point, refresh_count = _run_chainsync_with_refresh(port, point, out_dir)
            elapsed_ms = round((time.monotonic() - started_at) * 1000, 3)
            count = len(list(out_dir.glob("header.*.cbor")))
            success = result.returncode == 0 and count == 2
            _emit_fetch_event(
                "chainsync_burst_iteration",
                protocol="chainsync",
                node=peer,
                success=success,
                extra={"iteration": i, "port": port, "header_count": count, "exit_code": result.returncode, "point": point, "elapsed_ms": elapsed_ms, "refresh_count": refresh_count},
            )
            _emit_fetch_metric(
                "chainsync_burst_header_count",
                value=count,
                node=peer,
                protocol="chainsync",
                extra={"iteration": i},
            )
            _emit_fetch_metric(
                "chainsync_fetch_elapsed_ms",
                value=elapsed_ms,
                node=peer,
                protocol="chainsync",
                extra={"iteration": i},
            )
            if not success:
                return 1
            passes += 1
            total_elapsed_ms += elapsed_ms
    emit_runtime_metric("chainsync_burst_passes", value=passes, meta={"protocol": "chainsync", "node": peer})
    emit_runtime_metric("chainsync_burst_total_elapsed_ms", value=total_elapsed_ms, meta={"protocol": "chainsync", "node": peer})
    emit_target_event(
        primitive="runtime_fetch_check",
        event="chainsync_burst_completed",
        payload={"passes": passes, "point": point, "point_slot": point_slot_value, "peer": peer, "port": port, "total_elapsed_ms": total_elapsed_ms},
    )
    return 0 if passes == 5 else 1


def run_chainsync_multi_peer() -> int:
    point = derive_chainsync_point(PROFILE_A_CONFIG)
    point_slot_value = point_slot(point)
    emit_target_event(
        primitive="runtime_fetch_check",
        event="chainsync_multi_peer_started",
        payload={"point": point, "point_slot": point_slot_value, "nodes": list(NODES)},
    )
    emit_runtime_metric("chainsync_point_length", value=len(point), meta={"mode": "multi-peer"})
    if point_slot_value is not None:
        emit_runtime_metric("chainsync_point_slot", value=point_slot_value, meta={"mode": "multi-peer"})
    passes = 0
    total_elapsed_ms = 0
    with tempfile.TemporaryDirectory(prefix="dwarf-chainsync-multi-") as tmp:
        tmp_root = Path(tmp)
        for node in NODES:
            port = target_port(PROFILE_A_CONFIG, node)
            tip = query_tip(PROFILE_A_CONFIG, node)
            if point_slot_value is not None:
                _emit_fetch_metric("chainsync_point_tip_gap", value=max(0, tip["slot"] - point_slot_value), node=node, protocol="chainsync", extra={"port": port, "mode": "multi-peer"})
            _emit_tip_state(protocol="chainsync", node=node, tip=tip, port=port, mode="multi-peer")
            out_dir = tmp_root / node
            out_dir.mkdir(parents=True, exist_ok=True)
            started_at = time.monotonic()
            result, point, refresh_count = _run_chainsync_with_refresh(port, point, out_dir)
            elapsed_ms = round((time.monotonic() - started_at) * 1000, 3)
            count = len(list(out_dir.glob("header.*.cbor")))
            success = result.returncode == 0 and count == 2
            _emit_fetch_event(
                "chainsync_multi_peer_fetch",
                protocol="chainsync",
                node=node,
                success=success,
                extra={"port": port, "header_count": count, "exit_code": result.returncode, "point": point, "elapsed_ms": elapsed_ms, "tip_slot": tip["slot"], "refresh_count": refresh_count},
            )
            _emit_fetch_metric("chainsync_multi_peer_header_count", value=count, node=node, protocol="chainsync", extra={"port": port})
            _emit_fetch_metric("chainsync_fetch_elapsed_ms", value=elapsed_ms, node=node, protocol="chainsync", extra={"port": port, "mode": "multi-peer"})
            if not success:
                return 1
            passes += 1
            total_elapsed_ms += elapsed_ms
    emit_runtime_metric("chainsync_multi_peer_passes", value=passes, meta={"protocol": "chainsync"})
    emit_runtime_metric("chainsync_multi_peer_total_elapsed_ms", value=total_elapsed_ms, meta={"protocol": "chainsync"})
    emit_target_event(
        primitive="runtime_fetch_check",
        event="chainsync_multi_peer_completed",
        payload={"passes": passes, "point": point, "point_slot": point_slot_value, "total_elapsed_ms": total_elapsed_ms},
    )
    return 0 if passes == len(NODES) else 1


def run_blockfetch_burst() -> int:
    from_point, to_point = derive_range(PROFILE_A_CONFIG)
    from_slot = point_slot(from_point)
    to_slot = point_slot(to_point)
    slot_span = point_span(from_point, to_point)
    peer = "node1"
    port = target_port(PROFILE_A_CONFIG, peer)
    tip = query_tip(PROFILE_A_CONFIG, peer)
    emit_target_event(
        primitive="runtime_fetch_check",
        event="blockfetch_burst_started",
        payload={"from_point": from_point, "to_point": to_point, "from_slot": from_slot, "to_slot": to_slot, "slot_span": slot_span, "peer": peer, "port": port, "tip_slot": tip["slot"]},
    )
    emit_runtime_metric("blockfetch_range_point_count", value=2, meta={"mode": "burst"})
    if slot_span is not None:
        emit_runtime_metric("blockfetch_range_slot_span", value=slot_span, meta={"mode": "burst", "node": peer})
    if to_slot is not None:
        emit_runtime_metric("blockfetch_tip_gap", value=max(0, tip["slot"] - to_slot), meta={"mode": "burst", "node": peer})
    _emit_tip_state(protocol="blockfetch", node=peer, tip=tip, port=port, mode="burst")
    passes = 0
    total_elapsed_ms = 0
    for i in range(1, 6):
        started_at = time.monotonic()
        result, from_point, to_point, refresh_count = _run_blockfetch_with_refresh(port, from_point, to_point)
        elapsed_ms = round((time.monotonic() - started_at) * 1000, 3)
        success = result.returncode == 0
        _emit_fetch_event(
            "blockfetch_burst_iteration",
            protocol="blockfetch",
            node=peer,
            success=success,
            extra={"iteration": i, "port": port, "exit_code": result.returncode, "from_point": from_point, "to_point": to_point, "elapsed_ms": elapsed_ms, "refresh_count": refresh_count},
        )
        _emit_fetch_metric(
            "blockfetch_burst_iteration_success",
            value=1 if success else 0,
            node=peer,
            protocol="blockfetch",
            extra={"iteration": i},
        )
        _emit_fetch_metric(
            "blockfetch_fetch_elapsed_ms",
            value=elapsed_ms,
            node=peer,
            protocol="blockfetch",
            extra={"iteration": i},
        )
        if not success:
            return 1
        passes += 1
        total_elapsed_ms += elapsed_ms
    emit_runtime_metric("blockfetch_burst_passes", value=passes, meta={"protocol": "blockfetch", "node": peer})
    emit_runtime_metric("blockfetch_burst_total_elapsed_ms", value=total_elapsed_ms, meta={"protocol": "blockfetch", "node": peer})
    emit_target_event(
        primitive="runtime_fetch_check",
        event="blockfetch_burst_completed",
        payload={"passes": passes, "from_point": from_point, "to_point": to_point, "from_slot": from_slot, "to_slot": to_slot, "slot_span": slot_span, "peer": peer, "port": port, "total_elapsed_ms": total_elapsed_ms},
    )
    return 0 if passes == 5 else 1


def run_blockfetch_multi_peer() -> int:
    from_point, to_point = derive_range(PROFILE_A_CONFIG)
    from_slot = point_slot(from_point)
    to_slot = point_slot(to_point)
    slot_span = point_span(from_point, to_point)
    emit_target_event(
        primitive="runtime_fetch_check",
        event="blockfetch_multi_peer_started",
        payload={"from_point": from_point, "to_point": to_point, "from_slot": from_slot, "to_slot": to_slot, "slot_span": slot_span, "nodes": list(NODES)},
    )
    emit_runtime_metric("blockfetch_range_point_count", value=2, meta={"mode": "multi-peer"})
    if slot_span is not None:
        emit_runtime_metric("blockfetch_range_slot_span", value=slot_span, meta={"mode": "multi-peer"})
    passes = 0
    total_elapsed_ms = 0
    for node in NODES:
        port = target_port(PROFILE_A_CONFIG, node)
        tip = query_tip(PROFILE_A_CONFIG, node)
        if to_slot is not None:
            _emit_fetch_metric("blockfetch_tip_gap", value=max(0, tip["slot"] - to_slot), node=node, protocol="blockfetch", extra={"port": port, "mode": "multi-peer"})
        _emit_tip_state(protocol="blockfetch", node=node, tip=tip, port=port, mode="multi-peer")
        started_at = time.monotonic()
        result, from_point, to_point, refresh_count = _run_blockfetch_with_refresh(port, from_point, to_point)
        elapsed_ms = round((time.monotonic() - started_at) * 1000, 3)
        success = result.returncode == 0
        _emit_fetch_event(
            "blockfetch_multi_peer_fetch",
            protocol="blockfetch",
            node=node,
            success=success,
            extra={"port": port, "exit_code": result.returncode, "from_point": from_point, "to_point": to_point, "elapsed_ms": elapsed_ms, "tip_slot": tip["slot"], "refresh_count": refresh_count},
        )
        _emit_fetch_metric("blockfetch_multi_peer_success", value=1 if success else 0, node=node, protocol="blockfetch", extra={"port": port})
        _emit_fetch_metric("blockfetch_fetch_elapsed_ms", value=elapsed_ms, node=node, protocol="blockfetch", extra={"port": port, "mode": "multi-peer"})
        if not success:
            return 1
        passes += 1
        total_elapsed_ms += elapsed_ms
    emit_runtime_metric("blockfetch_multi_peer_passes", value=passes, meta={"protocol": "blockfetch"})
    emit_runtime_metric("blockfetch_multi_peer_total_elapsed_ms", value=total_elapsed_ms, meta={"protocol": "blockfetch"})
    emit_target_event(
        primitive="runtime_fetch_check",
        event="blockfetch_multi_peer_completed",
        payload={"passes": passes, "from_point": from_point, "to_point": to_point, "from_slot": from_slot, "to_slot": to_slot, "slot_span": slot_span, "total_elapsed_ms": total_elapsed_ms},
    )
    return 0 if passes == len(NODES) else 1


def main(argv) -> int:
    valid = {
        "chainsync-burst": run_chainsync_burst,
        "chainsync-multi-peer": run_chainsync_multi_peer,
        "blockfetch-burst": run_blockfetch_burst,
        "blockfetch-multi-peer": run_blockfetch_multi_peer,
    }
    if len(argv) != 2 or argv[1] not in valid:
        print(
            "usage: runtime_fetch_check.py {chainsync-burst|chainsync-multi-peer|blockfetch-burst|blockfetch-multi-peer}",
            file=sys.stderr,
        )
        return 2
    return valid[argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
