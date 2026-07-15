from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Callable


VALID_IMPLS = {"cardano-node", "amaru"}
VERSION_PATTERN = re.compile(r"(\d+\.\d+\.\d+)")
NODE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
NETWORK_PATTERN = re.compile(r"^(mainnet|preprod|preview|testnet_[1-9][0-9]*)$")
HOST_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")


class CommandResult:
    def __init__(self, command: list[str], returncode: int, stdout: str, stderr: str):
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> CommandResult:
    proc = subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    return CommandResult(command, proc.returncode, proc.stdout, proc.stderr)


def normalize_substrate(substrate: dict) -> dict:
    if not isinstance(substrate, dict):
        raise ValueError("substrate must be a mapping")
    host_strategy = str(substrate.get("host_strategy", "single-host"))
    if host_strategy not in {"single-host", "explicit"}:
        raise ValueError("substrate.host_strategy must be 'single-host' or 'explicit'")
    host_specs = substrate.get("hosts") or []
    if host_specs and host_strategy != "explicit":
        raise ValueError("substrate.hosts requires substrate.host_strategy='explicit'")
    normalized_hosts: list[dict] = []
    host_ids: set[str] = set()
    if host_strategy == "explicit":
        if not isinstance(host_specs, list) or not host_specs:
            raise ValueError("substrate.hosts must be a non-empty list when host_strategy='explicit'")
        for index, host in enumerate(host_specs):
            if not isinstance(host, dict):
                raise ValueError(f"substrate.hosts[{index}] must be a mapping")
            host_id = host.get("id")
            ssh_target = host.get("ssh_target")
            remote_runtime_base = host.get("remote_runtime_base")
            published_host = host.get("published_host")
            if not isinstance(host_id, str) or not HOST_ID_PATTERN.match(host_id):
                raise ValueError(f"substrate.hosts[{index}].id must match {HOST_ID_PATTERN.pattern}")
            if host_id in host_ids:
                raise ValueError(f"substrate.hosts contains duplicate id {host_id!r}")
            host_ids.add(host_id)
            if not isinstance(ssh_target, str) or not ssh_target:
                raise ValueError(f"substrate.hosts[{index}].ssh_target must be a non-empty string")
            if not isinstance(remote_runtime_base, str) or not remote_runtime_base:
                raise ValueError(f"substrate.hosts[{index}].remote_runtime_base must be a non-empty string")
            if not isinstance(published_host, str) or not published_host:
                raise ValueError(f"substrate.hosts[{index}].published_host must be a non-empty string")
            ssh_key_path = host.get("ssh_key_path")
            if ssh_key_path is not None and (not isinstance(ssh_key_path, str) or not ssh_key_path):
                raise ValueError(f"substrate.hosts[{index}].ssh_key_path must be a non-empty string when present")
            normalized_hosts.append(
                {
                    "id": host_id,
                    "ssh_target": ssh_target,
                    "ssh_key_path": ssh_key_path,
                    "remote_runtime_base": remote_runtime_base,
                    "published_host": published_host,
                }
            )
    nodes = substrate.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("substrate.nodes must be a non-empty list")
    seen_ids: set[str] = set()
    normalized_nodes: list[dict] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValueError(f"substrate.nodes[{index}] must be a mapping")
        node_id = node.get("id")
        impl = node.get("impl")
        version = node.get("version")
        role = node.get("role")
        if not isinstance(node_id, str) or not NODE_ID_PATTERN.match(node_id):
            raise ValueError(f"substrate.nodes[{index}].id must match {NODE_ID_PATTERN.pattern}")
        if node_id in seen_ids:
            raise ValueError(f"substrate.nodes contains duplicate id {node_id!r}")
        seen_ids.add(node_id)
        if impl not in VALID_IMPLS:
            raise ValueError(f"substrate.nodes[{index}].impl must be one of {sorted(VALID_IMPLS)}")
        if not isinstance(version, str) or not version:
            raise ValueError(f"substrate.nodes[{index}].version must be a non-empty string")
        if not isinstance(role, str) or not role:
            raise ValueError(f"substrate.nodes[{index}].role must be a non-empty string")
        host_id = node.get("host")
        if host_strategy == "explicit":
            if host_id not in host_ids:
                raise ValueError(f"substrate.nodes[{index}].host must reference a declared substrate.hosts id")
        elif host_id is not None:
            raise ValueError("substrate.nodes[*].host requires substrate.host_strategy='explicit'")
        normalized_nodes.append(
            {
                "id": node_id,
                "impl": impl,
                "version": version,
                "role": role,
                "host": host_id,
            }
        )
    topology = substrate.get("topology") or {}
    if not isinstance(topology, dict):
        raise ValueError("substrate.topology must be a mapping")
    edges = topology.get("edges", [])
    if not isinstance(edges, list):
        raise ValueError("substrate.topology.edges must be a list")
    normalized_edges: list[dict[str, str]] = []
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise ValueError(f"substrate.topology.edges[{index}] must be a mapping")
        from_id = edge.get("from")
        to_id = edge.get("to")
        if from_id not in seen_ids or to_id not in seen_ids:
            raise ValueError(f"substrate.topology.edges[{index}] references unknown node ids")
        if from_id == to_id:
            raise ValueError(f"substrate.topology.edges[{index}] must not self-reference")
        normalized_edges.append({"from": from_id, "to": to_id})
    network = substrate.get("network")
    network_magic = substrate.get("network_magic")
    if network is None:
        network_magic = int(substrate.get("network_magic", 42))
        network = f"testnet_{network_magic}"
    else:
        if not isinstance(network, str) or not NETWORK_PATTERN.match(network):
            raise ValueError("substrate.network must be one of mainnet, preprod, preview, or testnet_<positive-int>")
        if network_magic is not None:
            network_magic = int(network_magic)
            if network.startswith("testnet_") and network != f"testnet_{network_magic}":
                raise ValueError("substrate.network and substrate.network_magic must describe the same testnet")
            if not network.startswith("testnet_"):
                raise ValueError("substrate.network_magic must be omitted for mainnet, preprod, or preview")
        elif network.startswith("testnet_"):
            network_magic = int(network.split("_", 1)[1])
    return {
        "host_strategy": host_strategy,
        "hosts": normalized_hosts,
        "network": network,
        "network_magic": network_magic,
        "nodes": normalized_nodes,
        "topology": {"edges": normalized_edges},
    }


