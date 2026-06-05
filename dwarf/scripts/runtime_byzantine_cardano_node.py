from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_byzantine_peer import (
    _ensure_tmux_session_absent,
    _find_node,
    _kill_tmux_session,
    _launch_proxy_session,
    _pick_free_port,
    _read_metadata,
    _wait_for_port,
    _write_metadata,
    replace_peer_address,
)
from runtime_compose_substrate import _docker_container_port
from runtime_snapshot_substrate import _restart_node, _stop_node
from runtime_substrate_common import run_command, wait_for_nodes_healthy, write_json


HANDSHAKE_PROTOCOL_ID = 0
SUPPORTED_BEHAVIOR = "handshake_version_downgrade_attempt"
DOCKER_PROXY_IMAGE = "python:3.12-slim"


def _cardano_local_roots(access_points: list[dict[str, int | str]]) -> list[dict]:
    if not access_points:
        return [
            {
                "accessPoints": [],
                "advertise": False,
                "behindFirewall": False,
                "diffusionMode": "InitiatorAndResponder",
                "hotValency": 0,
                "trustable": False,
                "warmValency": 0,
            }
        ]
    return [
        {
            "accessPoints": access_points,
            "advertise": False,
            "behindFirewall": False,
            "diffusionMode": "InitiatorAndResponder",
            "hotValency": len(access_points),
            "trustable": True,
            "warmValency": len(access_points),
        }
    ]


def _active_peer_addresses(node: dict, *, compose_mode: str) -> list[str]:
    if compose_mode == "docker":
        return list(node.get("container_peer_addresses") or [])
    return list(node.get("peer_addresses") or [])


def _rewrite_haskell_topology(node: dict, *, compose_mode: str = "tmux") -> None:
    topo_path = Path(str(node["topology_path"]))
    body = json.loads(topo_path.read_text(encoding="utf-8"))
    peer_points = []
    for peer_address in _active_peer_addresses(node, compose_mode=compose_mode):
        host, port_text = str(peer_address).rsplit(":", 1)
        peer_points.append({"address": host, "port": int(port_text)})
    body["localRoots"] = _cardano_local_roots(peer_points)
    if "bootstrapPeers" not in body:
        body["bootstrapPeers"] = None
    if "publicRoots" not in body:
        body["publicRoots"] = [{"accessPoints": [], "advertise": False}]
    body["useLedgerAfterSlot"] = int(body.get("useLedgerAfterSlot", -1))
    topo_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def _wait_docker_container_running(container_name: str, *, timeout_seconds: float) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        result = run_command(["docker", "inspect", "-f", "{{.State.Running}}", container_name])
        if result.returncode == 0 and result.stdout.strip().lower() == "true":
            return
        time.sleep(0.25)
    raise TimeoutError(f"timed out waiting for docker container {container_name} to enter running state")


def _launch_docker_proxy_container(
    *,
    proxy_container_name: str,
    proxy_alias: str,
    network_name: str,
    listen_port: int,
    upstream_address: str,
    output_dir: Path,
    mutation_mode: str,
    mutation_direction: str,
    mutation_protocol: int,
    mutate_after_segments: int,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        proxy_container_name,
        "--network",
        network_name,
        "--network-alias",
        proxy_alias,
        "-v",
        f"{SCRIPT_DIR}:/scripts:ro",
        "-v",
        f"{output_dir}:/output",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        DOCKER_PROXY_IMAGE,
        "python3",
        "/scripts/byzantine_mux_proxy.py",
        "--listen-host",
        "0.0.0.0",
        "--listen-port",
        str(listen_port),
        "--upstream-address",
        upstream_address,
        "--output-dir",
        "/output",
        "--mutation-mode",
        mutation_mode,
        "--mutation-direction",
        mutation_direction,
        "--mutation-protocol",
        str(mutation_protocol),
        "--mutate-after-segments",
        str(mutate_after_segments),
    ]
    result = run_command(command)
    if result.returncode != 0:
        raise RuntimeError(f"failed to launch docker proxy container {proxy_container_name}: {result.stderr or result.stdout}")
    _wait_docker_container_running(proxy_container_name, timeout_seconds=15.0)
    return {
        "container_name": proxy_container_name,
        "listen_address": f"{proxy_alias}:{listen_port}",
        "upstream_address": upstream_address,
        "stats_path": str(output_dir / "proxy-stats.json"),
        "events_path": str(output_dir / "proxy-events.ndjson"),
    }


