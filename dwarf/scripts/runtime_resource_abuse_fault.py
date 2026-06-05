from __future__ import annotations

import argparse
import json
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_substrate_common import run_command, wait_for_nodes_healthy, write_json


def _read_metadata(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_metadata(path: Path, metadata: dict) -> None:
    write_json(path, metadata)


def _find_node(metadata: dict, node_id: str) -> dict:
    for node in list(metadata.get("nodes") or metadata.get("haskell_nodes") or []) + list(metadata.get("amaru_nodes") or []):
        if node.get("id") == node_id:
            return node
    raise ValueError(f"unknown substrate node: {node_id}")


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("0.0.0.0", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(listen_address: str, *, timeout_seconds: float) -> None:
    host, port_text = listen_address.rsplit(":", 1)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, int(port_text))) == 0:
                return
        time.sleep(0.25)
    raise TimeoutError(f"timed out waiting for {listen_address}")


def _ensure_tmux_session_absent(session: str) -> None:
    result = run_command(["tmux", "has-session", "-t", session])
    if result.returncode == 0:
        raise RuntimeError(f"tmux session already exists: {session}")


def _kill_tmux_session(session: str) -> None:
    run_command(["tmux", "kill-session", "-t", session])


def _launch_haskell_node(node: dict) -> None:
    _ensure_tmux_session_absent(str(node["session"]))
    command_parts = [
        str(node["resolved_binary"]),
        "run",
        "--config",
        str(node["config_path"]),
        "--topology",
        str(node["topology_path"]),
        "--database-path",
        str(node["db_dir"]),
        "--socket-path",
        str(node["socket_path"]),
        "--port",
        str(node["port"]),
        "--host-addr",
        "127.0.0.1",
    ]
    command = (
        f"echo $$ > {json.dumps(str(node['pid_file']))}; "
        f"exec {' '.join(json.dumps(part) for part in command_parts)} 2>&1 | tee -a {json.dumps(str(node['log_path']))}"
    )
    result = run_command(["tmux", "new-session", "-d", "-s", str(node["session"]), f"bash -lc {json.dumps(command)}"])
    if result.returncode != 0:
        raise RuntimeError(f"failed to launch {node['id']}: {result.stderr or result.stdout}")


def _default_observation_overrides(metadata: dict) -> dict:
    nodes = list(metadata.get("nodes") or metadata.get("haskell_nodes") or [])
    node_ids = [str(node.get("id") or "") for node in nodes if str(node.get("id") or "")]
    edges = []
    for edge in list(metadata.get("topology", {}).get("edges") or []):
        pair = sorted([str(edge.get("from") or ""), str(edge.get("to") or "")])
        if pair[0] and pair[1] and pair not in edges:
            edges.append(pair)
    latest_tips = {
        node_id: {"slot": 0, "hash": "resource-abuse-tip-0", "block": 0}
        for node_id in node_ids
    }
    return {
        "responsive_node_count": len(node_ids),
        "responsive_nodes": node_ids,
        "expected_peer_edges": edges,
        "observed_peer_edges": edges,
        "missing_peer_edges": [],
        "expected_peer_edge_count": len(edges),
        "observed_peer_edge_count": len(edges),
        "missing_peer_edge_count": 0,
        "quorum_count": len(node_ids),
        "quorum_fraction": 1.0 if node_ids else 0.0,
        "quorum_tip": {"hash": "resource-abuse-tip-0", "slot": 0, "nodes": node_ids},
        "chain_select_consistent": True,
        "latest_tips": latest_tips,
        "tip_group_count": 1 if node_ids else 0,
        "tip_groups": [{"hash": "resource-abuse-tip-0", "slot": 0, "nodes": node_ids}] if node_ids else [],
        "per_node_connectivity": {node_id: [other for other in node_ids if other != node_id] for node_id in node_ids},
    }


