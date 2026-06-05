#!/usr/bin/env python3

import json
import socket
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_telemetry import emit_runtime_metric, emit_target_event  # noqa: E402


def _runtime_json_nodes(runtime_root: Path) -> dict[str, dict]:
    runtime_json = runtime_root / "runtime.json"
    if not runtime_json.exists():
        raise RuntimeError(f"missing runtime metadata: {runtime_json}")
    body = json.loads(runtime_json.read_text(encoding="utf-8"))
    nodes = body.get("haskell_nodes")
    if not isinstance(nodes, list) or not nodes:
        raise RuntimeError(f"runtime metadata does not contain haskell_nodes: {runtime_json}")
    by_name = {}
    for node in nodes:
        name = node.get("name")
        port = node.get("port")
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(port, int):
            raise RuntimeError(f"runtime metadata node {name!r} has invalid port: {port!r}")
        by_name[name] = dict(node)
    if not by_name:
        raise RuntimeError(f"runtime metadata did not yield any named nodes: {runtime_json}")
    return by_name


def _listener_ok(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _emit_probe(*, node: str, port: int, listener_ok: bool, role: str) -> None:
    payload = {
        "node": node,
        "port": port,
        "listener_ok": listener_ok,
        "role": role,
    }
    emit_runtime_metric(
        "generated_node_port_drop_listener_port",
        value=port,
        meta={"node": node, "role": role},
    )
    emit_runtime_metric(
        "generated_node_port_drop_listener_ok",
        value=1 if listener_ok else 0,
        meta={"node": node, "role": role},
    )
    emit_target_event(
        primitive="runtime_generated_node_port_fault_check",
        event="node_probe",
        payload=payload,
        level="info" if (role == "blocked" and not listener_ok) or (role == "healthy" and listener_ok) else "error",
    )


def run_node_port_drop_check(*, runtime_root: Path, blocked_node: str, healthy_nodes: list[str]) -> int:
    nodes = _runtime_json_nodes(runtime_root)
    if blocked_node not in nodes:
        raise RuntimeError(f"blocked node {blocked_node!r} not present in runtime metadata")
    missing_healthy = [name for name in healthy_nodes if name not in nodes]
    if missing_healthy:
        raise RuntimeError(f"healthy node(s) missing from runtime metadata: {missing_healthy}")

    blocked = nodes[blocked_node]
    blocked_ok = _listener_ok("127.0.0.1", int(blocked["port"]))
    _emit_probe(node=blocked_node, port=int(blocked["port"]), listener_ok=blocked_ok, role="blocked")
    if blocked_ok:
        raise RuntimeError(f"expected blocked node {blocked_node} listener to fail on port {blocked['port']}")

    healthy_passes = 0
    for name in healthy_nodes:
        node = nodes[name]
        listener_ok = _listener_ok("127.0.0.1", int(node["port"]))
        _emit_probe(node=name, port=int(node["port"]), listener_ok=listener_ok, role="healthy")
        if not listener_ok:
            raise RuntimeError(f"expected healthy node {name} listener to remain reachable on port {node['port']}")
        healthy_passes += 1

    emit_runtime_metric(
        "generated_node_port_drop_healthy_passes",
        value=healthy_passes,
        meta={"blocked_node": blocked_node},
    )
    emit_target_event(
        primitive="runtime_generated_node_port_fault_check",
        event="node_port_drop_summary",
        payload={
            "blocked_node": blocked_node,
            "healthy_nodes": healthy_nodes,
            "healthy_passes": healthy_passes,
        },
    )
    print(
        f"blocked_node={blocked_node} blocked_port={blocked['port']} blocked_listener_ok=false "
        f"healthy_nodes={','.join(healthy_nodes)} healthy_passes={healthy_passes}"
    )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] != "node-port-drop-check":
        print(
            "usage: runtime_generated_node_port_fault_check.py node-port-drop-check "
            "--runtime-root PATH --blocked-node NAME --healthy-nodes node1,node3",
            file=sys.stderr,
        )
        return 2
    runtime_root = None
    blocked_node = None
    healthy_nodes = None
    i = 2
    while i < len(argv):
        if argv[i] == "--runtime-root" and i + 1 < len(argv):
            runtime_root = Path(argv[i + 1])
            i += 2
            continue
        if argv[i] == "--blocked-node" and i + 1 < len(argv):
            blocked_node = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--healthy-nodes" and i + 1 < len(argv):
            healthy_nodes = [part for part in argv[i + 1].split(",") if part]
            i += 2
            continue
        print(f"unknown argument: {argv[i]}", file=sys.stderr)
        return 2
    if runtime_root is None or blocked_node is None or healthy_nodes is None:
        print(
            "node-port-drop-check requires --runtime-root, --blocked-node, and --healthy-nodes",
            file=sys.stderr,
        )
        return 2
    return run_node_port_drop_check(
        runtime_root=runtime_root,
        blocked_node=blocked_node,
        healthy_nodes=healthy_nodes,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv))
