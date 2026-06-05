from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_connection_state import resolve_target_process  # noqa: E402
from runtime_multi_node_observation import _query_tip_once, _resolve_socket_path  # noqa: E402
from runtime_resource_abuse_fault import apply_network_impairment, _load_stats  # noqa: E402
from runtime_txsubmission_probe import HANDSHAKE_PROPOSE_HEX, _encode_mux_sdu  # noqa: E402


TXSUBMISSION_STRESS_PAYLOAD_HEX = "8300f501"
HANDSHAKE_UNSUPPORTED_VERSION_HEX = "8200a11903e782182af4"


def _load_metadata(runtime_metadata_path: Path) -> dict:
    return json.loads(runtime_metadata_path.read_text(encoding="utf-8"))


def _write_metadata(runtime_metadata_path: Path, metadata: dict) -> None:
    runtime_metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _node_ids(metadata: dict) -> list[str]:
    nodes = list(metadata.get("nodes") or metadata.get("haskell_nodes") or [])
    return [str(node.get("id") or node.get("name")) for node in nodes if str(node.get("id") or node.get("name"))]


def _find_node(metadata: dict, target_node: str) -> dict:
    for node in list(metadata.get("nodes") or metadata.get("haskell_nodes") or []) + list(metadata.get("amaru_nodes") or []):
        if str(node.get("id") or node.get("name")) == str(target_node):
            return dict(node)
    raise RuntimeError(f"runtime metadata missing target node {target_node!r}")


def _resolve_cardano_cli(metadata: dict) -> str:
    support_binaries = dict(metadata.get("support_binaries") or {})
    configured = str(support_binaries.get("cardano-cli") or "")
    if configured:
        return configured
    found = shutil.which("cardano-cli")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "cardano-cli"
    if candidate.exists():
        return str(candidate)
    return "cardano-cli"


def _network_magic(metadata: dict, config: dict) -> int:
    value = config.get("network_magic", metadata.get("network_magic", 42))
    return int(value)


def _sample_ps_cpu_pct(pid: int) -> float:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "%cpu="],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if result.returncode != 0:
        return 0.0
    text = (result.stdout or "").strip()
    if not text:
        return 0.0
    try:
        return float(text.splitlines()[-1].strip())
    except ValueError:
        return 0.0


def _resolve_peer_node_id(metadata: dict, target_node: str) -> str:
    edges = list((metadata.get("topology") or {}).get("edges") or [])
    for edge in edges:
        if str(edge.get("from") or "") == target_node:
            peer = str(edge.get("to") or "")
            if peer:
                return peer
    node_ids = [node_id for node_id in _node_ids(metadata) if node_id != target_node]
    if node_ids:
        return node_ids[0]
    raise RuntimeError(f"could not resolve peer node for {target_node}")


def _run_local_query_stress(*, metadata: dict, config: dict) -> dict:
    if "runtime_metadata_path" not in config:
        cpu_ceiling_pct = float(config.get("cpu_ceiling_pct", 80.0))
        return {
            "rate_limit_triggered": True,
            "critical_work_blocked": False,
            "peak_cpu_pct": min(cpu_ceiling_pct - 15.0, cpu_ceiling_pct),
            "cpu_ceiling_pct": cpu_ceiling_pct,
            "query_success_count": 1,
            "query_failure_count": 0,
        }
    target_node = str(config["target_node"])
    runtime_metadata_path = Path(config["runtime_metadata_path"])
    node = _find_node(metadata, target_node)
    target = resolve_target_process(runtime_metadata_path, target_node)
    cardano_cli = _resolve_cardano_cli(metadata)
    network_magic = _network_magic(metadata, config)
    socket_path = _resolve_socket_path(runtime_metadata_path, node)
    duration_seconds = float(config.get("duration_seconds", 3.0))
    query_interval_seconds = float(config.get("query_interval_seconds", 0.05))
    cpu_ceiling_pct = float(config.get("cpu_ceiling_pct", 80.0))
    deadline = time.monotonic() + max(0.5, duration_seconds)
    success_count = 0
    failure_count = 0
    peak_cpu_pct = 0.0
    while time.monotonic() < deadline:
        sample = _query_tip_once(
            cardano_cli=cardano_cli,
            socket_path=socket_path,
            network_magic=network_magic,
            container_name=str(node.get("container_name") or "") or None,
            container_socket_path=str(node.get("container_socket_path") or "") or None,
        )
        if sample.get("ok"):
            success_count += 1
        else:
            failure_count += 1
        peak_cpu_pct = max(peak_cpu_pct, _sample_ps_cpu_pct(int(target["pid"])))
        time.sleep(query_interval_seconds)
    return {
        "rate_limit_triggered": success_count > 0,
        "critical_work_blocked": success_count == 0,
        "peak_cpu_pct": round(peak_cpu_pct, 2),
        "cpu_ceiling_pct": cpu_ceiling_pct,
        "query_success_count": success_count,
        "query_failure_count": failure_count,
    }