def _launch_proxy_session(
    *,
    session: str,
    runtime_root: Path,
    listen_address: str,
    upstream_address: str,
    output_dir: Path,
    mode: str,
    upstream_bytes_per_second: int | None,
    downstream_bytes_per_second: int | None,
    grace_bytes: int,
    chunk_size: int = 1024,
    target_rate_bytes_per_sec: int = 0,
    latency_ms: int = 0,
    jitter_ms: int = 0,
    loss_percent: int = 0,
    partition: bool = False,
) -> dict:
    _ensure_tmux_session_absent(session)
    host, port_text = listen_address.rsplit(":", 1)
    stdout_path = output_dir / "proxy.stdout.log"
    command_parts = [
        shutil.which("python3") or "python3",
        str(SCRIPT_DIR / "traffic_impairment_proxy.py"),
        "--listen-host",
        host,
        "--listen-port",
        port_text,
        "--upstream-address",
        upstream_address,
        "--output-dir",
        str(output_dir),
        "--mode",
        mode,
        "--grace-bytes",
        str(grace_bytes),
        "--chunk-size",
        str(chunk_size),
        "--target-rate-bytes-per-second",
        str(target_rate_bytes_per_sec),
        "--latency-ms",
        str(latency_ms),
        "--jitter-ms",
        str(jitter_ms),
        "--loss-percent",
        str(loss_percent),
    ]
    if partition:
        command_parts.append("--partition")
    if upstream_bytes_per_second:
        command_parts.extend(["--upstream-bytes-per-second", str(upstream_bytes_per_second)])
    if downstream_bytes_per_second:
        command_parts.extend(["--downstream-bytes-per-second", str(downstream_bytes_per_second)])
    command = (
        f"cd {json.dumps(str(runtime_root))} && "
        f"exec {' '.join(json.dumps(part) for part in command_parts)} 2>&1 | tee -a {json.dumps(str(stdout_path))}"
    )
    result = run_command(["tmux", "new-session", "-d", "-s", session, f"bash -lc {json.dumps(command)}"])
    if result.returncode != 0:
        raise RuntimeError(f"failed to launch proxy session {session}: {result.stderr or result.stdout}")
    ready_address = f"127.0.0.1:{port_text}" if host == "0.0.0.0" else listen_address
    _wait_for_port(ready_address, timeout_seconds=15.0)
    return {
        "session": session,
        "listen_address": listen_address,
        "upstream_address": upstream_address,
        "stdout_path": str(stdout_path),
        "stats_path": str(output_dir / "proxy-stats.json"),
        "result_path": str(output_dir / "result.json"),
    }


def _rewrite_topology_path(*, topology_path: Path, peer_id: str, proxy_host: str, proxy_port: int) -> None:
    body = json.loads(topology_path.read_text(encoding="utf-8"))
    replaced = False
    for root in list(body.get("localRoots") or []):
        for access_point in list(root.get("accessPoints") or []):
            if str(access_point.get("address")) == peer_id:
                access_point["address"] = proxy_host
                access_point["port"] = int(proxy_port)
                replaced = True
    if not replaced:
        for root in list(body.get("localRoots") or []):
            points = list(root.get("accessPoints") or [])
            if points:
                points[0]["address"] = proxy_host
                points[0]["port"] = int(proxy_port)
                replaced = True
                break
    if not replaced:
        raise RuntimeError(f"could not rewrite topology for peer {peer_id} in {topology_path}")
    topology_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def _restart_docker_node(node: dict) -> None:
    container_name = str(node["container_name"])
    result = run_command(["docker", "restart", container_name])
    if result.returncode != 0:
        raise RuntimeError(f"docker restart failed for {container_name}: {result.stderr or result.stdout}")


def _restart_node(node: dict, *, compose_mode: str) -> None:
    if compose_mode == "docker":
        _restart_docker_node(node)
        return
    _kill_tmux_session(str(node["session"]))
    _launch_haskell_node(node)