def _candidate_binaries(impl: str, version: str, *, which: Callable[[str], str | None]) -> list[str]:
    paths: list[str] = []
    if impl == "cardano-node":
        for candidate in (
            which("cardano-node"),
            f"/home/nigel/.local/bin/cardano-node-{version}",
            f"/home/nigel/cardano-node-versions/{version}/bin/cardano-node",
            "/home/nigel/.local/bin/cardano-node",
        ):
            if candidate and candidate not in paths:
                paths.append(candidate)
    else:
        for candidate in (
            which("amaru"),
            f"/home/nigel/.local/bin/amaru-{version}",
            f"/home/nigel/amaru-versions/{version}/bin/amaru",
            "/home/nigel/amaru-verification/target/release/amaru",
            "/home/nigel/amaru-verification/target/debug/amaru",
        ):
            if candidate and candidate not in paths:
                paths.append(candidate)
    return paths


def _extract_version(text: str) -> str | None:
    match = VERSION_PATTERN.search(text)
    if match:
        return match.group(1)
    return None


def resolve_binary_for_node(
    node: dict,
    *,
    runner: Callable[..., CommandResult] = run_command,
    which: Callable[[str], str | None] = shutil.which,
) -> dict:
    for candidate in _candidate_binaries(node["impl"], node["version"], which=which):
        path = Path(candidate)
        try:
            result = runner([str(path), "--version"])
        except FileNotFoundError:
            continue
        combined = f"{result.stdout}\n{result.stderr}"
        detected = _extract_version(combined)
        if result.returncode == 0 and (node["version"] == "any" or detected == node["version"]):
            return {
                "status": "present",
                "satisfied": True,
                "resolved_binary": str(path),
                "resolved_version": detected or node["version"],
                "version_output": combined.strip(),
            }
    return {
        "status": "not_attempted",
        "satisfied": False,
        "resolved_binary": None,
        "resolved_version": None,
        "version_output": "",
    }