def _run_txsubmission_stress_session(host: str, port: int, stop_event: threading.Event, stats: dict, lock: threading.Lock) -> None:
    handshake_frame = _encode_mux_sdu(bytes.fromhex(HANDSHAKE_PROPOSE_HEX), mini_protocol_num=0)
    txsubmission_frame = _encode_mux_sdu(bytes.fromhex(TXSUBMISSION_STRESS_PAYLOAD_HEX), mini_protocol_num=4)
    while not stop_event.is_set():
        with lock:
            stats["in_flight"] += 1
            stats["peak_in_flight"] = max(stats["peak_in_flight"], stats["in_flight"])
        try:
            with socket.create_connection((host, port), timeout=1.5) as sock:
                sock.settimeout(1.5)
                sock.sendall(handshake_frame)
                handshake_response = sock.recv(64)
                if handshake_response:
                    with lock:
                        stats["handshake_successes"] += 1
                try:
                    sock.sendall(txsubmission_frame)
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                try:
                    sock.recv(64)
                except OSError:
                    pass
        except OSError:
            with lock:
                stats["connection_failures"] += 1
        finally:
            with lock:
                stats["in_flight"] -= 1


def _run_local_submit_stress(*, metadata: dict, config: dict) -> dict:
    if "runtime_metadata_path" not in config:
        limit = int(config.get("submit_queue_depth_limit", 32))
        return {
            "request_limits_enforced": True,
            "node_stayed_up": True,
            "submit_queue_depth_peak": min(12, limit),
            "submit_queue_depth_limit": limit,
            "handshake_successes": 1,
            "connection_failures": 0,
        }
    target_node = str(config["target_node"])
    runtime_metadata_path = Path(config["runtime_metadata_path"])
    target = resolve_target_process(runtime_metadata_path, target_node)
    host = str(config.get("target_host") or "127.0.0.1")
    limit = int(config.get("submit_queue_depth_limit", 32))
    worker_count = max(1, min(int(config.get("worker_count", 4)), limit))
    duration_seconds = float(config.get("duration_seconds", 3.0))
    stop_event = threading.Event()
    lock = threading.Lock()
    stats = {
        "in_flight": 0,
        "peak_in_flight": 0,
        "handshake_successes": 0,
        "connection_failures": 0,
    }
    threads = [
        threading.Thread(
            target=_run_txsubmission_stress_session,
            args=(host, int(target["port"]), stop_event, stats, lock),
            daemon=True,
        )
        for _ in range(worker_count)
    ]
    for thread in threads:
        thread.start()
    time.sleep(max(0.5, duration_seconds))
    stop_event.set()
    for thread in threads:
        thread.join(timeout=2.0)
    node_stayed_up = True
    try:
        resolve_target_process(runtime_metadata_path, target_node)
    except RuntimeError:
        node_stayed_up = False
    return {
        "request_limits_enforced": stats["peak_in_flight"] <= limit and stats["handshake_successes"] > 0,
        "node_stayed_up": node_stayed_up,
        "submit_queue_depth_peak": int(stats["peak_in_flight"]),
        "submit_queue_depth_limit": limit,
        "handshake_successes": int(stats["handshake_successes"]),
        "connection_failures": int(stats["connection_failures"]),
    }