def _remove_docker_proxy_container(proxy_container_name: str) -> None:
    result = run_command(["docker", "rm", "-f", proxy_container_name])
    if result.returncode != 0:
        raise RuntimeError(f"failed to remove docker proxy container {proxy_container_name}: {result.stderr or result.stdout}")


def _launch_haskell_node_without_bootstrap(node: dict, *, runtime_root: Path) -> None:
    _ensure_tmux_session_absent(str(node["session"]))
    Path(str(node["db_dir"])).mkdir(parents=True, exist_ok=True)
    Path(str(node["log_path"])).parent.mkdir(parents=True, exist_ok=True)
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
    if not bool(node.get("public_network", False)):
        slot = int(node["slot_index"])
        command_parts.extend(
            [
                "--shelley-kes-key",
                str(runtime_root / "env" / "pools-keys" / f"pool{slot}" / "kes.skey"),
                "--shelley-vrf-key",
                str(runtime_root / "env" / "pools-keys" / f"pool{slot}" / "vrf.skey"),
                "--shelley-operational-certificate",
                str(runtime_root / "env" / "pools-keys" / f"pool{slot}" / "opcert.cert"),
                "--byron-delegation-certificate",
                str(runtime_root / "env" / "pools-keys" / f"pool{slot}" / "byron-delegation.cert"),
                "--byron-signing-key",
                str(runtime_root / "env" / "pools-keys" / f"pool{slot}" / "byron-delegate.key"),
            ]
        )
    command = (
        f"cd {json.dumps(str(runtime_root))} && "
        f"echo $$ > {json.dumps(str(node['pid_file']))}; "
        f"exec {' '.join(json.dumps(part) for part in command_parts)} 2>&1 | tee -a {json.dumps(str(node['log_path']))}"
    )
    result = run_command(["tmux", "new-session", "-d", "-s", str(node["session"]), f"bash -lc {json.dumps(command)}"])
    if result.returncode != 0:
        raise RuntimeError(f"failed to relaunch {node['id']}: {result.stderr or result.stdout}")
    _wait_for_port(str(node["listen_address"]), timeout_seconds=15.0)


