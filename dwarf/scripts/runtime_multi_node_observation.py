#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_connection_state import resolve_target_process  # noqa: E402
from runtime_amaru_preview_proof import extract_latest_adopted_tip  # noqa: E402
from runtime_resource_profile import collect_samples, resolve_target_pid  # noqa: E402
from runtime_syscall_trace import run_trace  # noqa: E402
from runtime_telemetry import emit_target_event  # noqa: E402


DEFAULT_OUTPUT_NAME = "multi-node-observation"
BUILD_CHAIN_TIP_PATTERN = re.compile(r"build_chain tip=(?P<slot>\d+)\.(?P<hash>[0-9a-f]+)")
BUILD_LEDGER_TIP_PATTERN = re.compile(r"build_ledger tip\.hash=(?P<hash>[0-9a-f]+) tip\.slot=(?P<slot>\d+)")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_runtime_nodes(runtime_metadata_path: Path) -> tuple[dict, dict[str, dict]]:
    body = json.loads(runtime_metadata_path.read_text(encoding="utf-8"))
    raw_nodes = body.get("nodes")
    if not isinstance(raw_nodes, list):
        raw_nodes = body.get("haskell_nodes")
    if not isinstance(raw_nodes, list):
        raw_nodes = list(body.get("haskell_nodes") or []) + list(body.get("amaru_nodes") or [])
    if not isinstance(raw_nodes, list):
        raise RuntimeError(f"runtime metadata does not contain nodes or haskell_nodes: {runtime_metadata_path}")
    nodes: dict[str, dict] = {}
    for node in raw_nodes:
        name = str(node.get("name") or node.get("id") or "")
        if name:
            body_node = dict(node)
            body_node.setdefault("name", name)
            nodes[name] = body_node
    if not nodes:
        raise RuntimeError(f"runtime metadata contains no named nodes: {runtime_metadata_path}")
    return body, nodes


def _resolve_node_ids(node_ids: list[str], nodes: dict[str, dict]) -> list[str]:
    resolved = node_ids or sorted(nodes.keys())
    missing = [node_id for node_id in resolved if node_id not in nodes]
    if missing:
        raise RuntimeError(f"runtime metadata missing node ids: {', '.join(missing)}")
    return resolved


def _resolve_network_magic(metadata: dict, override: int | None) -> int:
    if override is not None:
        return int(override)
    for key in ("network_magic", "testnet_magic", "networkMagic"):
        value = metadata.get(key)
        if value is not None:
            return int(value)
    raise RuntimeError("network_magic was not provided and is missing from runtime metadata")


