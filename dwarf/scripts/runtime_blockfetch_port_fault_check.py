#!/usr/bin/env python3

import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_common import (  # noqa: E402
    PROFILE_A_CONFIG,
    derive_range,
    point_slot,
    point_span,
    run_blockfetch,
    target_port,
)
from runtime_telemetry import emit_runtime_metric, emit_target_event  # noqa: E402


NODE_NAMES = ("node1", "node2", "node3")
FETCH_TARGET_NODES = ("node2", "node3")
BLOCKED_NODE = "node1"


def _emit_range_metrics(label: str, *, from_point: str, to_point: str) -> None:
    from_slot = point_slot(from_point)
    to_slot = point_slot(to_point)
    slot_span = point_span(from_point, to_point)
    payload = {"from_point": from_point, "to_point": to_point, "slot_span": slot_span}
    if from_slot is not None:
        payload["from_slot"] = from_slot
        emit_runtime_metric(f"{label}_from_slot", value=from_slot, meta={"kind": "from_slot"})
    if to_slot is not None:
        payload["to_slot"] = to_slot
        emit_runtime_metric(f"{label}_to_slot", value=to_slot, meta={"kind": "to_slot"})
    emit_runtime_metric(f"{label}_slot_span", value=slot_span, meta={"kind": "slot_span"})
    emit_target_event(
        primitive="runtime_blockfetch_port_fault_check",
        event=f"{label}_range",
        payload=payload,
    )


def _emit_fault_result(label: str, *, node: str, port: int, outcome: str, elapsed_ms: float, exit_code: int) -> None:
    payload = {
        "node": node,
        "port": port,
        "outcome": outcome,
        "elapsed_ms": elapsed_ms,
        "exit_code": exit_code,
    }
    emit_target_event(
        primitive="runtime_blockfetch_port_fault_check",
        event=f"{label}_result",
        payload=payload,
        level="info" if outcome in {"timeout", "success"} else "error",
    )
    emit_runtime_metric(f"{label}_elapsed_ms", value=elapsed_ms, meta={"node": node, "outcome": outcome})
    emit_runtime_metric(f"{label}_exit_code", value=exit_code, meta={"node": node, "outcome": outcome})


def _run_blockfetch_with_elapsed(*, node: str, from_point: str, to_point: str, timeout_seconds: int) -> tuple[int, float]:
    port = target_port(PROFILE_A_CONFIG, node)
    started_at = time.monotonic()
    result = run_blockfetch(PROFILE_A_CONFIG, port, from_point, to_point, timeout_seconds=timeout_seconds)
    elapsed_ms = (time.monotonic() - started_at) * 1000.0
    return result.returncode, elapsed_ms


def _run_blockfetch_with_range_refresh(
    *,
    node: str,
    from_point: str,
    to_point: str,
    timeout_seconds: int,
    max_attempts: int = 4,
) -> tuple[int, float, str, str, int]:
    current_from, current_to = from_point, to_point
    total_elapsed_ms = 0.0
    exit_code = 1
    for attempt in range(max_attempts):
        if attempt > 0:
            current_from, current_to = derive_range(PROFILE_A_CONFIG)
        exit_code, elapsed_ms = _run_blockfetch_with_elapsed(
            node=node,
            from_point=current_from,
            to_point=current_to,
            timeout_seconds=timeout_seconds,
        )
        total_elapsed_ms += elapsed_ms
        if exit_code == 0:
            return exit_code, total_elapsed_ms, current_from, current_to, attempt
    return exit_code, total_elapsed_ms, current_from, current_to, max_attempts - 1