def apply_fault(
    *,
    runtime_metadata_path: Path,
    output_dir: Path,
    target_node_id: str,
    upstream_node_id: str | None,
    upstream_address: str | None,
    behavior: str,
    mutation_mode: str,
    mutation_direction: str,
    mutation_protocol: int,
    mutate_after_segments: int,
    healthy_timeout_seconds: float,
) -> dict:
    if behavior != SUPPORTED_BEHAVIOR:
        raise ValueError(f"unsupported cardano-node byzantine behavior: {behavior}")
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _read_metadata(runtime_metadata_path)
    compose_mode = str(metadata.get("compose_mode") or "tmux")
    if compose_mode == "docker" and bool(metadata.get("multi_host")):
        raise ValueError("runtime_byzantine_cardano_node currently supports single-host docker substrates only")
    runtime_root = Path(str(metadata["runtime_root"]))
    target_node = _find_node(metadata, target_node_id)
    if target_node.get("impl") != "cardano-node":
        raise ValueError("runtime_byzantine_cardano_node currently supports cardano-node targets only")
    selected_upstream = upstream_address
    if selected_upstream is None:
        if upstream_node_id is None:
            raise ValueError("runtime_byzantine_cardano_node requires upstream_node_id or upstream_address")
        upstream_node = _find_node(metadata, upstream_node_id)
        if compose_mode == "docker":
            selected_upstream = str(upstream_node.get("container_listen_address") or "")
        else:
            selected_upstream = str(upstream_node["listen_address"])
    proxy_port = _docker_container_port(target_node) if compose_mode == "docker" else _pick_free_port()
    proxy_session = f"{metadata['compose_project']}-byzantine-cardano-{target_node_id}"
    proxy_output_dir = output_dir / "proxy"
    if compose_mode == "docker":
        proxy_alias = f"byzantine-{target_node_id}"
        proxy_container_name = f"{metadata['compose_project']}-byzantine-cardano-{target_node_id}-1"
        proxy_info = _launch_docker_proxy_container(
            proxy_container_name=proxy_container_name,
            proxy_alias=proxy_alias,
            network_name=str(target_node["container_network"]),
            listen_port=proxy_port,
            upstream_address=selected_upstream,
            output_dir=proxy_output_dir,
            mutation_mode=mutation_mode,
            mutation_direction=mutation_direction,
            mutation_protocol=mutation_protocol,
            mutate_after_segments=mutate_after_segments,
        )
        proxy_listen_address = f"{proxy_alias}:{proxy_port}"
    else:
        proxy_listen_address = f"127.0.0.1:{proxy_port}"
        proxy_info = _launch_proxy_session(
            session=proxy_session,
            runtime_root=runtime_root,
            listen_address=proxy_listen_address,
            upstream_address=selected_upstream,
            output_dir=proxy_output_dir,
            mutation_mode=mutation_mode,
            mutation_direction=mutation_direction,
            mutation_protocol=str(mutation_protocol),
            mutate_after_segments=mutate_after_segments,
        )
    original_peer_addresses = _active_peer_addresses(target_node, compose_mode=compose_mode)
    updated_peer_addresses = replace_peer_address(
        original_peer_addresses,
        original=selected_upstream,
        replacement=proxy_listen_address,
    )
    if compose_mode == "docker":
        target_node["container_peer_addresses"] = updated_peer_addresses
    else:
        target_node["peer_addresses"] = updated_peer_addresses
    target_node["effective_peer_address"] = updated_peer_addresses[0] if updated_peer_addresses else None
    _rewrite_haskell_topology(target_node, compose_mode=compose_mode)
    if compose_mode == "docker":
        _stop_node(target_node, metadata=metadata)
        _restart_node(target_node, metadata=metadata)
    else:
        _kill_tmux_session(str(target_node["session"]))
        _launch_haskell_node_without_bootstrap(target_node, runtime_root=runtime_root)
    health = wait_for_nodes_healthy([target_node], timeout_seconds=healthy_timeout_seconds)
    target_healthy = bool(health and health[0].get("healthy"))
    fault_state = {
        "kind": "byzantine_cardano_node",
        "behavior": behavior,
        "target_node_id": target_node_id,
        "proxy_session": proxy_session,
        "proxy_listen_address": proxy_listen_address,
        "upstream_address": selected_upstream,
        "original_peer_addresses": original_peer_addresses,
        "proxy_output_dir": str(proxy_output_dir),
        "proxy_stats_path": proxy_info["stats_path"],
        "proxy_events_path": proxy_info["events_path"],
        "applied_output_dir": str(output_dir),
        "mutation_protocol": mutation_protocol,
        "compose_mode": compose_mode,
    }
    if compose_mode == "docker":
        fault_state["proxy_container_name"] = proxy_info["container_name"]
    metadata.setdefault("faults", []).append(fault_state)
    aux_entry = {
        "id": f"proxy-cardano-{target_node_id}",
        "kind": "byzantine_cardano_node_proxy",
    }
    if compose_mode == "docker":
        aux_entry["container_name"] = proxy_info["container_name"]
    else:
        aux_entry["session"] = proxy_session
    metadata.setdefault("aux_sessions", []).append(aux_entry)
    _write_metadata(runtime_metadata_path, metadata)
    report = {
        "target_node_id": target_node_id,
        "behavior": behavior,
        "handshake_protocol_id": mutation_protocol,
        "proxy_session": proxy_session,
        "proxy_listen_address": proxy_listen_address,
        "upstream_address": selected_upstream,
        "healthy": target_healthy,
        "intercepted_segments": 0,
        "mutated_segments": 0,
        "runtime_metadata_path": str(runtime_metadata_path),
    }
    write_json(output_dir / "apply-report.json", report)
    return report


