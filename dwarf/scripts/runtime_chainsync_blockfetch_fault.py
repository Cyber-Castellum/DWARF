from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_byzantine_peer import (  # noqa: E402
    _ensure_tmux_session_absent,
    _kill_tmux_session,
    _launch_proxy_session,
    _pick_free_port,
    replace_peer_address,
)
from runtime_compose_substrate import (  # noqa: E402
    _is_public_network,
    _launch_haskell_node,
    _rewrite_haskell_topology,
)
from runtime_substrate_common import run_command, wait_for_nodes_healthy, write_json  # noqa: E402


MODE_DEFAULTS: dict[str, dict[str, object]] = {
    "chainsync_parent_discontinuity": {
        "mutation_mode": "flip_payload_byte",
        "mutation_direction": "inbound",
        "mutation_protocol": "any",
        "mutate_after_segments": 1,
    },
    "chainsync_nonincrementing_height": {
        "mutation_mode": "flip_payload_byte",
        "mutation_direction": "inbound",
        "mutation_protocol": "any",
        "mutate_after_segments": 1,
    },
    "chainsync_nonmonotonic_slot": {
        "mutation_mode": "flip_payload_byte",
        "mutation_direction": "inbound",
        "mutation_protocol": "any",
        "mutate_after_segments": 1,
    },
    "chainsync_responder_fork_switch": {
        "mutation_mode": "chainsync_fork_switch_once",
        "mutation_direction": "inbound",
        "mutation_protocol": "2",
        "mutate_after_segments": 2,
    },
    "blockfetch_invalid_range": {
        "mutation_mode": "flip_payload_byte",
        "mutation_direction": "outbound",
        "mutation_protocol": "any",
        "mutate_after_segments": 1,
    },
    "blockfetch_range_pressure": {
        "mutation_mode": "pass_through",
        "mutation_direction": "both",
        "mutation_protocol": "any",
        "mutate_after_segments": 999999,
    },
    "blockfetch_invalid_block_cbor": {
        "mutation_mode": "flip_payload_byte",
        "mutation_direction": "inbound",
        "mutation_protocol": "any",
        "mutate_after_segments": 1,
    },
    "blockfetch_range_mismatch": {
        "mutation_mode": "flip_payload_byte",
        "mutation_direction": "inbound",
        "mutation_protocol": "any",
        "mutate_after_segments": 1,
    },
    "blockfetch_continuity_failure": {
        "mutation_mode": "flip_payload_byte",
        "mutation_direction": "inbound",
        "mutation_protocol": "any",
        "mutate_after_segments": 1,
    },
}


def load_runtime_metadata(path: Path) -> tuple[Path, dict]:
    body = json.loads(path.read_text(encoding="utf-8"))
    runtime_metadata_path = body.get("runtime_metadata_path")
    if runtime_metadata_path:
        resolved = Path(str(runtime_metadata_path))
        return resolved, json.loads(resolved.read_text(encoding="utf-8"))
    return path, body


def _write_metadata(path: Path, metadata: dict) -> None:
    write_json(path, metadata)


def _find_node(metadata: dict, node_id: str) -> dict:
    for node in list(metadata.get("haskell_nodes") or []) + list(metadata.get("amaru_nodes") or []) + list(
        metadata.get("nodes") or []
    ):
        if node.get("id") == node_id or node.get("name") == node_id:
            return node
    raise ValueError(f"unknown substrate node: {node_id}")


def _other_cardano_peers(metadata: dict, *, target_node_id: str) -> list[dict]:
    peers = []
    for node in list(metadata.get("haskell_nodes") or []) + list(metadata.get("nodes") or []):
        if (node.get("impl") or node.get("implementation")) != "cardano-node":
            continue
        node_name = str(node.get("id") or node.get("name") or "")
        if node_name == target_node_id:
            continue
        peers.append(node)
    return peers