def run_drop_timeout() -> int:
    from_point, to_point = derive_range(PROFILE_A_CONFIG)
    _emit_range_metrics("blockfetch_drop_timeout", from_point=from_point, to_point=to_point)
    port = target_port(PROFILE_A_CONFIG, BLOCKED_NODE)
    emit_runtime_metric("blockfetch_drop_timeout_blocked_peer_port", value=port, meta={"node": BLOCKED_NODE})
    timeouts = 0
    total_elapsed_ms = 0.0
    for _ in range(3):
        exit_code, elapsed_ms = _run_blockfetch_with_elapsed(
            node=BLOCKED_NODE,
            from_point=from_point,
            to_point=to_point,
            timeout_seconds=6,
        )
        total_elapsed_ms += elapsed_ms
        outcome = "timeout" if exit_code == 124 else "unexpected_exit"
        _emit_fault_result(
            "blockfetch_drop_timeout",
            node=BLOCKED_NODE,
            port=port,
            outcome=outcome,
            elapsed_ms=elapsed_ms,
            exit_code=exit_code,
        )
        if exit_code != 124:
            raise RuntimeError(f"expected timeout 124 from blocked peer {BLOCKED_NODE}, got {exit_code}")
        timeouts += 1
    emit_runtime_metric("blockfetch_drop_timeout_count", value=timeouts, meta={"node": BLOCKED_NODE})
    emit_runtime_metric("blockfetch_drop_timeout_total_elapsed_ms", value=total_elapsed_ms, meta={"node": BLOCKED_NODE})
    print(f"blocked_peer=127.0.0.1:{port} blocked_timeouts={timeouts}")
    return 0


def run_drop_isolated_peer() -> int:
    blocked_from, blocked_to = derive_range(PROFILE_A_CONFIG)
    _emit_range_metrics("blockfetch_drop_isolated_blocked", from_point=blocked_from, to_point=blocked_to)
    blocked_port = target_port(PROFILE_A_CONFIG, BLOCKED_NODE)
    emit_runtime_metric("blockfetch_drop_isolated_blocked_peer_port", value=blocked_port, meta={"node": BLOCKED_NODE})
    exit_code, blocked_elapsed_ms = _run_blockfetch_with_elapsed(
        node=BLOCKED_NODE,
        from_point=blocked_from,
        to_point=blocked_to,
        timeout_seconds=6,
    )
    _emit_fault_result(
        "blockfetch_drop_isolated_blocked",
        node=BLOCKED_NODE,
        port=blocked_port,
        outcome="timeout" if exit_code == 124 else "unexpected_exit",
        elapsed_ms=blocked_elapsed_ms,
        exit_code=exit_code,
    )
    if exit_code != 124:
        raise RuntimeError(f"expected timeout 124 from blocked peer {BLOCKED_NODE}, got {exit_code}")

    peer_passes = 0
    total_elapsed_ms = 0.0
    for node in FETCH_TARGET_NODES:
        from_point, to_point = derive_range(PROFILE_A_CONFIG)
        initial_from, initial_to = from_point, to_point
        port = target_port(PROFILE_A_CONFIG, node)
        exit_code, elapsed_ms, from_point, to_point, refresh_count = _run_blockfetch_with_range_refresh(
            node=node,
            from_point=from_point,
            to_point=to_point,
            timeout_seconds=6,
        )
        _emit_range_metrics(f"blockfetch_drop_isolated_{node}", from_point=from_point, to_point=to_point)
        total_elapsed_ms += elapsed_ms
        _emit_fault_result(
            "blockfetch_drop_isolated_peer",
            node=node,
            port=port,
            outcome="success" if exit_code == 0 else "unexpected_exit",
            elapsed_ms=elapsed_ms,
            exit_code=exit_code,
        )
        if exit_code != 0:
            raise RuntimeError(f"expected successful fetch from isolated peer fallback {node}, got {exit_code}")
        emit_runtime_metric("blockfetch_drop_isolated_peer_refresh_count", value=refresh_count, meta={"node": node})
        if refresh_count:
            emit_target_event(
                primitive="runtime_blockfetch_port_fault_check",
                event="blockfetch_drop_isolated_peer_retry",
                payload={
                    "node": node,
                    "port": port,
                    "refresh_count": refresh_count,
                    "initial_from_point": initial_from,
                    "initial_to_point": initial_to,
                    "final_from_point": from_point,
                    "final_to_point": to_point,
                },
            )
        emit_runtime_metric("blockfetch_drop_isolated_peer_port", value=port, meta={"node": node})
        peer_passes += 1
    emit_runtime_metric("blockfetch_drop_isolated_timeout_count", value=1, meta={"node": BLOCKED_NODE})
    emit_runtime_metric("blockfetch_drop_isolated_peer_passes", value=peer_passes, meta={"nodes": list(FETCH_TARGET_NODES)})
    emit_runtime_metric("blockfetch_drop_isolated_total_elapsed_ms", value=total_elapsed_ms + blocked_elapsed_ms, meta={"phase": "combined"})
    print(f"blocked_peer=127.0.0.1:{blocked_port} blocked_timeouts=1 peer_passes={peer_passes}")
    return 0