def _resolve_socket_path(runtime_metadata_path: Path, node: dict) -> Path:
    socket_path = node.get("socket_path")
    if socket_path:
        path = Path(str(socket_path))
        if path.exists():
            return path
    name = str(node.get("name") or "")
    candidates = [
        runtime_metadata_path.parent / "socket" / f"{name}.sock",
        runtime_metadata_path.parent / "socket" / name / "sock",
        runtime_metadata_path.parent / "env" / "socket" / name / "sock",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return Path(str(socket_path or candidates[0]))


def _resolve_port(node: dict) -> int | None:
    port = node.get("port")
    if port is None:
        return None
    try:
        return int(port)
    except (TypeError, ValueError):
        return None


def _write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _run(args: list[str], *, timeout: float = 15, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env=env,
    )


def _query_tip_once(
    *,
    cardano_cli: str,
    socket_path: Path,
    network_magic: int,
    container_name: str | None = None,
    container_socket_path: str | None = None,
) -> dict:
    if container_name and container_socket_path:
        proc = _run(
            [
                "docker",
                "exec",
                container_name,
                "cardano-cli",
                "query",
                "tip",
                "--testnet-magic",
                str(network_magic),
                "--socket-path",
                container_socket_path,
            ],
            timeout=15,
        )
    else:
        env = os.environ.copy()
        env["CARDANO_NODE_SOCKET_PATH"] = str(socket_path)
        proc = _run(
            [cardano_cli, "query", "tip", "--testnet-magic", str(network_magic)],
            timeout=15,
            env=env,
        )
    if proc.returncode != 0:
        return {
            "ok": False,
            "observed_at": _utc_now(),
            "error": (proc.stderr or proc.stdout or "").strip() or f"tip query failed ({proc.returncode})",
            "slot": None,
            "block": None,
            "hash": None,
            "syncProgress": None,
        }
    body = json.loads(proc.stdout)
    return {
        "ok": True,
        "observed_at": _utc_now(),
        "slot": int(body.get("slot", 0)),
        "block": int(body.get("block", 0)),
        "hash": str(body.get("hash", "")),
        "syncProgress": str(body.get("syncProgress", "")),
    }


def _extract_latest_amaru_tip(log_text: str) -> dict | None:
    adopted_tip = extract_latest_adopted_tip(log_text)
    if adopted_tip is not None:
        return {
            "slot": int(adopted_tip["slot"]),
            "hash": str(adopted_tip["hash"]),
            "block": int(adopted_tip["block_height"]),
        }
    latest = None
    for match in BUILD_CHAIN_TIP_PATTERN.finditer(log_text):
        latest = {
            "slot": int(match.group("slot")),
            "hash": match.group("hash"),
            "block": None,
        }
    if latest is not None:
        return latest
    for match in BUILD_LEDGER_TIP_PATTERN.finditer(log_text):
        latest = {
            "slot": int(match.group("slot")),
            "hash": match.group("hash"),
            "block": None,
        }
    return latest


def _observe_cardano_node_tip_state(
    *,
    runtime_metadata_path: Path,
    node: dict,
    network_magic: int,
    cardano_cli: str,
    observation_window_seconds: float,
    sample_interval_seconds: float,
) -> dict:
    socket_path = _resolve_socket_path(runtime_metadata_path, node)
    started = time.monotonic()
    samples = []
    while True:
        samples.append(
            _query_tip_once(
                cardano_cli=cardano_cli,
                socket_path=socket_path,
                network_magic=network_magic,
                container_name=str(node.get("container_name") or "") or None,
                container_socket_path=str(node.get("container_socket_path") or "") or None,
            )
        )
        elapsed = time.monotonic() - started
        if elapsed >= observation_window_seconds:
            break
        time.sleep(min(sample_interval_seconds, max(0.0, observation_window_seconds - elapsed)))
    latest_ok = next((sample for sample in reversed(samples) if sample.get("ok")), None)
    return {
        "target_node": str(node.get("name") or ""),
        "socket_path": str(socket_path),
        "sample_count": len(samples),
        "successful_sample_count": sum(1 for sample in samples if sample.get("ok")),
        "failed_sample_count": sum(1 for sample in samples if not sample.get("ok")),
        "latest_tip": latest_ok,
        "samples": samples,
    }


def _observe_amaru_tip_state(
    *,
    node: dict,
    observation_window_seconds: float,
    sample_interval_seconds: float,
) -> dict:
    log_path_value = str(node.get("log_path") or "")
    log_path = Path(log_path_value) if log_path_value else None
    started = time.monotonic()
    samples = []
    while True:
        observed_at = _utc_now()
        if log_path is None:
            sample = {
                "ok": False,
                "observed_at": observed_at,
                "error": "amaru runtime metadata missing log_path",
                "slot": None,
                "block": None,
                "hash": None,
                "syncProgress": None,
            }
        elif not log_path.exists():
            sample = {
                "ok": False,
                "observed_at": observed_at,
                "error": f"amaru log path does not exist: {log_path}",
                "slot": None,
                "block": None,
                "hash": None,
                "syncProgress": None,
            }
        else:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            latest_tip = _extract_latest_amaru_tip(log_text)
            if latest_tip is None:
                sample = {
                    "ok": False,
                    "observed_at": observed_at,
                    "error": "amaru log did not contain a parsable tip line",
                    "slot": None,
                    "block": None,
                    "hash": None,
                    "syncProgress": None,
                }
            else:
                sample = {
                    "ok": True,
                    "observed_at": observed_at,
                    "slot": int(latest_tip["slot"]),
                    "block": latest_tip.get("block"),
                    "hash": str(latest_tip["hash"]),
                    "syncProgress": None,
                }
        samples.append(sample)
        elapsed = time.monotonic() - started
        if elapsed >= observation_window_seconds:
            break
        time.sleep(min(sample_interval_seconds, max(0.0, observation_window_seconds - elapsed)))
    latest_ok = next((sample for sample in reversed(samples) if sample.get("ok")), None)
    return {
        "target_node": str(node.get("name") or ""),
        "log_path": str(log_path) if log_path is not None else "",
        "sample_count": len(samples),
        "successful_sample_count": sum(1 for sample in samples if sample.get("ok")),
        "failed_sample_count": sum(1 for sample in samples if not sample.get("ok")),
        "latest_tip": latest_ok,
        "samples": samples,
    }


def _observe_tip_state(
    *,
    runtime_metadata_path: Path,
    node: dict,
    network_magic: int,
    cardano_cli: str,
    observation_window_seconds: float,
    sample_interval_seconds: float,
) -> dict:
    implementation = str(node.get("implementation") or node.get("impl") or "")
    if implementation == "amaru":
        return _observe_amaru_tip_state(
            node=node,
            observation_window_seconds=observation_window_seconds,
            sample_interval_seconds=sample_interval_seconds,
        )
    return _observe_cardano_node_tip_state(
        runtime_metadata_path=runtime_metadata_path,
        node=node,
        network_magic=network_magic,
        cardano_cli=cardano_cli,
        observation_window_seconds=observation_window_seconds,
        sample_interval_seconds=sample_interval_seconds,
    )


def _parse_peer_nodes_from_ss(ss_output: str, *, known_ports: dict[int, str], target_node: str) -> list[str]:
    peers: set[str] = set()
    for line in ss_output.splitlines():
        line = line.strip()
        if not line.startswith("ESTAB"):
            continue
        parts = line.split()
        ports = []
        for token in parts:
            if ":" not in token:
                continue
            try:
                ports.append(int(token.rsplit(":", 1)[1]))
            except ValueError:
                continue
        for port in ports:
            peer = known_ports.get(port)
            if peer and peer != target_node:
                peers.add(peer)
    return sorted(peers)


def _parse_peer_nodes_from_docker_ss(ss_output: str, *, known_container_ips: dict[str, str], target_node: str) -> list[str]:
    peers: set[str] = set()
    for line in ss_output.splitlines():
        line = line.strip()
        if not line.startswith("ESTAB"):
            continue
        for token in line.split():
            if ":" not in token:
                continue
            host = token.rsplit(":", 1)[0].strip("[]")
            peer = known_container_ips.get(host)
            if peer and peer != target_node:
                peers.add(peer)
    return sorted(peers)


def _observe_connection_state(
    *,
    runtime_metadata_path: Path,
    node: dict,
    known_ports: dict[int, str],
    known_container_ips: dict[str, str],
    target_host: str,
    connect_attempts: int,
) -> dict:
    target = resolve_target_process(runtime_metadata_path, str(node.get("name") or ""))
    port = int(target["port"])
    connect_successes = 0
    connect_failures = 0
    for _ in range(connect_attempts):
        try:
            with socket.create_connection((target_host, port), timeout=1.0):
                connect_successes += 1
        except OSError:
            connect_failures += 1

    if node.get("container_name"):
        ss_proc = _run(["docker", "exec", str(node["container_name"]), "ss", "-tan"], timeout=10)
        if ss_proc.returncode != 0:
            raise RuntimeError("multi-node observation could not capture docker ss output")
        filtered_lines = []
        container_port = int(str(node.get("container_listen_address") or "node:3001").rsplit(":", 1)[1])
        for line in (ss_proc.stdout or "").splitlines():
            text = line.strip()
            if not text:
                continue
            if f":{container_port}" in text:
                filtered_lines.append(text)
        filtered_ss = "\n".join(filtered_lines) + ("\n" if filtered_lines else "")
        peer_nodes = _parse_peer_nodes_from_docker_ss(
            filtered_ss,
            known_container_ips=known_container_ips,
            target_node=str(node.get("name") or ""),
        )
        ss_lines = [line.strip() for line in filtered_ss.splitlines() if line.strip()]
        listen_count = sum(1 for line in ss_lines if line.startswith("LISTEN"))
        established_count = sum(1 for line in ss_lines if line.startswith("ESTAB"))
        return {
            "target_node": str(node.get("name") or ""),
            "pid": int(target["pid"]),
            "port": port,
            "connect_successes": connect_successes,
            "connect_failures": connect_failures,
            "ss_match_count": len(ss_lines),
            "ss_listen_count": listen_count,
            "ss_established_count": established_count,
            "lsof_socket_count": 0,
            "peer_nodes_connected": peer_nodes,
            "responsive": bool(connect_successes >= 1 and listen_count >= 1),
            "ss_lines": ss_lines,
        }

    ss_proc = _run(["ss", "-tanp"], timeout=10)
    if ss_proc.returncode != 0:
        raise RuntimeError("multi-node observation could not capture ss output")
    filtered_lines = []
    for line in (ss_proc.stdout or "").splitlines():
        text = line.strip()
        if not text:
            continue
        if f"pid={target['pid']}," in text or f":{port} " in text or text.endswith(f":{port}"):
            filtered_lines.append(text)
    filtered_ss = "\n".join(filtered_lines) + ("\n" if filtered_lines else "")
    lsof_proc = _run(["lsof", "-w", "-nP", "-p", str(target["pid"]), "-a", "-iTCP", "-iUDP"], timeout=10)
    lsof_text = lsof_proc.stdout or "" if lsof_proc.returncode in (0, 1) else ""
    peer_nodes = _parse_peer_nodes_from_ss(filtered_ss, known_ports=known_ports, target_node=str(node.get("name") or ""))
    ss_lines = [line.strip() for line in filtered_ss.splitlines() if line.strip()]
    lsof_lines = [line.strip() for line in lsof_text.splitlines() if line.strip()]
    listen_count = sum(1 for line in ss_lines if line.startswith("LISTEN"))
    established_count = sum(1 for line in ss_lines if line.startswith("ESTAB"))
    return {
        "target_node": str(node.get("name") or ""),
        "pid": int(target["pid"]),
        "port": port,
        "connect_successes": connect_successes,
        "connect_failures": connect_failures,
        "ss_match_count": len(ss_lines),
        "ss_listen_count": listen_count,
        "ss_established_count": established_count,
        "lsof_socket_count": max(0, len(lsof_lines) - 1),
        "peer_nodes_connected": peer_nodes,
        "responsive": listen_count >= 1,
        "ss_lines": ss_lines,
    }


def _observe_resource_profile(
    *,
    runtime_metadata_path: Path,
    target_node: str,
    observation_window_seconds: float,
    sample_interval_seconds: float,
) -> dict:
    sample_count = max(1, int(round(observation_window_seconds / max(sample_interval_seconds, 0.1))))
    pid = resolve_target_pid(runtime_metadata_path, target_node)
    samples = collect_samples(
        pid=pid,
        sample_count=sample_count,
        sample_interval_seconds=sample_interval_seconds,
    )
    return {
        "target_node": target_node,
        "pid": pid,
        "sample_count": len(samples),
        "max_rss_bytes": max(sample["rss_bytes"] for sample in samples) if samples else 0,
        "max_fd_count": max(sample["fd_count"] for sample in samples) if samples else 0,
        "final_threads": samples[-1]["threads"] if samples else 0,
        "samples": samples,
    }


def _observe_syscall_trace(
    *,
    runtime_metadata_path: Path,
    target_node: str,
    output_dir: Path,
    target_host: str,
    connect_attempts: int,
    startup_seconds: float,
    settle_seconds: float,
    strace_bin: str,
    sudo_bin: str,
) -> dict:
    trace_dir = output_dir / "syscall-trace"
    result = run_trace(
        runtime_metadata_path=runtime_metadata_path,
        target_node=target_node,
        output_dir=trace_dir,
        connect_attempts=connect_attempts,
        target_host=target_host,
        startup_seconds=startup_seconds,
        settle_seconds=settle_seconds,
        strace_bin=strace_bin,
        sudo_bin=sudo_bin,
    )
    return {
        "target_node": target_node,
        "pid": result.get("pid"),
        "port": result.get("port"),
        "connect_successes": result.get("connect_successes"),
        "total_syscalls": result.get("total_syscalls"),
        "unique_syscalls": result.get("unique_syscalls"),
        "top_syscall": result.get("top_syscall"),
        "top_syscall_count": result.get("top_syscall_count"),
        "trace_relpath": str(trace_dir / "trace.log"),
        "summary_relpath": str(trace_dir / "summary.json"),
    }


def _correlated_timeline(per_node: dict[str, dict]) -> list[dict]:
    events = []
    for node_id, body in per_node.items():
        tip_state = body.get("tip_state") or {}
        for sample in tip_state.get("samples", []):
            events.append(
                {
                    "ts": sample.get("observed_at"),
                    "node_id": node_id,
                    "event_type": "tip_sample",
                    "slot": sample.get("slot"),
                    "hash": sample.get("hash"),
                    "ok": sample.get("ok"),
                }
            )
        resource_profile = body.get("resource_profile") or {}
        for sample in resource_profile.get("samples", []):
            events.append(
                {
                    "ts": sample.get("ts_epoch_s"),
                    "node_id": node_id,
                    "event_type": "resource_sample",
                    "rss_bytes": sample.get("rss_bytes"),
                    "fd_count": sample.get("fd_count"),
                    "threads": sample.get("threads"),
                }
            )
    return sorted(events, key=lambda item: str(item.get("ts") or ""))


def _shared_tip_groups(per_node: dict[str, dict], node_ids: list[str]) -> list[dict]:
    counters: Counter[tuple[str, int]] = Counter()
    supporting_nodes: dict[tuple[str, int], set[str]] = {}
    for node_id in node_ids:
        tip_state = (per_node.get(node_id) or {}).get("tip_state") or {}
        seen_for_node: set[tuple[str, int]] = set()
        for sample in tip_state.get("samples", []):
            if not sample.get("ok") or not sample.get("hash"):
                continue
            try:
                key = (str(sample["hash"]), int(sample.get("slot", 0)))
            except (TypeError, ValueError):
                continue
            if key in seen_for_node:
                continue
            seen_for_node.add(key)
            counters[key] += 1
            supporting_nodes.setdefault(key, set()).add(node_id)
    return [
        {
            "hash": key[0],
            "slot": key[1],
            "count": count,
            "nodes": sorted(supporting_nodes.get(key, set())),
        }
        for key, count in sorted(counters.items(), key=lambda item: (-item[1], -item[0][1], item[0][0]))
    ]


def _summarize_observation(per_node: dict[str, dict], node_ids: list[str], metadata: dict | None = None) -> dict:
    latest_tips = {}
    responsive_nodes = []
    observed_edges: set[tuple[str, str]] = set()
    for node_id in node_ids:
        body = per_node.get(node_id) or {}
        connection_state = body.get("connection_state") or {}
        if connection_state.get("responsive"):
            responsive_nodes.append(node_id)
        for peer in connection_state.get("peer_nodes_connected", []):
            observed_edges.add((node_id, peer))
        latest_tip = ((body.get("tip_state") or {}).get("latest_tip")) or {}
        if latest_tip.get("ok") and latest_tip.get("hash"):
            latest_tips[node_id] = {
                "slot": int(latest_tip.get("slot", 0)),
                "hash": str(latest_tip.get("hash")),
                "block": latest_tip.get("block"),
            }
    tip_groups_counter: Counter[tuple[str, int]] = Counter()
    for tip in latest_tips.values():
        tip_groups_counter[(tip["hash"], tip["slot"])] += 1
    tip_groups = [
        {
            "hash": key[0],
            "slot": key[1],
            "count": count,
            "nodes": sorted(
                node_id
                for node_id, tip in latest_tips.items()
                if tip["hash"] == key[0] and tip["slot"] == key[1]
            ),
        }
        for key, count in sorted(tip_groups_counter.items(), key=lambda item: (-item[1], item[0][1], item[0][0]))
    ]
    quorum = tip_groups[0] if tip_groups else {"hash": None, "slot": None, "count": 0, "nodes": []}
    topology_edges = (((metadata or {}).get("topology") or {}).get("edges") or [])
    expected_edges = []
    if topology_edges:
        for edge in topology_edges:
            left = str(edge.get("from") or "")
            right = str(edge.get("to") or "")
            if left in node_ids and right in node_ids and left and right:
                expected_edges.append([left, right])
    else:
        for index, left in enumerate(node_ids):
            for right in node_ids[index + 1:]:
                expected_edges.append([left, right])
    observed_edge_list = [list(edge) for edge in sorted(observed_edges)]
    missing_edges = [edge for edge in expected_edges if tuple(edge) not in observed_edges]
    shared_tip_groups = _shared_tip_groups(per_node, node_ids)
    fully_shared_tip = next((group for group in shared_tip_groups if group["count"] == len(node_ids)), None)
    quorum_fraction = (int(quorum.get("count", 0)) / len(node_ids)) if node_ids else 0.0
    chain_select_consistent = (
        len(tip_groups) == 1
        and len(latest_tips) == len(node_ids)
        and quorum_fraction == 1.0
    )
    chain_eventually_consistent = fully_shared_tip is not None and len(latest_tips) == len(node_ids)
    summary = {
        "node_count": len(node_ids),
        "responsive_node_count": len(responsive_nodes),
        "responsive_nodes": sorted(responsive_nodes),
        "latest_tips": latest_tips,
        "tip_groups": tip_groups,
        "tip_group_count": len(tip_groups),
        "chain_select_consistent": chain_select_consistent,
        "chain_eventually_consistent": chain_eventually_consistent,
        "quorum_count": int(quorum.get("count", 0)),
        "quorum_fraction": quorum_fraction,
        "quorum_tip": {key: quorum.get(key) for key in ("hash", "slot", "nodes")},
        "latest_common_tip": (
            {
                "hash": fully_shared_tip["hash"],
                "slot": fully_shared_tip["slot"],
                "nodes": fully_shared_tip["nodes"],
            }
            if fully_shared_tip is not None
            else None
        ),
        "expected_peer_edges": expected_edges,
        "observed_peer_edges": observed_edge_list,
        "missing_peer_edges": missing_edges,
        "expected_peer_edge_count": len(expected_edges),
        "observed_peer_edge_count": len(observed_edge_list),
        "missing_peer_edge_count": len(missing_edges),
    }
    era_transition = (metadata or {}).get("era_transition") or {}
    for key in ("hf_boundary", "transition_window", "genesis_mode"):
        if key in era_transition:
            summary[key] = era_transition[key]
    observation_overrides = (metadata or {}).get("observation_overrides") or {}
    if isinstance(observation_overrides, dict):
        for key, value in observation_overrides.items():
            summary[key] = value
    return summary


def run_multi_node_observation(
    *,
    runtime_metadata_path: Path,
    node_ids: list[str],
    observation_window_seconds: float,
    observation_primitives: list[str],
    output_dir: Path,
    sample_interval_seconds: float = 1.0,
    network_magic: int | None = None,
    cardano_cli: str = "cardano-cli",
    connect_attempts: int = 2,
    target_host: str = "127.0.0.1",
    strace_bin: str = "strace",
    sudo_bin: str = "sudo",
) -> dict:
    metadata, nodes = _load_runtime_nodes(runtime_metadata_path)
    resolved_node_ids = _resolve_node_ids(node_ids, nodes)
    resolved_network_magic = _resolve_network_magic(metadata, network_magic)
    output_dir.mkdir(parents=True, exist_ok=True)
    known_ports = {
        port: node_id
        for node_id in resolved_node_ids
        for port in [_resolve_port(nodes[node_id])]
        if port is not None
    }
    known_container_ips = {
        str(nodes[node_id].get("container_ip")): node_id
        for node_id in resolved_node_ids
        if nodes[node_id].get("container_ip")
    }
    requested = list(dict.fromkeys(observation_primitives or ["tip_state", "connection_state"]))
    per_node = {}
    for node_id in resolved_node_ids:
        node = nodes[node_id]
        node_output_dir = output_dir / "per-node" / node_id
        node_output_dir.mkdir(parents=True, exist_ok=True)
        node_record = {
            "node_id": node_id,
            "implementation": str(node.get("implementation") or node.get("impl") or ""),
            "version": str(node.get("version") or ""),
            "port": _resolve_port(node),
            "socket_path": str(_resolve_socket_path(runtime_metadata_path, node)),
        }
        if "tip_state" in requested:
            tip_state = _observe_tip_state(
                runtime_metadata_path=runtime_metadata_path,
                node=node,
                network_magic=resolved_network_magic,
                cardano_cli=cardano_cli,
                observation_window_seconds=observation_window_seconds,
                sample_interval_seconds=sample_interval_seconds,
            )
            node_record["tip_state"] = tip_state
            _write_json(node_output_dir / "tip-state.json", tip_state)
        if "connection_state" in requested:
            connection_state = _observe_connection_state(
                runtime_metadata_path=runtime_metadata_path,
                node=node,
                known_ports=known_ports,
                known_container_ips=known_container_ips,
                target_host=target_host,
                connect_attempts=connect_attempts,
            )
            node_record["connection_state"] = connection_state
            _write_json(node_output_dir / "connection-state.json", connection_state)
        if "resource_profile" in requested:
            resource_profile = _observe_resource_profile(
                runtime_metadata_path=runtime_metadata_path,
                target_node=node_id,
                observation_window_seconds=observation_window_seconds,
                sample_interval_seconds=sample_interval_seconds,
            )
            node_record["resource_profile"] = resource_profile
            _write_json(node_output_dir / "resource-profile.json", resource_profile)
        if "syscall_trace" in requested:
            syscall_trace = _observe_syscall_trace(
                runtime_metadata_path=runtime_metadata_path,
                target_node=node_id,
                output_dir=node_output_dir,
                target_host=target_host,
                connect_attempts=connect_attempts,
                startup_seconds=max(0.1, min(sample_interval_seconds, 1.0)),
                settle_seconds=max(0.1, min(sample_interval_seconds, 1.0)),
                strace_bin=strace_bin,
                sudo_bin=sudo_bin,
            )
            node_record["syscall_trace"] = syscall_trace
            _write_json(node_output_dir / "syscall-trace.json", syscall_trace)
        per_node[node_id] = node_record

    timeline = _correlated_timeline(per_node)
    _write_json(output_dir / "correlated-timeline.json", timeline)
    summary = _summarize_observation(per_node, resolved_node_ids, metadata)
    result = {
        "runtime_metadata_path": str(runtime_metadata_path),
        "node_ids": resolved_node_ids,
        "observation_window_seconds": observation_window_seconds,
        "sample_interval_seconds": sample_interval_seconds,
        "observation_primitives": requested,
        "network_magic": resolved_network_magic,
        "per_node": per_node,
        "summary": summary,
    }
    _write_json(output_dir / "observation-summary.json", result)
    return result


def _default_output_dir() -> Path:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    base = Path(run_dir) if run_dir else Path.cwd()
    return base / "outputs" / DEFAULT_OUTPUT_NAME


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Capture and correlate per-node observations for a multi-node runtime")
    parser.add_argument("--runtime-metadata-path", required=True)
    parser.add_argument("--node-id", action="append", dest="node_ids", default=[])
    parser.add_argument("--observation-window-seconds", type=float, default=5.0)
    parser.add_argument("--sample-interval-seconds", type=float, default=1.0)
    parser.add_argument("--observation", action="append", dest="observation_primitives", default=[])
    parser.add_argument("--network-magic", type=int, default=None)
    parser.add_argument("--cardano-cli", default="cardano-cli")
    parser.add_argument("--connect-attempts", type=int, default=2)
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--strace-bin", default="strace")
    parser.add_argument("--sudo-bin", default="sudo")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args(argv[1:])

    runtime_metadata_path = Path(args.runtime_metadata_path)
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()
    requested = args.observation_primitives or ["tip_state", "connection_state"]
    emit_target_event(
        primitive="runtime_multi_node_observation",
        event="multi_node_observation_started",
        payload={
            "runtime_metadata_path": str(runtime_metadata_path),
            "node_ids": args.node_ids,
            "observation_window_seconds": args.observation_window_seconds,
            "sample_interval_seconds": args.sample_interval_seconds,
            "observation_primitives": requested,
            "output_dir": str(output_dir),
        },
    )
    result = run_multi_node_observation(
        runtime_metadata_path=runtime_metadata_path,
        node_ids=args.node_ids,
        observation_window_seconds=args.observation_window_seconds,
        observation_primitives=requested,
        output_dir=output_dir,
        sample_interval_seconds=args.sample_interval_seconds,
        network_magic=args.network_magic,
        cardano_cli=args.cardano_cli,
        connect_attempts=args.connect_attempts,
        target_host=args.target_host,
        strace_bin=args.strace_bin,
        sudo_bin=args.sudo_bin,
    )
    summary = result["summary"]
    payload = {
        "node_count": summary["node_count"],
        "responsive_node_count": summary["responsive_node_count"],
        "tip_group_count": summary["tip_group_count"],
        "quorum_count": summary["quorum_count"],
        "quorum_fraction": summary["quorum_fraction"],
        "observation_summary_relpath": str(output_dir / "observation-summary.json"),
        "correlated_timeline_relpath": str(output_dir / "correlated-timeline.json"),
    }
    emit_target_event(
        primitive="runtime_multi_node_observation",
        event="multi_node_observation_completed",
        payload=payload,
        level="info",
    )
    print(
        "node_count={node_count} responsive_node_count={responsive_node_count} tip_group_count={tip_group_count} "
        "quorum_count={quorum_count} quorum_fraction={quorum_fraction:.4f} "
        "observation_summary_relpath={observation_summary_relpath} correlated_timeline_relpath={correlated_timeline_relpath}".format(
            **payload
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