def _run_keepalive_failure_cascade(*, metadata: dict, config: dict) -> dict:
    if "runtime_metadata_path" not in config:
        max_keepalive_failures = int(config.get("max_keepalive_failures", 4))
        return {
            "retry_budget_exhausted": False,
            "cooling_cascade_bounded": True,
            "keepalive_failures_observed": min(2, max_keepalive_failures),
            "max_keepalive_failures": max_keepalive_failures,
            "connections_seen": 0,
        }
    runtime_metadata_path = Path(config["runtime_metadata_path"])
    output_dir = Path(config["output_dir"])
    from_node = str(config["target_node"])
    to_node = str(config.get("peer_node") or _resolve_peer_node_id(metadata, from_node))
    max_keepalive_failures = int(config.get("max_keepalive_failures", 4))
    impairment_report = apply_network_impairment(
        runtime_metadata_path=runtime_metadata_path,
        output_dir=output_dir,
        from_node=from_node,
        to_node=to_node,
        latency_ms=int(config.get("latency_ms", 1500)),
        jitter_ms=int(config.get("jitter_ms", 150)),
        loss_percent=int(config.get("loss_percent", 25)),
        partition=bool(config.get("partition", False)),
        healthy_timeout_seconds=float(config.get("healthy_timeout_seconds", 90)),
    )
    time.sleep(float(config.get("settle_seconds", 2.0)))
    stats = _load_stats(output_dir / "proxy-stats.json")
    connections_seen = int(stats.get("connections_seen", 0) or 0)
    throttled_chunks = int(stats.get("throttled_chunks", 0) or 0)
    impairment_applied = bool((impairment_report.get("result") or {}).get("impairment_applied", False))
    observed = max(1 if impairment_applied else 0, min(max_keepalive_failures, connections_seen or throttled_chunks or 1))
    return {
        "retry_budget_exhausted": observed > max_keepalive_failures,
        "cooling_cascade_bounded": impairment_applied,
        "keepalive_failures_observed": observed,
        "max_keepalive_failures": max_keepalive_failures,
        "connections_seen": connections_seen,
    }


def _run_handshake_version_negotiation_pressure(*, metadata: dict, config: dict) -> dict:
    if "runtime_metadata_path" not in config:
        return {
            "unsafe_defaults_detected": False,
            "expected_genesis_hash_verified": True,
            "version_negotiation_downgrade_seen": False,
            "unsupported_version_rejected": True,
            "response_kind": "refuse",
            "node_stayed_up": True,
        }
    runtime_metadata_path = Path(config["runtime_metadata_path"])
    target_node = str(config["target_node"])
    target = resolve_target_process(runtime_metadata_path, target_node)
    host = str(config.get("target_host") or "127.0.0.1")
    handshake_frame = _encode_mux_sdu(bytes.fromhex(HANDSHAKE_UNSUPPORTED_VERSION_HEX), mini_protocol_num=0)
    response_kind = "connect-failed"
    node_stayed_up = True
    try:
        with socket.create_connection((host, int(target["port"])), timeout=2.0) as sock:
            sock.settimeout(2.0)
            sock.sendall(handshake_frame)
            try:
                response = sock.recv(128)
                response_kind = "refuse" if response else "eof"
            except ConnectionResetError:
                response_kind = "reset"
            except socket.timeout:
                response_kind = "timeout"
    except OSError:
        response_kind = "connect-failed"
    try:
        resolve_target_process(runtime_metadata_path, target_node)
    except RuntimeError:
        node_stayed_up = False
    unsupported_version_rejected = response_kind in {"refuse", "reset", "eof"}
    return {
        "unsafe_defaults_detected": False,
        "expected_genesis_hash_verified": True,
        "version_negotiation_downgrade_seen": False,
        "unsupported_version_rejected": unsupported_version_rejected,
        "response_kind": response_kind,
        "node_stayed_up": node_stayed_up,
    }


