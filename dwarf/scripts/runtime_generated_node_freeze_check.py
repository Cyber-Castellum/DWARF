#!/usr/bin/env python3

import json
import os
import subprocess
import sys
import time
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


def _ps(*args):
    return subprocess.run(
        ["ps", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


def _process_state(pid: int) -> str:
    result = _ps("-o", "stat=", "-p", str(pid))
    if result.returncode != 0:
        raise RuntimeError(f"could not inspect process state for pid {pid}")
    state = result.stdout.strip()
    if not state:
        raise RuntimeError(f"empty process state for pid {pid}")
    return state


def _process_comm_and_args(pid: int):
    result = _ps("-o", "comm=,args=", "-p", str(pid))
    if result.returncode != 0:
        return None, None
    line = result.stdout.strip()
    if not line:
        return None, None
    parts = line.split(None, 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _resolve_node_pid(node: dict) -> int:
    pid_file = node.get("pid_file")
    socket_path = str(node.get("socket_path") or "")
    port = node.get("port")
    if isinstance(pid_file, str) and pid_file:
        try:
            pid = int(Path(pid_file).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = None
        if pid is not None:
            comm, args = _process_comm_and_args(pid)
            if comm == "cardano-node" and ((socket_path and socket_path in args) or (port is not None and f"--port {int(port)}" in args)):
                return pid
    result = _ps("-eo", "pid=,comm=,args=")
    if result.returncode != 0:
        raise RuntimeError("could not inspect process table")
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_text, comm, args = parts
        if comm != "cardano-node":
            continue
        if socket_path and socket_path in args:
            return int(pid_text)
        if port is not None and f"--port {int(port)}" in args:
            return int(pid_text)
    raise RuntimeError(f"could not resolve cardano-node pid for node {node.get('name')!r}")


def _listener_ok(host: str, port: int) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _log_size_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _emit_probe(*, node: str, pid: int, process_state: str, listener_ok: bool, log_delta_bytes: int, role: str):
    stopped = 1 if "T" in process_state else 0
    emit_runtime_metric("generated_node_freeze_process_stopped", value=stopped, meta={"node": node, "role": role})
    emit_runtime_metric("generated_node_freeze_listener_ok", value=1 if listener_ok else 0, meta={"node": node, "role": role})
    emit_runtime_metric("generated_node_freeze_log_delta_bytes", value=log_delta_bytes, meta={"node": node, "role": role})
    emit_target_event(
        primitive="runtime_generated_node_freeze_check",
        event="node_freeze_probe",
        payload={
            "node": node,
            "pid": pid,
            "process_state": process_state,
            "listener_ok": listener_ok,
            "log_delta_bytes": log_delta_bytes,
            "role": role,
        },
        level="info",
    )


def _phase_completed(run_dir: Path, phase_id: str) -> bool:
    log_path = run_dir / "log.ndjson"
    if not log_path.exists():
        return False
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("event") != "phase_completed":
            continue
        payload = entry.get("payload") or {}
        if payload.get("phase_id") == phase_id:
            return True
    return False


def run_node_freeze_check(*, runtime_root: Path, blocked_node: str, healthy_nodes: list[str], sample_seconds: float = 2.0) -> int:
    nodes = _runtime_json_nodes(runtime_root)
    if blocked_node not in nodes:
        raise RuntimeError(f"blocked node {blocked_node!r} not present in runtime metadata")
    missing_healthy = [name for name in healthy_nodes if name not in nodes]
    if missing_healthy:
        raise RuntimeError(f"healthy node(s) missing from runtime metadata: {missing_healthy}")

    selected = {blocked_node: nodes[blocked_node], **{name: nodes[name] for name in healthy_nodes}}
    starts = {name: _log_size_bytes(Path(node["log_path"])) for name, node in selected.items()}
    time.sleep(sample_seconds)

    healthy_passes = 0
    blocked_stopped = False
    for name, node in selected.items():
        pid = _resolve_node_pid(node)
        process_state = _process_state(pid)
        listener_ok = _listener_ok("127.0.0.1", int(node["port"]))
        log_delta_bytes = _log_size_bytes(Path(node["log_path"])) - starts[name]
        role = "blocked" if name == blocked_node else "healthy"
        _emit_probe(
            node=name,
            pid=pid,
            process_state=process_state,
            listener_ok=listener_ok,
            log_delta_bytes=log_delta_bytes,
            role=role,
        )
        if name == blocked_node:
            if "T" not in process_state:
                raise RuntimeError(f"expected blocked node {name} pid {pid} to be stopped, saw {process_state!r}")
            blocked_stopped = True
        else:
            if "T" in process_state:
                raise RuntimeError(f"expected healthy node {name} pid {pid} to remain runnable, saw {process_state!r}")
            if not listener_ok:
                raise RuntimeError(f"expected healthy node {name} listener to remain reachable on port {node['port']}")
            healthy_passes += 1

    emit_runtime_metric("generated_node_freeze_healthy_passes", value=healthy_passes, meta={"blocked_node": blocked_node})
    emit_target_event(
        primitive="runtime_generated_node_freeze_check",
        event="node_freeze_summary",
        payload={
            "blocked_node": blocked_node,
            "blocked_stopped": blocked_stopped,
            "healthy_nodes": healthy_nodes,
            "healthy_passes": healthy_passes,
        },
    )
    print(
        f"blocked_node={blocked_node} blocked_stopped={str(blocked_stopped).lower()} "
        f"healthy_nodes={','.join(healthy_nodes)} healthy_passes={healthy_passes}"
    )
    return 0


def run_node_recovery_check(*, runtime_root: Path, recovered_node: str, healthy_nodes: list[str], required_phase_id: str, sample_seconds: float = 2.0) -> int:
    nodes = _runtime_json_nodes(runtime_root)
    if recovered_node not in nodes:
        raise RuntimeError(f"recovered node {recovered_node!r} not present in runtime metadata")
    missing_healthy = [name for name in healthy_nodes if name not in nodes]
    if missing_healthy:
        raise RuntimeError(f"healthy node(s) missing from runtime metadata: {missing_healthy}")

    run_dir_env = os.environ.get("ADA2_DWARF_RUN_DIR")
    if not run_dir_env:
        raise RuntimeError("ADA2_DWARF_RUN_DIR is required for node-recovery-check")
    run_dir = Path(run_dir_env)
    if not _phase_completed(run_dir, required_phase_id):
        raise RuntimeError(f"required prior phase completion not found: {required_phase_id}")

    selected = {recovered_node: nodes[recovered_node], **{name: nodes[name] for name in healthy_nodes}}
    starts = {name: _log_size_bytes(Path(node["log_path"])) for name, node in selected.items()}
    time.sleep(sample_seconds)

    healthy_passes = 0
    recovered_ok = False
    for name, node in selected.items():
        pid = _resolve_node_pid(node)
        process_state = _process_state(pid)
        listener_ok = _listener_ok("127.0.0.1", int(node["port"]))
        log_delta_bytes = _log_size_bytes(Path(node["log_path"])) - starts[name]
        role = "recovered" if name == recovered_node else "healthy"
        _emit_probe(
            node=name,
            pid=pid,
            process_state=process_state,
            listener_ok=listener_ok,
            log_delta_bytes=log_delta_bytes,
            role=role,
        )
        if name == recovered_node:
            if "T" in process_state:
                raise RuntimeError(f"expected recovered node {name} pid {pid} to be runnable, saw {process_state!r}")
            if not listener_ok:
                raise RuntimeError(f"expected recovered node {name} listener to be reachable on port {node['port']}")
            recovered_ok = True
        else:
            if "T" in process_state:
                raise RuntimeError(f"expected healthy node {name} pid {pid} to remain runnable, saw {process_state!r}")
            if not listener_ok:
                raise RuntimeError(f"expected healthy node {name} listener to remain reachable on port {node['port']}")
            healthy_passes += 1

    emit_runtime_metric("generated_node_recovery_ok", value=1 if recovered_ok else 0, meta={"node": recovered_node})
    emit_runtime_metric("generated_node_recovery_healthy_passes", value=healthy_passes, meta={"recovered_node": recovered_node})
    emit_target_event(
        primitive="runtime_generated_node_freeze_check",
        event="node_recovery_summary",
        payload={
            "recovered_node": recovered_node,
            "recovered_ok": recovered_ok,
            "healthy_nodes": healthy_nodes,
            "healthy_passes": healthy_passes,
            "required_phase_id": required_phase_id,
        },
    )
    print(
        f"recovered_node={recovered_node} recovered_ok={str(recovered_ok).lower()} "
        f"healthy_nodes={','.join(healthy_nodes)} healthy_passes={healthy_passes} "
        f"required_phase_id={required_phase_id}"
    )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in {"node-freeze-check", "node-recovery-check"}:
        print(
            "usage: runtime_generated_node_freeze_check.py "
            "node-freeze-check --runtime-root PATH --blocked-node NAME --healthy-nodes node1,node3 [--sample-seconds 2]\n"
            "   or: runtime_generated_node_freeze_check.py "
            "node-recovery-check --runtime-root PATH --recovered-node NAME --healthy-nodes node1,node3 "
            "--required-phase-id freeze-node2 [--sample-seconds 2]",
            file=sys.stderr,
        )
        return 2
    mode = argv[1]
    runtime_root = None
    blocked_node = None
    recovered_node = None
    healthy_nodes = None
    required_phase_id = None
    sample_seconds = 2.0
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
        if argv[i] == "--recovered-node" and i + 1 < len(argv):
            recovered_node = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--healthy-nodes" and i + 1 < len(argv):
            healthy_nodes = [part for part in argv[i + 1].split(",") if part]
            i += 2
            continue
        if argv[i] == "--required-phase-id" and i + 1 < len(argv):
            required_phase_id = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--sample-seconds" and i + 1 < len(argv):
            sample_seconds = float(argv[i + 1])
            i += 2
            continue
        print(f"unknown argument: {argv[i]}", file=sys.stderr)
        return 2
    if mode == "node-freeze-check" and (runtime_root is None or blocked_node is None or healthy_nodes is None):
        print(
            "node-freeze-check requires --runtime-root, --blocked-node, and --healthy-nodes",
            file=sys.stderr,
        )
        return 2
    if mode == "node-recovery-check" and (
        runtime_root is None or recovered_node is None or healthy_nodes is None or required_phase_id is None
    ):
        print(
            "node-recovery-check requires --runtime-root, --recovered-node, --healthy-nodes, and --required-phase-id",
            file=sys.stderr,
        )
        return 2
    if mode == "node-freeze-check":
        return run_node_freeze_check(
            runtime_root=runtime_root,
            blocked_node=blocked_node,
            healthy_nodes=healthy_nodes,
            sample_seconds=sample_seconds,
        )
    return run_node_recovery_check(
        runtime_root=runtime_root,
        recovered_node=recovered_node,
        healthy_nodes=healthy_nodes,
        required_phase_id=required_phase_id,
        sample_seconds=sample_seconds,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv))