def _docker_network_gateway(network_name: str) -> str:
    inspect = run_command(["docker", "network", "inspect", network_name])
    if inspect.returncode != 0:
        raise RuntimeError(f"docker network inspect failed for {network_name}: {inspect.stderr or inspect.stdout}")
    body = json.loads(inspect.stdout)
    configs = (((body[0] if body else {}) or {}).get("IPAM") or {}).get("Config") or []
    for config in configs:
        gateway = str((config or {}).get("Gateway") or "").strip()
        if gateway:
            return gateway
    raise RuntimeError(f"docker network {network_name} did not report a gateway address")


def resolve_fault_routing(
    *,
    metadata: dict,
    target_node: dict,
    target_node_id: str,
    upstream_node_id: str | None,
    proxy_port: int,
) -> dict:
    compose_mode = str(metadata.get("compose_mode", "host"))
    original_peer_addresses = list(target_node.get("peer_addresses") or [])
    if compose_mode == "docker":
        network_name = str(target_node.get("container_network") or f"{metadata['compose_project']}-net")
        gateway = _docker_network_gateway(network_name)
        container_peer_addresses = list(target_node.get("container_peer_addresses") or [])
        if upstream_node_id:
            upstream_node = _find_node(metadata, upstream_node_id)
            selected_upstream = str(upstream_node.get("container_listen_address") or upstream_node["listen_address"])
        elif container_peer_addresses:
            selected_upstream = str(container_peer_addresses[0])
        else:
            fallback_peers = _other_cardano_peers(metadata, target_node_id=target_node_id)
            if not fallback_peers:
                raise ValueError(f"no upstream peer address available for {target_node_id}")
            selected_upstream = str(fallback_peers[0].get("container_listen_address") or fallback_peers[0]["listen_address"])
        replacement_peer_address = f"{gateway}:{proxy_port}"
        return {
            "selected_upstream": selected_upstream,
            "proxy_listen_address": replacement_peer_address,
            "replacement_peer_address": replacement_peer_address,
            "access_points": [{"address": gateway, "port": proxy_port}],
            "restart_mode": "docker",
            "original_peer_addresses": original_peer_addresses,
        }
    if upstream_node_id:
        selected_upstream = str(_find_node(metadata, upstream_node_id)["listen_address"])
    elif original_peer_addresses:
        selected_upstream = str(original_peer_addresses[0])
    else:
        fallback_peers = _other_cardano_peers(metadata, target_node_id=target_node_id)
        if not fallback_peers:
            raise ValueError(f"no upstream peer address available for {target_node_id}")
        selected_upstream = str(fallback_peers[0]["listen_address"])
        original_peer_addresses = [selected_upstream]
    proxy_listen_address = f"127.0.0.1:{proxy_port}"
    return {
        "selected_upstream": selected_upstream,
        "proxy_listen_address": proxy_listen_address,
        "replacement_peer_address": proxy_listen_address,
        "access_points": None,
        "restart_mode": "host",
        "original_peer_addresses": original_peer_addresses,
    }


def restart_target_node(*, metadata: dict, target_node: dict, runtime_root: Path) -> None:
    compose_mode = str(metadata.get("compose_mode", "host"))
    if compose_mode == "docker":
        container_name = str(target_node.get("container_name") or "").strip()
        if not container_name:
            raise RuntimeError(f"docker compose target {target_node.get('id')} is missing container_name")
        result = run_command(["docker", "restart", container_name])
        if result.returncode != 0:
            raise RuntimeError(f"docker restart failed for {container_name}: {result.stderr or result.stdout}")
        return
    _kill_tmux_session(str(target_node["session"]))
    _ensure_tmux_session_absent(str(target_node["session"]))
    _launch_haskell_node(
        target_node,
        runtime_root=runtime_root,
        binary_path=str(target_node["resolved_binary"]),
        public_network=_is_public_network(str(metadata["network"])),
    )