def run_delay_success() -> int:
    from_point, to_point = derive_range(PROFILE_A_CONFIG)
    _emit_range_metrics("blockfetch_delay_success", from_point=from_point, to_point=to_point)
    port = target_port(PROFILE_A_CONFIG, BLOCKED_NODE)
    emit_runtime_metric("blockfetch_delay_success_peer_port", value=port, meta={"node": BLOCKED_NODE})
    exit_code, elapsed_ms = _run_blockfetch_with_elapsed(
        node=BLOCKED_NODE,
        from_point=from_point,
        to_point=to_point,
        timeout_seconds=6,
    )
    _emit_fault_result(
        "blockfetch_delay_success",
        node=BLOCKED_NODE,
        port=port,
        outcome="success" if exit_code == 0 else "unexpected_exit",
        elapsed_ms=elapsed_ms,
        exit_code=exit_code,
    )
    if exit_code != 0:
        raise RuntimeError(f"expected successful delayed fetch from {BLOCKED_NODE}, got {exit_code}")
    emit_runtime_metric("blockfetch_delay_success_count", value=1, meta={"node": BLOCKED_NODE})
    print(f"peer=127.0.0.1:{port} delayed_fetch=success")
    return 0


def run_delay_timeout() -> int:
    from_point, to_point = derive_range(PROFILE_A_CONFIG)
    _emit_range_metrics("blockfetch_delay_timeout", from_point=from_point, to_point=to_point)
    port = target_port(PROFILE_A_CONFIG, BLOCKED_NODE)
    emit_runtime_metric("blockfetch_delay_timeout_peer_port", value=port, meta={"node": BLOCKED_NODE})
    exit_code, elapsed_ms = _run_blockfetch_with_elapsed(
        node=BLOCKED_NODE,
        from_point=from_point,
        to_point=to_point,
        timeout_seconds=8,
    )
    outcome = "scenario_timeout" if exit_code == 124 else "unexpected_exit"
    _emit_fault_result(
        "blockfetch_delay_timeout",
        node=BLOCKED_NODE,
        port=port,
        outcome=outcome,
        elapsed_ms=elapsed_ms,
        exit_code=exit_code,
    )
    if exit_code != 124:
        raise RuntimeError(f"expected scenario timeout 124 from delayed peer {BLOCKED_NODE}, got {exit_code}")
    emit_runtime_metric("blockfetch_delay_timeout_count", value=1, meta={"node": BLOCKED_NODE})
    print(f"peer=127.0.0.1:{port} delayed_timeout=1")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: runtime_blockfetch_port_fault_check.py "
            "<drop-timeout|drop-isolated-peer|delay-success|delay-timeout>",
            file=sys.stderr,
        )
        return 2
    mode = argv[1]
    if mode == "drop-timeout":
        return run_drop_timeout()
    if mode == "drop-isolated-peer":
        return run_drop_isolated_peer()
    if mode == "delay-success":
        return run_delay_success()
    if mode == "delay-timeout":
        return run_delay_timeout()
    print(f"unknown mode: {mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