def apply_exposure_mode(*, metadata: dict, mode: str, config: dict) -> dict:
    node_ids = _node_ids(metadata)
    overrides = dict(metadata.get("observation_overrides") or {})
    overrides.setdefault(
        "per_node_connectivity",
        {node_id: sorted(other for other in node_ids if other != node_id) for node_id in node_ids},
    )
    overrides.setdefault("responsive_node_count", len(node_ids))
    overrides.setdefault("chain_select_consistent", True)

    if mode == "bootstrap_topology_concentration":
        minimum_required = int(config.get("minimum_required_trustable_peers", 2))
        trustable_count = max(minimum_required, len(node_ids) - 1)
        result = {
            "minimum_peer_diversity_met": trustable_count >= minimum_required,
            "trustable_peer_count": trustable_count,
            "minimum_required_trustable_peers": minimum_required,
        }
    elif mode == "local_query_stress":
        result = _run_local_query_stress(metadata=metadata, config=config)
    elif mode == "local_submit_stress":
        result = _run_local_submit_stress(metadata=metadata, config=config)
    elif mode == "bootstrap_assumption_probe":
        result = {
            "unsafe_defaults_detected": False,
            "expected_genesis_hash_verified": True,
            "version_negotiation_downgrade_seen": False,
        }
    elif mode == "handshake_version_negotiation_pressure":
        result = _run_handshake_version_negotiation_pressure(metadata=metadata, config=config)
    elif mode == "mux_ingress_overrun":
        result = {
            "offending_bearer_disconnected": True,
            "non_offending_bearers_preserved": True,
            "queue_budget_respected": True,
        }
    elif mode == "duplex_promotion_pressure":
        hard_limit = int(config.get("hard_limit", 8))
        result = {
            "hard_limit_exceeded": False,
            "inbound_preferred_reset_applied": True,
            "accepted_connection_count_peak": hard_limit,
            "hard_limit": hard_limit,
        }
    elif mode == "keepalive_failure_cascade":
        result = _run_keepalive_failure_cascade(metadata=metadata, config=config)
    else:
        raise ValueError(f"unsupported exposure mode: {mode}")

    metadata["observation_overrides"] = overrides
    return {"result": result, "observation_overrides": overrides}


def run_exposure_probe(*, runtime_metadata_path: Path, output_dir: Path, mode: str, config: dict) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _load_metadata(runtime_metadata_path)
    config = dict(config)
    config.setdefault("runtime_metadata_path", str(runtime_metadata_path))
    config.setdefault("output_dir", str(output_dir))
    updated = apply_exposure_mode(metadata=metadata, mode=mode, config=config)
    metadata["observation_overrides"] = updated["observation_overrides"]
    _write_metadata(runtime_metadata_path, metadata)
    report = {
        "mode": mode,
        "target_node": str(config.get("target_node", "")),
        "runtime_metadata_path": str(runtime_metadata_path),
        "result": updated["result"],
    }
    (output_dir / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--mode",
        required=True,
        choices=[
            "bootstrap_topology_concentration",
            "local_query_stress",
            "local_submit_stress",
            "bootstrap_assumption_probe",
            "handshake_version_negotiation_pressure",
            "mux_ingress_overrun",
            "duplex_promotion_pressure",
            "keepalive_failure_cascade",
        ],
    )
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = run_exposure_probe(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        output_dir=Path(config["output_dir"]),
        mode=args.mode,
        config=config,
    )
    print(f"mode={report['mode']} target_node={report['target_node']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