def remove_fault(
    *,
    runtime_metadata_path: Path,
    output_dir: Path,
    target_node_id: str,
    healthy_timeout_seconds: float,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _read_metadata(runtime_metadata_path)
    runtime_root = Path(str(metadata["runtime_root"]))
    compose_mode = str(metadata.get("compose_mode") or "tmux")
    target_node = _find_node(metadata, target_node_id)
    fault_state = next(
        (
            item
            for item in reversed(list(metadata.get("faults") or []))
            if item.get("kind") == "byzantine_cardano_node" and item.get("target_node_id") == target_node_id
        ),
        None,
    )
    if fault_state is None:
        raise ValueError(f"no active cardano-node byzantine fault found for {target_node_id}")
    proxy_session = str(fault_state["proxy_session"])
    if compose_mode == "docker":
        target_node["container_peer_addresses"] = list(fault_state["original_peer_addresses"])
        active_peer_addresses = list(target_node["container_peer_addresses"])
    else:
        _kill_tmux_session(str(target_node["session"]))
        target_node["peer_addresses"] = list(fault_state["original_peer_addresses"])
        active_peer_addresses = list(target_node["peer_addresses"])
    target_node["effective_peer_address"] = active_peer_addresses[0] if active_peer_addresses else None
    _rewrite_haskell_topology(target_node, compose_mode=compose_mode)
    if compose_mode == "docker":
        _stop_node(target_node, metadata=metadata)
        _restart_node(target_node, metadata=metadata)
    else:
        _launch_haskell_node_without_bootstrap(target_node, runtime_root=runtime_root)
    health = wait_for_nodes_healthy([target_node], timeout_seconds=healthy_timeout_seconds)
    target_healthy = bool(health and health[0].get("healthy"))
    if compose_mode == "docker":
        _remove_docker_proxy_container(str(fault_state["proxy_container_name"]))
    else:
        _kill_tmux_session(proxy_session)
    time.sleep(1.0)
    stats = {}
    stats_path = Path(str(fault_state["proxy_stats_path"]))
    if stats_path.is_file():
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
    metadata["faults"] = [
        item
        for item in list(metadata.get("faults") or [])
        if not (item.get("kind") == "byzantine_cardano_node" and item.get("target_node_id") == target_node_id)
    ]
    metadata["aux_sessions"] = [
        item
        for item in list(metadata.get("aux_sessions") or [])
        if item.get("session") != proxy_session and item.get("container_name") != fault_state.get("proxy_container_name")
    ]
    _write_metadata(runtime_metadata_path, metadata)
    report = {
        "target_node_id": target_node_id,
        "behavior": str(fault_state.get("behavior") or SUPPORTED_BEHAVIOR),
        "handshake_protocol_id": int(fault_state.get("mutation_protocol", HANDSHAKE_PROTOCOL_ID)),
        "proxy_session": proxy_session,
        "healthy": target_healthy,
        "intercepted_segments": int(stats.get("intercepted_segments", 0)),
        "mutated_segments": int(stats.get("mutated_segments", 0)),
        "client_to_server_segments": int(stats.get("client_to_server_segments", 0)),
        "server_to_client_segments": int(stats.get("server_to_client_segments", 0)),
        "connections_seen": int(stats.get("connections_seen", 0)),
        "proxy_stats_path": str(stats_path),
        "runtime_metadata_path": str(runtime_metadata_path),
    }
    write_json(output_dir / "remove-report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=("apply", "remove"), required=True)
    args = parser.parse_args(argv)

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    common = {
        "runtime_metadata_path": Path(config["runtime_metadata_path"]),
        "output_dir": Path(config["output_dir"]),
        "target_node_id": str(config["target_node_id"]),
        "healthy_timeout_seconds": float(config.get("healthy_timeout_seconds", 90)),
    }
    if args.mode == "apply":
        report = apply_fault(
            upstream_node_id=config.get("upstream_node_id"),
            upstream_address=config.get("upstream_address"),
            behavior=str(config.get("behavior", SUPPORTED_BEHAVIOR)),
            mutation_mode=str(config.get("mutation_mode", "flip_payload_byte")),
            mutation_direction=str(config.get("mutation_direction", "outbound")),
            mutation_protocol=int(config.get("mutation_protocol", HANDSHAKE_PROTOCOL_ID)),
            mutate_after_segments=max(1, int(config.get("mutate_after_segments", 1))),
            **common,
        )
        print(
            f"applied={report['target_node_id']} behavior={report['behavior']} proxy={report['proxy_listen_address']}",
            flush=True,
        )
    else:
        report = remove_fault(**common)
        print(
            f"removed={report['target_node_id']} behavior={report['behavior']} intercepted={report['intercepted_segments']}",
            flush=True,
        )
    return 0 if report.get("healthy", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