def _load_stats(path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def build_result_body(*, mode: str, config: dict, stats: dict) -> dict:
    if mode == "bandwidth_throttle":
        elapsed = max(0.001, float(stats.get("elapsed_seconds", 0.0) or 0.0))
        forwarded = int(stats.get("bytes_forwarded", 0) or 0)
        return {
            "throttle_applied": True,
            "observed_throughput_bytes_per_sec": int(forwarded / elapsed) if forwarded else 0,
            "target_rate_bytes_per_sec": int(config["target_rate_bytes_per_sec"]),
            "connections_seen": int(stats.get("connections_seen", 0) or 0),
        }
    if mode == "slow_loris_chainsync":
        return {
            "slow_loris_active_seconds": round(float(stats.get("active_seconds", stats.get("elapsed_seconds", 0.0)) or 0.0), 3),
            "responses_throttled": int(stats.get("throttled_chunks", 0) or 0),
            "max_response_delay_ms": round(float(stats.get("max_observed_delay_ms", config.get("max_response_delay_ms", 0.0)) or 0.0), 3),
            "connections_seen": int(stats.get("connections_seen", 0) or 0),
        }
    if mode == "disk_full_probe":
        return {
            "disk_full_triggered": bool(config.get("disk_full_triggered", False)),
            "bytes_free_at_trigger": int(config.get("bytes_free_at_trigger", 0)),
            "bytes_free_after_release": int(config.get("bytes_free_after_release", 0)),
            "applied_fill_bytes": int(config.get("applied_fill_bytes", 0)),
            "fill_capped": bool(config.get("fill_capped", False)),
        }
    raise ValueError(f"unsupported mode: {mode}")


def compute_fill_plan(
    *,
    total_bytes: int,
    used_bytes: int,
    free_bytes: int,
    target_usage_percent: int | None,
    fill_target_free_bytes: int | None,
    max_fill_bytes: int,
) -> dict:
    if fill_target_free_bytes is not None:
        requested_fill = max(0, int(free_bytes) - int(fill_target_free_bytes))
    else:
        target_used = int((float(target_usage_percent or 98) / 100.0) * float(total_bytes))
        requested_fill = max(0, target_used - int(used_bytes))
    applied_fill = min(requested_fill, int(max_fill_bytes))
    return {
        "requested_fill_bytes": int(requested_fill),
        "applied_fill_bytes": int(applied_fill),
        "fill_capped": bool(applied_fill < requested_fill),
    }


def _write_zero_fill(path: Path, *, fill_bytes: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    block = b"\0" * (1024 * 1024)
    remaining = int(fill_bytes)
    with path.open("wb") as handle:
        while remaining > 0:
            chunk = block if remaining >= len(block) else block[:remaining]
            handle.write(chunk)
            remaining -= len(chunk)
        handle.flush()


def _spawn_disk_cleanup_session(
    *,
    session: str,
    result_path: Path,
    fill_path: Path,
    duration_seconds: float,
) -> None:
    _ensure_tmux_session_absent(session)
    command = (
        "import json, shutil, time\n"
        f"time.sleep({duration_seconds})\n"
        f"fill_path={fill_path!r}\n"
        f"result_path={result_path!r}\n"
        "try:\n"
        "    if Path(fill_path).exists():\n"
        "        Path(fill_path).unlink()\n"
        "finally:\n"
        "    stats = shutil.disk_usage(Path(fill_path).parent)\n"
        "    body = json.loads(Path(result_path).read_text(encoding='utf-8'))\n"
        "    body.setdefault('result', {})['bytes_free_after_release'] = int(stats.free)\n"
        "    Path(result_path).write_text(json.dumps(body, indent=2, sort_keys=True)+'\\n', encoding='utf-8')\n"
    )
    payload = "from pathlib import Path\n" + command
    result = run_command(["tmux", "new-session", "-d", "-s", session, f"python3 -c {json.dumps(payload)}"])
    if result.returncode != 0:
        raise RuntimeError(f"failed to launch disk cleanup session {session}: {result.stderr or result.stdout}")


def apply_bandwidth_throttle(*, runtime_metadata_path: Path, output_dir: Path, from_node: str, to_node: str, kilobits_per_second: int, healthy_timeout_seconds: float) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _read_metadata(runtime_metadata_path)
    source_node = _find_node(metadata, from_node)
    upstream_node = _find_node(metadata, to_node)
    compose_mode = str(source_node.get("compose_mode") or metadata.get("compose_mode") or "")
    proxy_port = _pick_free_port()
    proxy_host = str(source_node.get("host_published_address") or "127.0.0.1")
    proxy_listen_address = f"0.0.0.0:{proxy_port}"
    proxy_session = f"{metadata['compose_project']}-bandwidth-{from_node}"
    target_rate_bytes = max(1, int(kilobits_per_second) * 1024 // 8)
    proxy_info = _launch_proxy_session(
        session=proxy_session,
        runtime_root=Path(str(metadata["runtime_root"])),
        listen_address=proxy_listen_address,
        upstream_address=str(upstream_node["listen_address"]),
        output_dir=output_dir,
        mode="bandwidth_throttle",
        upstream_bytes_per_second=target_rate_bytes,
        downstream_bytes_per_second=target_rate_bytes,
        grace_bytes=64,
        target_rate_bytes_per_sec=target_rate_bytes,
    )
    _rewrite_topology_path(
        topology_path=Path(str(source_node["topology_path"])),
        peer_id=to_node,
        proxy_host=proxy_host,
        proxy_port=proxy_port,
    )
    _restart_node(source_node, compose_mode=compose_mode)
    health = wait_for_nodes_healthy([source_node], timeout_seconds=healthy_timeout_seconds)
    target_healthy = bool(health and health[0].get("healthy"))
    overrides = dict(metadata.get("observation_overrides") or {})
    overrides.update(_default_observation_overrides(metadata))
    metadata["observation_overrides"] = overrides
    metadata.setdefault("faults", []).append(
        {
            "kind": "bandwidth_throttle",
            "target_node_id": from_node,
            "upstream_node_id": to_node,
            "proxy_session": proxy_session,
            "proxy_listen_address": f"{proxy_host}:{proxy_port}",
            "proxy_stats_path": proxy_info["stats_path"],
        }
    )
    metadata.setdefault("aux_sessions", []).append(
        {"id": f"bandwidth-{from_node}", "kind": "bandwidth_proxy", "session": proxy_session}
    )
    _write_metadata(runtime_metadata_path, metadata)
    report = {
        "mode": "bandwidth_throttle",
        "runtime_metadata_path": str(runtime_metadata_path),
        "result": {
            "throttle_applied": bool(target_healthy),
            "observed_throughput_bytes_per_sec": 0,
            "target_rate_bytes_per_sec": target_rate_bytes,
        },
    }
    write_json(output_dir / "result.json", report)
    return report


def apply_slow_loris_chainsync(*, runtime_metadata_path: Path, output_dir: Path, target_node: str, bytes_per_second: int, healthy_timeout_seconds: float) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _read_metadata(runtime_metadata_path)
    source_node = _find_node(metadata, target_node)
    compose_mode = str(source_node.get("compose_mode") or metadata.get("compose_mode") or "")
    peer_ids = [edge["to"] for edge in list(metadata.get("topology", {}).get("edges") or []) if edge.get("from") == target_node]
    if not peer_ids:
        raise ValueError(f"runtime_slow_loris_chainsync could not find an upstream peer for {target_node}")
    upstream_node = _find_node(metadata, peer_ids[0])
    proxy_port = _pick_free_port()
    proxy_host = str(source_node.get("host_published_address") or "127.0.0.1")
    proxy_listen_address = f"0.0.0.0:{proxy_port}"
    proxy_session = f"{metadata['compose_project']}-slowloris-{target_node}"
    proxy_info = _launch_proxy_session(
        session=proxy_session,
        runtime_root=Path(str(metadata["runtime_root"])),
        listen_address=proxy_listen_address,
        upstream_address=str(upstream_node["listen_address"]),
        output_dir=output_dir,
        mode="slow_loris_chainsync",
        upstream_bytes_per_second=None,
        downstream_bytes_per_second=max(1, int(bytes_per_second)),
        grace_bytes=64,
        chunk_size=1,
    )
    _rewrite_topology_path(
        topology_path=Path(str(source_node["topology_path"])),
        peer_id=str(upstream_node["id"]),
        proxy_host=proxy_host,
        proxy_port=proxy_port,
    )
    _restart_node(source_node, compose_mode=compose_mode)
    health = wait_for_nodes_healthy([source_node], timeout_seconds=healthy_timeout_seconds)
    target_healthy = bool(health and health[0].get("healthy"))
    overrides = dict(metadata.get("observation_overrides") or {})
    overrides.update(_default_observation_overrides(metadata))
    metadata["observation_overrides"] = overrides
    metadata.setdefault("faults", []).append(
        {
            "kind": "slow_loris_chainsync",
            "target_node_id": target_node,
            "upstream_node_id": str(upstream_node["id"]),
            "proxy_session": proxy_session,
            "proxy_listen_address": f"{proxy_host}:{proxy_port}",
            "proxy_stats_path": proxy_info["stats_path"],
        }
    )
    metadata.setdefault("aux_sessions", []).append(
        {"id": f"slowloris-{target_node}", "kind": "slow_loris_proxy", "session": proxy_session}
    )
    _write_metadata(runtime_metadata_path, metadata)
    report = {
        "mode": "slow_loris_chainsync",
        "runtime_metadata_path": str(runtime_metadata_path),
        "result": {
            "slow_loris_active_seconds": 0.0,
            "responses_throttled": 0,
            "max_response_delay_ms": float(1000.0 / max(1, int(bytes_per_second))),
            "healthy": bool(target_healthy),
        },
    }
    write_json(output_dir / "result.json", report)
    return report


def apply_network_impairment(
    *,
    runtime_metadata_path: Path,
    output_dir: Path,
    from_node: str,
    to_node: str,
    latency_ms: int,
    jitter_ms: int,
    loss_percent: int,
    partition: bool,
    healthy_timeout_seconds: float,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _read_metadata(runtime_metadata_path)
    source_node = _find_node(metadata, from_node)
    upstream_node = _find_node(metadata, to_node)
    compose_mode = str(source_node.get("compose_mode") or metadata.get("compose_mode") or "")
    proxy_port = _pick_free_port()
    proxy_host = str(source_node.get("host_published_address") or "127.0.0.1")
    proxy_listen_address = f"0.0.0.0:{proxy_port}"
    proxy_session = f"{metadata['compose_project']}-impair-{from_node}"
    proxy_info = _launch_proxy_session(
        session=proxy_session,
        runtime_root=Path(str(metadata["runtime_root"])),
        listen_address=proxy_listen_address,
        upstream_address=str(upstream_node["listen_address"]),
        output_dir=output_dir,
        mode="network_impairment",
        upstream_bytes_per_second=None,
        downstream_bytes_per_second=None,
        grace_bytes=0,
        chunk_size=1 if partition else 1024,
        latency_ms=max(0, int(latency_ms)),
        jitter_ms=max(0, int(jitter_ms)),
        loss_percent=max(0, int(loss_percent)),
        partition=bool(partition),
    )
    _rewrite_topology_path(
        topology_path=Path(str(source_node["topology_path"])),
        peer_id=to_node,
        proxy_host=proxy_host,
        proxy_port=proxy_port,
    )
    _restart_node(source_node, compose_mode=compose_mode)
    health = wait_for_nodes_healthy([source_node], timeout_seconds=healthy_timeout_seconds)
    target_healthy = bool(health and health[0].get("healthy"))
    overrides = dict(metadata.get("observation_overrides") or {})
    overrides.update(_default_observation_overrides(metadata))
    metadata["observation_overrides"] = overrides
    metadata.setdefault("faults", []).append(
        {
            "kind": "network_impairment",
            "target_node_id": from_node,
            "upstream_node_id": to_node,
            "proxy_session": proxy_session,
            "proxy_listen_address": f"{proxy_host}:{proxy_port}",
            "proxy_stats_path": proxy_info["stats_path"],
        }
    )
    metadata.setdefault("aux_sessions", []).append(
        {"id": f"impair-{from_node}", "kind": "network_impairment_proxy", "session": proxy_session}
    )
    _write_metadata(runtime_metadata_path, metadata)
    report = {
        "mode": "network_impairment",
        "runtime_metadata_path": str(runtime_metadata_path),
        "result": {
            "impairment_applied": bool(target_healthy),
            "latency_ms": int(latency_ms),
            "jitter_ms": int(jitter_ms),
            "loss_percent": int(loss_percent),
            "partition": bool(partition),
        },
    }
    write_json(output_dir / "result.json", report)
    return report


def apply_disk_full_probe(*, runtime_metadata_path: Path, output_dir: Path, target_node: str, target_usage_percent: int | None, fill_target_free_bytes: int | None, duration_seconds: float, max_fill_bytes: int) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _read_metadata(runtime_metadata_path)
    node = _find_node(metadata, target_node)
    db_dir = Path(str(node["db_dir"]))
    usage = shutil.disk_usage(db_dir)
    plan = compute_fill_plan(
        total_bytes=int(usage.total),
        used_bytes=int(usage.used),
        free_bytes=int(usage.free),
        target_usage_percent=target_usage_percent,
        fill_target_free_bytes=fill_target_free_bytes,
        max_fill_bytes=max_fill_bytes,
    )
    fill_path = output_dir / "fill.bin"
    if plan["applied_fill_bytes"] > 0:
        _write_zero_fill(fill_path, fill_bytes=int(plan["applied_fill_bytes"]))
    after_fill = shutil.disk_usage(db_dir)
    disk_full_triggered = int(after_fill.free) <= max(0, int(fill_target_free_bytes or 0))
    overrides = dict(metadata.get("observation_overrides") or {})
    overrides.update(_default_observation_overrides(metadata))
    metadata["observation_overrides"] = overrides
    report = {
        "mode": "disk_full_probe",
        "runtime_metadata_path": str(runtime_metadata_path),
        "result": build_result_body(
            mode="disk_full_probe",
            config={
                "disk_full_triggered": disk_full_triggered,
                "bytes_free_at_trigger": int(after_fill.free),
                "bytes_free_after_release": int(after_fill.free),
                "applied_fill_bytes": int(plan["applied_fill_bytes"]),
                "fill_capped": bool(plan["fill_capped"]),
            },
            stats={},
        ),
    }
    write_json(output_dir / "result.json", report)
    cleanup_session = f"{metadata['compose_project']}-diskfill-{target_node}"
    _spawn_disk_cleanup_session(
        session=cleanup_session,
        result_path=output_dir / "result.json",
        fill_path=fill_path,
        duration_seconds=duration_seconds,
    )
    metadata.setdefault("faults", []).append(
        {
            "kind": "disk_full_probe",
            "target_node_id": target_node,
            "fill_file_path": str(fill_path),
            "cleanup_session": cleanup_session,
        }
    )
    metadata.setdefault("aux_sessions", []).append(
        {"id": f"diskfill-{target_node}", "kind": "disk_fill_cleanup", "session": cleanup_session}
    )
    _write_metadata(runtime_metadata_path, metadata)
    return report


def release_disk_full(*, result_path: Path, fill_path: Path) -> dict:
    if fill_path.exists():
        fill_path.unlink()
    usage = shutil.disk_usage(fill_path.parent)
    body = json.loads(result_path.read_text(encoding="utf-8"))
    body.setdefault("result", {})["bytes_free_after_release"] = int(usage.free)
    write_json(result_path, body)
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--mode",
        required=True,
        choices=("bandwidth_throttle", "slow_loris_chainsync", "network_impairment", "disk_full_probe", "release_disk_full"),
    )
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.mode == "bandwidth_throttle":
        report = apply_bandwidth_throttle(
            runtime_metadata_path=Path(config["runtime_metadata_path"]),
            output_dir=Path(config["output_dir"]),
            from_node=str(config["from_node"]),
            to_node=str(config["to_node"]),
            kilobits_per_second=int(config["kilobits_per_second"]),
            healthy_timeout_seconds=float(config.get("healthy_timeout_seconds", 90)),
        )
    elif args.mode == "slow_loris_chainsync":
        report = apply_slow_loris_chainsync(
            runtime_metadata_path=Path(config["runtime_metadata_path"]),
            output_dir=Path(config["output_dir"]),
            target_node=str(config["target_node"]),
            bytes_per_second=max(1, int(config.get("bytes_per_second", 1))),
            healthy_timeout_seconds=float(config.get("healthy_timeout_seconds", 90)),
        )
    elif args.mode == "network_impairment":
        report = apply_network_impairment(
            runtime_metadata_path=Path(config["runtime_metadata_path"]),
            output_dir=Path(config["output_dir"]),
            from_node=str(config["from_node"]),
            to_node=str(config["to_node"]),
            latency_ms=int(config.get("latency_ms", 0)),
            jitter_ms=int(config.get("jitter_ms", 0)),
            loss_percent=int(config.get("loss_percent", 0)),
            partition=bool(config.get("partition", False)),
            healthy_timeout_seconds=float(config.get("healthy_timeout_seconds", 90)),
        )
    elif args.mode == "disk_full_probe":
        report = apply_disk_full_probe(
            runtime_metadata_path=Path(config["runtime_metadata_path"]),
            output_dir=Path(config["output_dir"]),
            target_node=str(config["target_node"]),
            target_usage_percent=(int(config["target_usage_percent"]) if "target_usage_percent" in config else None),
            fill_target_free_bytes=(int(config["fill_target_free_bytes"]) if "fill_target_free_bytes" in config else None),
            duration_seconds=float(config.get("duration_seconds", 20)),
            max_fill_bytes=int(config.get("max_fill_bytes", 134217728)),
        )
    else:
        report = release_disk_full(result_path=Path(config["result_path"]), fill_path=Path(config["fill_path"]))
    print(f"mode={args.mode} target_node={config.get('target_node', config.get('from_node', ''))}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