def allocate_node_plan(
    substrate: dict,
    *,
    runtime_root: Path,
    compose_project: str,
    base_haskell_port: int = 33001,
    base_amaru_port: int = 34001,
) -> dict:
    normalized = normalize_substrate(substrate)
    host_strategy = normalized.get("host_strategy", "single-host")
    hosts = list(normalized.get("hosts") or [])
    if host_strategy == "single-host":
        hosts = [
            {
                "id": "local",
                "ssh_target": None,
                "ssh_key_path": None,
                "remote_runtime_base": str(runtime_root),
                "published_host": "127.0.0.1",
            }
        ]
    host_by_id = {host["id"]: dict(host) for host in hosts}
    nodes: list[dict] = []
    haskell_index = 0
    amaru_index = 0
    haskell_per_host: dict[str, int] = {}
    amaru_per_host: dict[str, int] = {}
    for node in normalized["nodes"]:
        host_id = str(node.get("host") or hosts[0]["id"])
        if node["impl"] == "cardano-node":
            haskell_index += 1
            host_haskell_index = haskell_per_host.get(host_id, 0) + 1
            haskell_per_host[host_id] = host_haskell_index
            port = base_haskell_port + haskell_index - 1
            nodes.append(
                {
                    **node,
                    "host_id": host_id,
                    "listen_address": f"{host_by_id[host_id]['published_host']}:{port}",
                    "port": port,
                    "slot_index": haskell_index,
                    "host_slot_index": host_haskell_index,
                    "session": f"{compose_project}-{node['id']}",
                    "socket_path": str(runtime_root / "socket" / f"{node['id']}.sock"),
                    "log_path": str(runtime_root / "logs" / node["id"] / "stdout.log"),
                    "pid_file": str(runtime_root / "pids" / f"{node['id']}.pid"),
                    "db_dir": str(runtime_root / "cardano-db" / node["id"]),
                }
            )
        else:
            amaru_index += 1
            host_amaru_index = amaru_per_host.get(host_id, 0) + 1
            amaru_per_host[host_id] = host_amaru_index
            port = base_amaru_port + amaru_index - 1
            state_root = runtime_root / "amaru" / node["id"]
            network_suffix = normalized["network"]
            chain_name = f"chain.{network_suffix}.db"
            ledger_name = f"ledger.{network_suffix}.db"
            if network_suffix.startswith("testnet_"):
                # Custom-network Amaru bootstrap synthesis materializes the final node state
                # under the plain db names used by the upstream amaru-loader flow.
                chain_name = "chain.db"
                ledger_name = "ledger.db"
            nodes.append(
                {
                    **node,
                    "host_id": host_id,
                    "listen_address": f"{host_by_id[host_id]['published_host']}:{port}",
                    "port": port,
                    "slot_index": amaru_index,
                    "host_slot_index": host_amaru_index,
                    "session": f"{compose_project}-{node['id']}",
                    "chain_dir": str(state_root / chain_name),
                    "ledger_dir": str(state_root / ledger_name),
                    "state_root": str(state_root),
                    "log_path": str(runtime_root / "logs" / node["id"] / "stdout.log"),
                    "pid_file": str(state_root / "amaru.pid"),
                    "bootstrap_stdout": str(runtime_root / "logs" / node["id"] / "bootstrap.stdout.log"),
                    "bootstrap_stderr": str(runtime_root / "logs" / node["id"] / "bootstrap.stderr.log"),
                }
            )
    by_id = {node["id"]: node for node in nodes}
    first_haskell = next((node["listen_address"] for node in nodes if node["impl"] == "cardano-node"), None)
    for node in nodes:
        peer_addresses = [
            by_id[edge["to"]]["listen_address"]
            for edge in normalized["topology"]["edges"]
            if edge["from"] == node["id"]
        ]
        node["peer_addresses"] = peer_addresses
        container_peer_addresses = []
        for edge in normalized["topology"]["edges"]:
            if edge["from"] != node["id"]:
                continue
            peer = by_id[edge["to"]]
            if peer["host_id"] == node["host_id"]:
                container_port = 3001 if peer["impl"] == "cardano-node" else 5001
                container_peer_addresses.append(f"{peer['id']}:{container_port}")
            else:
                container_peer_addresses.append(peer["listen_address"])
        node["container_peer_addresses"] = container_peer_addresses
        if node["impl"] == "amaru":
            node["effective_peer_address"] = peer_addresses[0] if peer_addresses else first_haskell
            node["unsupported_extra_peer_addresses"] = peer_addresses[1:]
        else:
            node["effective_peer_address"] = None
            node["unsupported_extra_peer_addresses"] = []
    for host in hosts:
        host_nodes = [node for node in nodes if node["host_id"] == host["id"]]
        host["nodes"] = [node["id"] for node in host_nodes]
        host["runtime_root"] = str(runtime_root / "hosts" / host["id"])
    return {
        "host_strategy": host_strategy,
        "hosts": hosts,
        "network": normalized["network"],
        "network_magic": normalized["network_magic"],
        "compose_project": compose_project,
        "runtime_root": str(runtime_root),
        "nodes": nodes,
        "topology": normalized["topology"],
    }


def is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def wait_for_nodes_healthy(nodes: list[dict], *, timeout_seconds: float) -> list[dict]:
    deadline = time.time() + timeout_seconds
    statuses = [{"id": node["id"], "healthy": False, "listen_address": node["listen_address"]} for node in nodes]
    by_id = {status["id"]: status for status in statuses}
    while time.time() < deadline:
        all_healthy = True
        for node in nodes:
            host, port_text = str(node["listen_address"]).rsplit(":", 1)
            port = int(port_text)
            port_ready = is_port_open(host, port)
            path_ready = True
            if node.get("health_probe") == "port-only":
                path_ready = True
            elif node["impl"] == "cardano-node":
                path_ready = Path(node["socket_path"]).exists()
            elif node["impl"] == "amaru":
                path_ready = Path(node["pid_file"]).exists()
            healthy = bool(port_ready and path_ready)
            by_id[node["id"]]["healthy"] = healthy
            if not healthy:
                all_healthy = False
        if all_healthy:
            break
        time.sleep(1.0)
    return statuses


def write_json(path: Path, body: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
