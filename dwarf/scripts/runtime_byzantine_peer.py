from __future__ import annotations

import argparse
import json
import shutil
import socket
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_substrate_common import is_port_open, run_command, wait_for_nodes_healthy, write_json


def replace_peer_address(peer_addresses: list[str], *, original: str, replacement: str) -> list[str]:
    replaced = False
    updated = []
    for address in peer_addresses:
        if address == original and not replaced:
            updated.append(replacement)
            replaced = True
        else:
            updated.append(address)
    if not replaced:
        raise ValueError(f"upstream address {original} not present in peer list")
    return updated


def _read_metadata(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_metadata(path: Path, metadata: dict) -> None:
    write_json(path, metadata)


def _find_node(metadata: dict, node_id: str) -> dict:
    for node in list(metadata.get("haskell_nodes") or []) + list(metadata.get("amaru_nodes") or []):
        if node.get("id") == node_id:
            return node
    raise ValueError(f"unknown substrate node: {node_id}")


def _kill_tmux_session(session: str) -> None:
    run_command(["tmux", "kill-session", "-t", session])


def _wait_for_port(listen_address: str, *, timeout_seconds: float) -> None:
    host, port_text = listen_address.rsplit(":", 1)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if is_port_open(host, int(port_text)):
            return
        time.sleep(0.25)
    raise TimeoutError(f"timed out waiting for {listen_address}")


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _ensure_tmux_session_absent(session: str) -> None:
    result = run_command(["tmux", "has-session", "-t", session])
    if result.returncode == 0:
        raise RuntimeError(f"tmux session already exists: {session}")


def _launch_proxy_session(
    *,
    session: str,
    runtime_root: Path,
    listen_address: str,
    upstream_address: str,
    output_dir: Path,
    mutation_mode: str,
    mutation_direction: str,
    mutation_protocol: str,
    mutate_after_segments: int,
) -> dict:
    _ensure_tmux_session_absent(session)
    host, port_text = listen_address.rsplit(":", 1)
    stdout_path = output_dir / "proxy.stdout.log"
    command_parts = [
        shutil.which("python3") or "python3",
        str(SCRIPT_DIR / "byzantine_mux_proxy.py"),
        "--listen-host",
        host,
        "--listen-port",
        port_text,
        "--upstream-address",
        upstream_address,
        "--output-dir",
        str(output_dir),
        "--mutation-mode",
        mutation_mode,
        "--mutation-direction",
        mutation_direction,
        "--mutation-protocol",
        mutation_protocol,
        "--mutate-after-segments",
        str(mutate_after_segments),
    ]
    command = (
        f"cd {json.dumps(str(runtime_root))} && "
        f"exec {' '.join(json.dumps(part) for part in command_parts)} 2>&1 | tee -a {json.dumps(str(stdout_path))}"
    )
    result = run_command(["tmux", "new-session", "-d", "-s", session, f"bash -lc {json.dumps(command)}"])
    if result.returncode != 0:
        raise RuntimeError(f"failed to launch proxy session {session}: {result.stderr or result.stdout}")
    _wait_for_port(listen_address, timeout_seconds=15.0)
    return {
        "session": session,
        "listen_address": listen_address,
        "upstream_address": upstream_address,
        "stdout_path": str(stdout_path),
        "stats_path": str(output_dir / "proxy-stats.json"),
        "events_path": str(output_dir / "proxy-events.ndjson"),
    }


def _launch_amaru_node_without_bootstrap(node: dict, *, network_name: str, runtime_root: Path) -> None:
    _ensure_tmux_session_absent(node["session"])
    Path(node["state_root"]).mkdir(parents=True, exist_ok=True)
    Path(node["log_path"]).parent.mkdir(parents=True, exist_ok=True)
    peer_addresses = list(node.get("peer_addresses") or [])
    if node.get("fallback_peer_addresses"):
        for peer_address in node["fallback_peer_addresses"]:
            if peer_address not in peer_addresses:
                peer_addresses.append(peer_address)
    if not peer_addresses and node.get("effective_peer_address"):
        peer_addresses = [node["effective_peer_address"]]
    if not peer_addresses:
        raise RuntimeError(f"no peer address available for {node['id']}")
    command_parts = [
        node["resolved_binary"],
        "run",
        "--network",
        network_name,
    ]
    for peer_address in peer_addresses:
        command_parts.extend(["--peer-address", peer_address])
    command_parts.extend(
        [
            "--listen-address",
            node["listen_address"],
            "--ledger-dir",
            node["ledger_dir"],
            "--chain-dir",
            node["chain_dir"],
            "--pid-file",
            node["pid_file"],
        ]
    )
    command = (
        f"cd {json.dumps(str(runtime_root))} && "
        f"echo $$ > {json.dumps(node['pid_file'])}; "
        f"exec {' '.join(json.dumps(part) for part in command_parts)} 2>&1 | tee -a {json.dumps(node['log_path'])}"
    )
    result = run_command(["tmux", "new-session", "-d", "-s", node["session"], f"bash -lc {json.dumps(command)}"])
    if result.returncode != 0:
        raise RuntimeError(f"failed to relaunch {node['id']}: {result.stderr or result.stdout}")


def apply_fault(
    *,
    runtime_metadata_path: Path,
    output_dir: Path,
    target_node_id: str,
    upstream_node_id: str | None,
    upstream_address: str | None,
    mutation_mode: str,
    mutation_direction: str,
    mutation_protocol: str,
    mutate_after_segments: int,
    healthy_timeout_seconds: float,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _read_metadata(runtime_metadata_path)
    runtime_root = Path(metadata["runtime_root"])
    target_node = _find_node(metadata, target_node_id)
    if target_node.get("impl") != "amaru":
        raise ValueError("runtime_byzantine_peer currently supports amaru targets only")
    selected_upstream = upstream_address
    if selected_upstream is None:
        if upstream_node_id is None:
            raise ValueError("runtime_byzantine_peer requires upstream_node_id or upstream_address")
        selected_upstream = str(_find_node(metadata, upstream_node_id)["listen_address"])
    proxy_port = _pick_free_port()
    proxy_listen_address = f"127.0.0.1:{proxy_port}"
    proxy_session = f"{metadata['compose_project']}-byzantine-{target_node_id}"
    proxy_output_dir = output_dir / "proxy"
    proxy_info = _launch_proxy_session(
        session=proxy_session,
        runtime_root=runtime_root,
        listen_address=proxy_listen_address,
        upstream_address=selected_upstream,
        output_dir=proxy_output_dir,
        mutation_mode=mutation_mode,
        mutation_direction=mutation_direction,
        mutation_protocol=mutation_protocol,
        mutate_after_segments=mutate_after_segments,
    )
    original_peer_addresses = list(target_node.get("peer_addresses") or [])
    updated_peer_addresses = replace_peer_address(
        original_peer_addresses,
        original=selected_upstream,
        replacement=proxy_listen_address,
    )
    _kill_tmux_session(str(target_node["session"]))
    target_node["peer_addresses"] = updated_peer_addresses
    target_node["effective_peer_address"] = updated_peer_addresses[0] if updated_peer_addresses else None
    _launch_amaru_node_without_bootstrap(target_node, network_name=str(metadata["network"]), runtime_root=runtime_root)
    health = wait_for_nodes_healthy([target_node], timeout_seconds=healthy_timeout_seconds)
    target_healthy = bool(health and health[0].get("healthy"))
    fault_state = {
        "kind": "byzantine_peer",
        "target_node_id": target_node_id,
        "proxy_session": proxy_session,
        "proxy_listen_address": proxy_listen_address,
        "upstream_address": selected_upstream,
        "original_peer_addresses": original_peer_addresses,
        "proxy_output_dir": str(proxy_output_dir),
        "proxy_stats_path": proxy_info["stats_path"],
        "proxy_events_path": proxy_info["events_path"],
        "applied_output_dir": str(output_dir),
    }
    metadata.setdefault("faults", []).append(fault_state)
    metadata.setdefault("aux_sessions", []).append(
        {
            "id": f"proxy-{target_node_id}",
            "kind": "byzantine_proxy",
            "session": proxy_session,
        }
    )
    _write_metadata(runtime_metadata_path, metadata)
    report = {
        "target_node_id": target_node_id,
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
    runtime_root = Path(metadata["runtime_root"])
    target_node = _find_node(metadata, target_node_id)
    fault_state = next(
        (
            item
            for item in reversed(list(metadata.get("faults") or []))
            if item.get("kind") == "byzantine_peer" and item.get("target_node_id") == target_node_id
        ),
        None,
    )
    if fault_state is None:
        raise ValueError(f"no active byzantine fault found for {target_node_id}")
    proxy_session = str(fault_state["proxy_session"])
    _kill_tmux_session(str(target_node["session"]))
    target_node["peer_addresses"] = list(fault_state["original_peer_addresses"])
    target_node["effective_peer_address"] = target_node["peer_addresses"][0] if target_node["peer_addresses"] else None
    _launch_amaru_node_without_bootstrap(target_node, network_name=str(metadata["network"]), runtime_root=runtime_root)
    health = wait_for_nodes_healthy([target_node], timeout_seconds=healthy_timeout_seconds)
    target_healthy = bool(health and health[0].get("healthy"))
    _kill_tmux_session(proxy_session)
    time.sleep(1.0)
    stats = {}
    stats_path = Path(str(fault_state["proxy_stats_path"]))
    if stats_path.is_file():
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
    metadata["faults"] = [
        item
        for item in list(metadata.get("faults") or [])
        if not (item.get("kind") == "byzantine_peer" and item.get("target_node_id") == target_node_id)
    ]
    metadata["aux_sessions"] = [
        item
        for item in list(metadata.get("aux_sessions") or [])
        if item.get("session") != proxy_session
    ]
    _write_metadata(runtime_metadata_path, metadata)
    report = {
        "target_node_id": target_node_id,
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
            mutation_mode=str(config.get("mutation_mode", "flip_payload_byte")),
            mutation_direction=str(config.get("mutation_direction", "outbound")),
            mutation_protocol=str(config.get("mutation_protocol", "any")),
            mutate_after_segments=max(1, int(config.get("mutate_after_segments", 1))),
            **common,
        )
        print(f"applied={report['target_node_id']} proxy={report['proxy_listen_address']}")
    else:
        report = remove_fault(**common)
        print(f"removed={report['target_node_id']} intercepted={report['intercepted_segments']}")
    return 0 if report.get("healthy", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
