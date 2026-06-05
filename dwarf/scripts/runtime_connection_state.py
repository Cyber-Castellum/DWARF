#!/usr/bin/env python3

import argparse
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_telemetry import emit_target_event  # noqa: E402


DEFAULT_OUTPUT_NAME = "runtime-connection-state"


def _load_runtime_node(runtime_metadata_path: Path, target_node: str) -> dict:
    body = json.loads(runtime_metadata_path.read_text(encoding="utf-8"))
    nodes = body.get("nodes")
    if not isinstance(nodes, list):
        haskell_nodes = body.get("haskell_nodes")
        amaru_nodes = body.get("amaru_nodes")
        if isinstance(haskell_nodes, list) and isinstance(amaru_nodes, list):
            nodes = [*haskell_nodes, *amaru_nodes]
        elif isinstance(haskell_nodes, list):
            nodes = haskell_nodes
        else:
            raise RuntimeError(f"runtime metadata does not contain nodes or haskell_nodes: {runtime_metadata_path}")
    for node in nodes:
        if node.get("name") == target_node or node.get("id") == target_node:
            return node
    raise RuntimeError(f"runtime metadata missing target node {target_node!r}: {runtime_metadata_path}")


def _proc_exists(pid: int, proc_root: Path) -> bool:
    return (proc_root / str(pid) / "status").exists()


def _pid_matches_node(pid: int, proc_root: Path) -> bool:
    status_path = proc_root / str(pid) / "status"
    if not status_path.exists():
        return False
    status_text = status_path.read_text(encoding="utf-8", errors="replace")
    return "Name:\tcardano-node" in status_text


