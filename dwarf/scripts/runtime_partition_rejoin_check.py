#!/usr/bin/env python3

import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_common import (  # noqa: E402
    PROFILE_A_CONFIG,
    derive_range,
    run_blockfetch,
    target_port,
    wait_for_all_tips_with_details,
)
from runtime_telemetry import emit_runtime_metric, emit_target_event  # noqa: E402


TARGET_NODES = ("node2", "node3")
TIMEOUT_SECONDS = 6
MIN_BASELINE_SLOT = 64


def _emit_tip_snapshot(label: str, tips: dict[str, dict]) -> None:
    emit_target_event(
        primitive="runtime_partition_rejoin_check",
        event=f"{label}_tips",
        payload={node: {"slot": info["slot"], "block": info["block"]} for node, info in tips.items()},
    )
    for node, info in tips.items():
        emit_runtime_metric(f"{label}_{node}_slot", value=info["slot"], meta={"node": node, "kind": "slot"})
        emit_runtime_metric(f"{label}_{node}_block", value=info["block"], meta={"node": node, "kind": "block"})


def _run(args, *, timeout=60, check=False, text=True, capture_output=True, env=None):
    return subprocess.run(
        args,
        timeout=timeout,
        check=check,
        text=text,
        capture_output=capture_output,
        env=env,
    )


def _iptables_spec(port: int):
    return [
        ("OUTPUT", f"-p tcp -d 127.0.0.1 --dport {port} -j DROP"),
        ("INPUT", f"-p tcp -s 127.0.0.1 --sport {port} -j DROP"),
    ]


def _remove_rule(chain: str, spec: str) -> None:
    _run(["sudo", "iptables", "-w", "-D", chain, *spec.split()], timeout=15)


def _apply_rule(chain: str, spec: str) -> None:
    result = _run(["sudo", "iptables", "-w", "-I", chain, *spec.split()], timeout=15)
    if result.returncode != 0:
        raise RuntimeError(f"failed to apply {chain} {spec}")


def _run_blockfetch(peer_port: int, from_point: str, to_point: str):
    return run_blockfetch(PROFILE_A_CONFIG, peer_port, from_point, to_point, timeout_seconds=TIMEOUT_SECONDS)