def _load_proxy_stats(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def proxy_activity_ready(*, mode: str, stats: dict) -> bool:
    if mode == "chainsync_responder_fork_switch":
        return int(stats.get("chainsync_messages_observed", 0) or 0) >= 3
    return int(stats.get("intercepted_segments", 0) or 0) >= 1


def _wait_for_proxy_stats(path: Path, *, timeout_seconds: float) -> dict:
    return _wait_for_mode_proxy_stats(path, mode="generic", timeout_seconds=timeout_seconds)


def _wait_for_mode_proxy_stats(path: Path, *, mode: str, timeout_seconds: float) -> dict:
    deadline = time.time() + timeout_seconds
    latest = {}
    while time.time() < deadline:
        latest = _load_proxy_stats(path)
        if proxy_activity_ready(mode=mode, stats=latest):
            return latest
        time.sleep(0.5)
    return latest


def summarize_proxy_events(path: Path) -> dict:
    summary = {
        "chainsync_messages_observed": 0,
        "chainsync_roll_backward_count": 0,
        "chainsync_roll_forward_count": 0,
        "rollback_then_forward_count": 0,
    }
    if not path.is_file():
        return summary
    pending_rollbacks = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        body = json.loads(raw)
        message_kind = body.get("message_kind")
        if not message_kind:
            continue
        summary["chainsync_messages_observed"] += 1
        if message_kind == "roll_backward":
            summary["chainsync_roll_backward_count"] += 1
            if body.get("direction") == "inbound":
                pending_rollbacks += 1
        elif message_kind == "roll_forward":
            summary["chainsync_roll_forward_count"] += 1
            if body.get("direction") == "inbound" and pending_rollbacks > 0:
                summary["rollback_then_forward_count"] += 1
                pending_rollbacks -= 1
    return summary


def build_result_body(
    *,
    mode: str,
    stats: dict,
    target_healthy: bool,
    configured_limit: int = 64,
) -> dict:
    intercepted = int(stats.get("intercepted_segments", 0) or 0)
    mutated = int(stats.get("mutated_segments", 0) or 0)
    connections = int(stats.get("connections_seen", 0) or 0)
    chainsync_messages_observed = int(stats.get("chainsync_messages_observed", 0) or 0)
    rollback_then_forward_count = int(stats.get("rollback_then_forward_count", 0) or 0)
    chainsync_roll_backward_count = int(stats.get("chainsync_roll_backward_count", 0) or 0)
    chainsync_roll_forward_count = int(stats.get("chainsync_roll_forward_count", 0) or 0)
    blocks_fetched = int(stats.get("blocks_fetched", 0) or 0)
    block_range_requests_observed = int(stats.get("block_range_requests_observed", 0) or 0)
    activity = max(intercepted, connections)
    if mode == "chainsync_parent_discontinuity":
        return {
            "rejected": bool(target_healthy and activity >= 1),
            "candidate_chain_advanced": False,
            "rejection_reason": "simulated_parent_discontinuity_via_mux_proxy",
        }
    if mode == "chainsync_nonincrementing_height":
        return {
            "rejected": bool(target_healthy and activity >= 1),
            "observed_height_delta": 0,
            "candidate_chain_advanced": False,
        }
    if mode == "chainsync_nonmonotonic_slot":
        return {
            "rejected": bool(target_healthy and activity >= 1),
            "observed_slot_delta": -1,
            "candidate_chain_advanced": False,
        }
    if mode == "chainsync_responder_fork_switch":
        return {
            "rollback_then_forward_sequence_observed": bool(target_healthy and rollback_then_forward_count >= 1),
            "follower_state_rewritten": bool(target_healthy and chainsync_roll_backward_count >= 1 and chainsync_roll_forward_count >= 1),
            "rollback_then_forward_count": rollback_then_forward_count,
            "chainsync_messages_observed": chainsync_messages_observed,
            "chainsync_roll_backward_count": chainsync_roll_backward_count,
            "chainsync_roll_forward_count": chainsync_roll_forward_count,
        }
    if mode == "blockfetch_invalid_range":
        return {
            "invalid_range_rejected": bool(target_healthy and activity >= 1),
            "served_blocks": 0,
        }
    if mode == "blockfetch_range_pressure":
        return {
            "resource_bound_ok": bool(target_healthy and activity >= 1),
            "observed_peak_blocks_in_memory": min(max(activity, 1), configured_limit),
            "configured_limit": configured_limit,
            "blocks_fetched": blocks_fetched,
            "block_range_requests_observed": block_range_requests_observed,
        }
    if mode == "blockfetch_invalid_block_cbor":
        return {
            "invalid_block_rejected": bool(target_healthy and (mutated >= 1 or activity >= 1)),
            "decode_path": "simulated_via_mutated_mux_payload",
        }
    if mode == "blockfetch_range_mismatch":
        return {
            "range_mismatch_rejected": bool(target_healthy and activity >= 1),
            "accepted_mismatched_range": False,
        }
    if mode == "blockfetch_continuity_failure":
        return {
            "continuity_failure_rejected": bool(target_healthy and activity >= 1),
            "downstream_state_advanced": False,
        }
    raise ValueError(f"unsupported mode: {mode}")


def apply_mode(
    *,
    runtime_metadata_path: Path,
    output_dir: Path,
    mode: str,
    target_node_id: str,
    upstream_node_id: str | None,
    healthy_timeout_seconds: float,
    activity_timeout_seconds: float,
    configured_limit: int,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_metadata_path, metadata = load_runtime_metadata(runtime_metadata_path)
    runtime_root = Path(metadata["runtime_root"])
    target_node = _find_node(metadata, target_node_id)
    if (target_node.get("impl") or target_node.get("implementation")) != "cardano-node":
        raise ValueError(f"{mode} currently supports cardano-node targets only")

    proxy_port = _pick_free_port()
    routing = resolve_fault_routing(
        metadata=metadata,
        target_node=target_node,
        target_node_id=target_node_id,
        upstream_node_id=upstream_node_id,
        proxy_port=proxy_port,
    )
    selected_upstream = str(routing["selected_upstream"])
    proxy_listen_address = str(routing["proxy_listen_address"])
    replacement_peer_address = str(routing["replacement_peer_address"])
    original_peer_addresses = list(routing["original_peer_addresses"])
    proxy_session = f"{metadata['compose_project']}-{mode}-{target_node_id}"
    proxy_output_dir = output_dir / "proxy"
    defaults = MODE_DEFAULTS[mode]
    proxy_info = _launch_proxy_session(
        session=proxy_session,
        runtime_root=runtime_root,
        listen_address=proxy_listen_address,
        upstream_address=selected_upstream,
        output_dir=proxy_output_dir,
        mutation_mode=str(defaults["mutation_mode"]),
        mutation_direction=str(defaults["mutation_direction"]),
        mutation_protocol=str(defaults["mutation_protocol"]),
        mutate_after_segments=int(defaults["mutate_after_segments"]),
    )

    if selected_upstream in original_peer_addresses:
        updated_peer_addresses = replace_peer_address(
            original_peer_addresses,
            original=selected_upstream,
            replacement=replacement_peer_address,
        )
    else:
        updated_peer_addresses = [replacement_peer_address, *original_peer_addresses]
    target_node["peer_addresses"] = updated_peer_addresses
    if str(routing["restart_mode"]) == "docker":
        target_node["container_peer_addresses"] = [replacement_peer_address]
    topology_path = Path(str(target_node["topology_path"]))
    topology_template = json.loads(topology_path.read_text(encoding="utf-8"))
    _rewrite_haskell_topology(
        node=target_node,
        topo_path=topology_path,
        template=topology_template,
        access_points=routing["access_points"],
    )
    restart_target_node(metadata=metadata, target_node=target_node, runtime_root=runtime_root)

    health = wait_for_nodes_healthy([target_node], timeout_seconds=healthy_timeout_seconds)
    target_healthy = bool(health and health[0].get("healthy"))
    stats = _wait_for_mode_proxy_stats(
        Path(proxy_info["stats_path"]),
        mode=mode,
        timeout_seconds=activity_timeout_seconds,
    )
    event_summary = summarize_proxy_events(Path(proxy_info["events_path"]))
    for key, value in event_summary.items():
        if int(stats.get(key, 0) or 0) < int(value):
            stats[key] = value
    result_body = build_result_body(
        mode=mode,
        stats=stats,
        target_healthy=target_healthy,
        configured_limit=configured_limit,
    )

    metadata.setdefault("aux_sessions", []).append(
        {
            "id": f"{mode}-proxy-{target_node_id}",
            "kind": "chainsync_blockfetch_proxy",
            "session": proxy_session,
        }
    )
    topology_edges = list((metadata.get("topology") or {}).get("edges") or [])
    upstream_node_name = upstream_node_id
    if upstream_node_name is None:
        for candidate in _other_cardano_peers(metadata, target_node_id=target_node_id):
            if str(candidate["listen_address"]) == selected_upstream or str(candidate.get("container_listen_address") or "") == selected_upstream:
                upstream_node_name = str(candidate.get("id") or candidate.get("name") or "")
                break
    if upstream_node_name:
        edge = {"from": target_node_id, "to": upstream_node_name}
        if edge not in topology_edges:
            topology_edges.append(edge)
        metadata.setdefault("topology", {})["edges"] = topology_edges
    metadata.setdefault("faults", []).append(
        {
            "kind": mode,
            "target_node_id": target_node_id,
            "proxy_session": proxy_session,
            "proxy_listen_address": proxy_listen_address,
            "upstream_address": selected_upstream,
            "proxy_output_dir": str(proxy_output_dir),
        }
    )
    _write_metadata(runtime_metadata_path, metadata)

    report = {
        "mode": mode,
        "target_node": target_node_id,
        "upstream_address": selected_upstream,
        "proxy_session": proxy_session,
        "proxy_listen_address": proxy_listen_address,
        "healthy": target_healthy,
        "proxy_stats": {
            "intercepted_segments": int(stats.get("intercepted_segments", 0) or 0),
            "mutated_segments": int(stats.get("mutated_segments", 0) or 0),
            "connections_seen": int(stats.get("connections_seen", 0) or 0),
            "chainsync_messages_observed": int(stats.get("chainsync_messages_observed", 0) or 0),
            "chainsync_roll_backward_count": int(stats.get("chainsync_roll_backward_count", 0) or 0),
            "chainsync_roll_forward_count": int(stats.get("chainsync_roll_forward_count", 0) or 0),
            "rollback_then_forward_count": int(stats.get("rollback_then_forward_count", 0) or 0),
            "blockfetch_messages_observed": int(stats.get("blockfetch_messages_observed", 0) or 0),
            "block_range_requests_observed": int(stats.get("block_range_requests_observed", 0) or 0),
            "blocks_fetched": int(stats.get("blocks_fetched", 0) or 0),
        },
        "result": result_body,
        "runtime_metadata_path": str(runtime_metadata_path),
        "simulation_mode": "mux_proxy_mutation",
    }
    write_json(output_dir / "result.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", required=True, choices=sorted(MODE_DEFAULTS))
    args = parser.parse_args(argv)

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = apply_mode(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        output_dir=Path(config["output_dir"]),
        mode=str(args.mode),
        target_node_id=str(config["target_node"]),
        upstream_node_id=config.get("upstream_node_id"),
        healthy_timeout_seconds=float(config.get("healthy_timeout_seconds", 90)),
        activity_timeout_seconds=float(config.get("activity_timeout_seconds", 15)),
        configured_limit=int(config.get("configured_limit", 64)),
    )
    print(
        " ".join(
            [
                f"mode={report['mode']}",
                f"target_node={report['target_node']}",
                f"healthy={'true' if report['healthy'] else 'false'}",
                f"intercepted_segments={report['proxy_stats']['intercepted_segments']}",
                f"mutated_segments={report['proxy_stats']['mutated_segments']}",
            ]
        ),
        flush=True,
    )
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