def _scan_for_runtime_process(node: dict) -> dict:
    result = subprocess.run(
        ["ps", "-eo", "pid=,comm=,args="],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError("runtime_connection_state could not inspect process table")
    name = str(node.get("name") or "")
    socket_hint = str(node.get("socket_path") or "")
    db_hint = str(node.get("db_dir") or "")
    runtime_port = int(node.get("port") or 0)
    legacy_socket_hint = f"socket/{name}/sock" if name else ""
    legacy_db_hint = f"node-data/{name}/db" if name else ""
    candidates = []
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
        port_match = re.search(r"--port\s+(\d+)", args)
        port = int(port_match.group(1)) if port_match else runtime_port
        score = None
        if socket_hint and socket_hint in args:
            score = 0
        elif runtime_port and port == runtime_port:
            score = 1
        elif legacy_socket_hint and legacy_socket_hint in args and runtime_port and port == runtime_port:
            score = 2
        elif db_hint and db_hint in args and runtime_port and port == runtime_port:
            score = 3
        elif legacy_db_hint and legacy_db_hint in args and runtime_port and port == runtime_port:
            score = 4
        if score is None:
            continue
        candidates.append((score, int(pid_text), port))
    if candidates:
        _, pid, port = sorted(candidates)[0]
        return {"pid": pid, "port": port, "node": node}
    raise RuntimeError(f"runtime_connection_state could not resolve runtime process for node {name!r}")


def _resolve_docker_target_process(node: dict, *, proc_root: Path) -> dict | None:
    container_name = str(node.get("container_name") or "")
    if not container_name:
        return None
    result = subprocess.run(
        ["docker", "inspect", container_name],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        return None
    try:
        body = json.loads(result.stdout or "[]")[0]
        pid = int(((body.get("State") or {}).get("Pid")) or 0)
    except (IndexError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if pid and _proc_exists(pid, proc_root):
        return {"pid": pid, "port": int(node.get("port") or 0), "node": node}
    return None


def resolve_target_process(runtime_metadata_path: Path, target_node: str, *, proc_root: Path = Path("/proc")) -> dict:
    node = _load_runtime_node(runtime_metadata_path, target_node)
    docker_target = _resolve_docker_target_process(node, proc_root=proc_root)
    if docker_target is not None:
        return docker_target
    pid_file = node.get("pid_file")
    if isinstance(pid_file, str) and pid_file:
        try:
            pid = int(Path(pid_file).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = None
        if pid is not None and _proc_exists(pid, proc_root) and _pid_matches_node(pid, proc_root):
            return {"pid": pid, "port": int(node.get("port") or 0), "node": node}
    return _scan_for_runtime_process(node)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _filter_ss(stdout: str, *, pid: int, port: int) -> str:
    kept = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if f"pid={pid}," in line or f":{port} " in line or line.endswith(f":{port}"):
            kept.append(line)
    return "\n".join(kept) + ("\n" if kept else "")


def summarize_snapshot(output_dir: Path) -> dict:
    ss_text = (output_dir / "ss.txt").read_text(encoding="utf-8", errors="replace") if (output_dir / "ss.txt").exists() else ""
    lsof_text = (output_dir / "lsof.txt").read_text(encoding="utf-8", errors="replace") if (output_dir / "lsof.txt").exists() else ""
    ss_lines = [line.strip() for line in ss_text.splitlines() if line.strip()]
    lsof_lines = [line.strip() for line in lsof_text.splitlines() if line.strip()]
    return {
        "ss_match_count": len(ss_lines),
        "ss_listen_count": sum(1 for line in ss_lines if line.startswith("LISTEN")),
        "ss_established_count": sum(1 for line in ss_lines if line.startswith("ESTAB")),
        "lsof_socket_count": max(0, len(lsof_lines) - 1),
    }


def run_snapshot(
    *,
    runtime_metadata_path: Path,
    target_node: str,
    output_dir: Path,
    snapshot_name: str,
    target_host: str,
    connect_attempts: int,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = resolve_target_process(runtime_metadata_path, target_node)

    connect_successes = 0
    connect_failures = 0
    for _ in range(connect_attempts):
        try:
            with socket.create_connection((target_host, target["port"]), timeout=1.0):
                connect_successes += 1
        except OSError:
            connect_failures += 1

    ss_proc = subprocess.run(
        ["ss", "-tanp"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if ss_proc.returncode != 0:
        raise RuntimeError("runtime_connection_state could not capture ss output")
    filtered_ss = _filter_ss(ss_proc.stdout or "", pid=int(target["pid"]), port=int(target["port"]))
    _write_text(output_dir / "ss.txt", filtered_ss)

    lsof_proc = subprocess.run(
        ["lsof", "-w", "-nP", "-p", str(target["pid"]), "-a", "-iTCP", "-iUDP"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    lsof_text = (lsof_proc.stdout or "") if lsof_proc.returncode in (0, 1) else ""
    _write_text(output_dir / "lsof.txt", lsof_text)

    summary = summarize_snapshot(output_dir)
    summary.update(
        {
            "snapshot_name": snapshot_name,
            "target_node": target_node,
            "pid": int(target["pid"]),
            "port": int(target["port"]),
            "connect_successes": int(connect_successes),
            "connect_failures": int(connect_failures),
        }
    )
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def _default_output_dir(snapshot_name: str) -> Path:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    base = Path(run_dir) if run_dir else Path.cwd()
    return base / "outputs" / DEFAULT_OUTPUT_NAME / snapshot_name


def _relative_to_run(path: Path) -> str:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        try:
            return str(path.relative_to(Path(run_dir)))
        except ValueError:
            pass
    return str(path)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Capture bounded connection-state snapshots for a target runtime process")
    parser.add_argument("--runtime-metadata-path", required=True)
    parser.add_argument("--target-node", required=True)
    parser.add_argument("--snapshot-name", required=True)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--connect-attempts", type=int, default=1)
    args = parser.parse_args(argv[1:])

    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir(args.snapshot_name)
    runtime_metadata_path = Path(args.runtime_metadata_path)
    emit_target_event(
        primitive="runtime_connection_state",
        event="connection_state_started",
        payload={
            "runtime_metadata_path": str(runtime_metadata_path),
            "target_node": args.target_node,
            "snapshot_name": args.snapshot_name,
            "target_host": args.target_host,
            "connect_attempts": args.connect_attempts,
            "output_dir": str(output_dir),
        },
    )
    result = run_snapshot(
        runtime_metadata_path=runtime_metadata_path,
        target_node=args.target_node,
        output_dir=output_dir,
        snapshot_name=args.snapshot_name,
        target_host=args.target_host,
        connect_attempts=args.connect_attempts,
    )
    result["summary_relpath"] = _relative_to_run(output_dir / "summary.json")
    result["ss_relpath"] = _relative_to_run(output_dir / "ss.txt")
    result["lsof_relpath"] = _relative_to_run(output_dir / "lsof.txt")
    emit_target_event(
        primitive="runtime_connection_state",
        event="connection_state_completed",
        payload=result,
        level="info",
    )
    print(
        "snapshot_name={snapshot_name} target_node={target_node} pid={pid} port={port} "
        "ss_match_count={ss_match_count} ss_listen_count={ss_listen_count} "
        "ss_established_count={ss_established_count} lsof_socket_count={lsof_socket_count} "
        "connect_successes={connect_successes} connect_failures={connect_failures} "
        "summary_relpath={summary_relpath} ss_relpath={ss_relpath} lsof_relpath={lsof_relpath}".format(**result)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