def main() -> int:
    baseline, baseline_wait = wait_for_all_tips_with_details(
        PROFILE_A_CONFIG,
        TARGET_NODES,
        min_slot=MIN_BASELINE_SLOT,
        timeout_seconds=120,
    )
    if baseline_wait.get("timed_out") or baseline is None:
        raise RuntimeError(
            f"timed out waiting for partition baseline tips: {baseline_wait.get('last_error')}"
        )
    _emit_tip_snapshot("partition_rejoin_baseline", baseline)
    emit_runtime_metric(
        "partition_rejoin_baseline_wait_duration_seconds",
        value=baseline_wait["duration_seconds"],
        meta={"attempts": baseline_wait["attempts"], "min_slot": MIN_BASELINE_SLOT},
    )
    emit_runtime_metric(
        "partition_rejoin_baseline_slot_gap",
        value=abs(baseline["node2"]["slot"] - baseline["node3"]["slot"]),
        meta={"nodes": list(TARGET_NODES)},
    )
    if baseline["node2"]["slot"] != baseline["node3"]["slot"]:
        raise RuntimeError(
            f"expected node2/node3 aligned before partition, saw "
            f"{baseline['node2']['slot']} and {baseline['node3']['slot']}"
        )
    from_point, to_point = derive_range(PROFILE_A_CONFIG)
    applied = []
    timeout_nodes = []
    success_nodes = []
    apply_started = time.monotonic()
    try:
        for node in TARGET_NODES:
            for chain, spec in _iptables_spec(target_port(PROFILE_A_CONFIG, node)):
                _apply_rule(chain, spec)
                applied.append((chain, spec))
        emit_target_event(
            primitive="runtime_partition_rejoin_check",
            event="partition_rules_applied",
            payload={"rule_count": len(applied), "nodes": list(TARGET_NODES)},
        )
        emit_runtime_metric("partition_rule_count", value=len(applied), meta={"phase": "partition"})
        emit_runtime_metric(
            "partition_apply_duration_seconds",
            value=round(time.monotonic() - apply_started, 6),
            meta={"phase": "partition"},
        )
        time.sleep(2)
        for node in TARGET_NODES:
            port = target_port(PROFILE_A_CONFIG, node)
            result = _run_blockfetch(port, from_point, to_point)
            if result.returncode != 124:
                emit_target_event(
                    primitive="runtime_partition_rejoin_check",
                    event="partition_blockfetch",
                    payload={"node": node, "port": port, "exit_code": result.returncode, "phase": "partition", "success": False},
                    level="error",
                )
                print(
                    f"unexpected_partition_result node={node} port={port} exit_code={result.returncode}",
                    file=sys.stderr,
                )
                if result.stdout:
                    print(result.stdout, end="", file=sys.stderr)
                if result.stderr:
                    print(result.stderr, end="", file=sys.stderr)
                return 1
            timeout_nodes.append(node)
            emit_target_event(
                primitive="runtime_partition_rejoin_check",
                event="partition_blockfetch",
                payload={"node": node, "port": port, "exit_code": result.returncode, "phase": "partition", "success": True},
            )
            emit_runtime_metric(
                f"partition_timeout_{node}",
                value=1,
                meta={"node": node, "port": port, "phase": "partition", "expected_exit": 124},
            )
    finally:
        remove_started = time.monotonic()
        for chain, spec in reversed(applied):
            _remove_rule(chain, spec)
        emit_target_event(
            primitive="runtime_partition_rejoin_check",
            event="partition_rules_removed",
            payload={"rule_count": len(applied)},
        )
        emit_runtime_metric(
            "partition_remove_duration_seconds",
            value=round(time.monotonic() - remove_started, 6),
            meta={"phase": "rejoin"},
        )
    time.sleep(2)
    for node in TARGET_NODES:
        port = target_port(PROFILE_A_CONFIG, node)
        result = _run_blockfetch(port, from_point, to_point)
        if result.returncode != 0:
            emit_target_event(
                primitive="runtime_partition_rejoin_check",
                event="rejoin_blockfetch",
                payload={"node": node, "port": port, "exit_code": result.returncode, "phase": "rejoin", "success": False},
                level="error",
            )
            print(f"unexpected_rejoin_result node={node} port={port} exit_code={result.returncode}", file=sys.stderr)
            if result.stdout:
                print(result.stdout, end="", file=sys.stderr)
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)
            return 1
        success_nodes.append(node)
        emit_target_event(
            primitive="runtime_partition_rejoin_check",
            event="rejoin_blockfetch",
            payload={"node": node, "port": port, "exit_code": result.returncode, "phase": "rejoin", "success": True},
        )
        emit_runtime_metric(
            f"rejoin_success_{node}",
            value=1,
            meta={"node": node, "port": port, "phase": "rejoin"},
        )
    restored, restored_wait = wait_for_all_tips_with_details(PROFILE_A_CONFIG, TARGET_NODES, timeout_seconds=120)
    if restored_wait.get("timed_out") or restored is None:
        raise RuntimeError(
            f"timed out waiting for partition restored tips: {restored_wait.get('last_error')}"
        )
    _emit_tip_snapshot("partition_rejoin_restored", restored)
    emit_runtime_metric(
        "partition_rejoin_restored_wait_duration_seconds",
        value=restored_wait["duration_seconds"],
        meta={"attempts": restored_wait["attempts"]},
    )
    emit_runtime_metric(
        "partition_rejoin_restored_slot_gap",
        value=abs(restored["node2"]["slot"] - restored["node3"]["slot"]),
        meta={"nodes": list(TARGET_NODES)},
    )
    emit_runtime_metric("partition_timeout_count", value=len(timeout_nodes), meta={"phase": "partition"})
    emit_runtime_metric("rejoin_success_count", value=len(success_nodes), meta={"phase": "rejoin"})
    print(
        "partition_rejoin "
        f"baseline_node2_slot={baseline['node2']['slot']} "
        f"baseline_node3_slot={baseline['node3']['slot']} "
        f"from_point={from_point} to_point={to_point} "
        f"partition_timeouts={len(timeout_nodes)} "
        f"rejoin_successes={len(success_nodes)}"
    )
    for node in TARGET_NODES:
        info = restored[node]
        print(f"{node} slot={info['slot']} block={info['block']} syncProgress={info['syncProgress']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
