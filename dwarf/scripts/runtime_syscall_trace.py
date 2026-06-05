#!/usr/bin/env python3

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_telemetry import emit_target_event  # noqa: E402


SYSCALL_RE = re.compile(r"^\s*(?:\d+\s+)?([A-Za-z_][A-Za-z0-9_]*)\(")
DEFAULT_OUTPUT_NAME = "runtime-syscall-trace"


def _load_runtime_node(runtime_metadata_path: Path, target_node: str) -> dict:
    body = json.loads(runtime_metadata_path.read_text(encoding="utf-8"))
    nodes = body.get("haskell_nodes")
    if not isinstance(nodes, list):
        raise RuntimeError(f"runtime metadata does not contain haskell_nodes: {runtime_metadata_path}")
    for node in nodes:
        if node.get("name") == target_node:
            return node
    raise RuntimeError(f"runtime metadata missing target node {target_node!r}: {runtime_metadata_path}")


def _proc_exists(pid: int, proc_root: Path) -> bool:
    return (proc_root / str(pid) / "status").exists()


def _pid_matches_node(pid: int, node: dict, proc_root: Path) -> bool:
    status_path = proc_root / str(pid) / "status"
    if not status_path.exists():
        return False
    status_text = status_path.read_text(encoding="utf-8", errors="replace")
    if "Name:\tcardano-node" not in status_text:
        return False
    return True


def _scan_for_runtime_process(node: dict) -> dict:
    result = subprocess.run(
        ["ps", "-eo", "pid=,comm=,args="],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError("runtime_syscall_trace could not inspect process table")
    name = str(node.get("name") or "")
    socket_hint = str(node.get("socket_path") or "")
    db_hint = str(node.get("db_dir") or "")
    legacy_socket_hint = f"socket/{name}/sock" if name else ""
    legacy_db_hint = f"node-data/{name}/db" if name else ""
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
        socket_match = socket_hint and socket_hint in args
        db_match = db_hint and db_hint in args
        legacy_socket_match = legacy_socket_hint and legacy_socket_hint in args
        legacy_db_match = legacy_db_hint and legacy_db_hint in args
        if not any((socket_match, db_match, legacy_socket_match, legacy_db_match)):
            continue
        port_match = re.search(r"--port\s+(\d+)", args)
        port = int(port_match.group(1)) if port_match else int(node.get("port") or 0)
        return {"pid": int(pid_text), "port": port, "node": node}
    raise RuntimeError(f"runtime_syscall_trace could not resolve runtime process for node {name!r}")


def resolve_target_process(runtime_metadata_path: Path, target_node: str, *, proc_root: Path = Path("/proc")) -> dict:
    node = _load_runtime_node(runtime_metadata_path, target_node)
    pid_file = node.get("pid_file")
    if isinstance(pid_file, str) and pid_file:
        try:
            pid = int(Path(pid_file).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = None
        if pid is not None and _proc_exists(pid, proc_root) and _pid_matches_node(pid, node, proc_root):
            port = int(node.get("port") or 0)
            return {"pid": pid, "port": port, "node": node}
    return _scan_for_runtime_process(node)


def summarize_trace(trace_path: Path) -> dict:
    counts: Counter[str] = Counter()
    for line in trace_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = SYSCALL_RE.match(line)
        if not match:
            continue
        counts[match.group(1)] += 1
    ordered_counts = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    top_syscall = ""
    top_count = 0
    if ordered_counts:
        top_syscall, top_count = ordered_counts[0]
    return {
        "total_syscalls": int(sum(counts.values())),
        "unique_syscalls": int(len(counts)),
        "top_syscall": top_syscall,
        "top_syscall_count": int(top_count),
        "syscall_counts": dict(ordered_counts),
    }


def _write_summary(output_dir: Path, summary: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def run_trace(
    *,
    runtime_metadata_path: Path,
    target_node: str,
    output_dir: Path,
    connect_attempts: int,
    target_host: str,
    startup_seconds: float,
    settle_seconds: float,
    strace_bin: str,
    sudo_bin: str,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = resolve_target_process(runtime_metadata_path, target_node)
    trace_path = output_dir / "trace.log"
    trace_cmd = [
        sudo_bin,
        "-n",
        strace_bin,
        "-qq",
        "-f",
        "-p",
        str(target["pid"]),
        "-o",
        str(trace_path),
    ]
    trace_proc = subprocess.Popen(
        trace_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    try:
        time.sleep(startup_seconds)
        connect_successes = 0
        for _ in range(connect_attempts):
            with socket.create_connection((target_host, target["port"]), timeout=1.0):
                connect_successes += 1
        time.sleep(settle_seconds)
    finally:
        trace_proc.send_signal(signal.SIGINT)
        try:
            trace_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            trace_proc.kill()
            trace_proc.wait(timeout=5)

    summary = summarize_trace(trace_path) if trace_path.exists() else {
        "total_syscalls": 0,
        "unique_syscalls": 0,
        "top_syscall": "",
        "top_syscall_count": 0,
        "syscall_counts": {},
    }
    _write_summary(output_dir, summary)
    return {
        "target_node": target_node,
        "pid": int(target["pid"]),
        "port": int(target["port"]),
        "connect_successes": int(connect_successes),
        "trace_path": str(trace_path),
        **summary,
    }


def _default_output_dir() -> Path:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir) / "outputs" / DEFAULT_OUTPUT_NAME
    return Path.cwd() / "outputs" / DEFAULT_OUTPUT_NAME


def _relative_to_run(path: Path) -> str:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        try:
            return str(path.relative_to(Path(run_dir)))
        except ValueError:
            pass
    return str(path)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Capture bounded syscall trace for a target runtime process")
    parser.add_argument("--runtime-metadata-path", required=True)
    parser.add_argument("--target-node", required=True)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--connect-attempts", type=int, default=3)
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--startup-seconds", type=float, default=1.0)
    parser.add_argument("--settle-seconds", type=float, default=0.5)
    parser.add_argument("--strace-bin", default="strace")
    parser.add_argument("--sudo-bin", default="sudo")
    args = parser.parse_args(argv[1:])

    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()
    runtime_metadata_path = Path(args.runtime_metadata_path)
    emit_target_event(
        primitive="runtime_syscall_trace",
        event="syscall_trace_started",
        payload={
            "runtime_metadata_path": str(runtime_metadata_path),
            "target_node": args.target_node,
            "output_dir": str(output_dir),
            "connect_attempts": args.connect_attempts,
            "target_host": args.target_host,
        },
    )
    result = run_trace(
        runtime_metadata_path=runtime_metadata_path,
        target_node=args.target_node,
        output_dir=output_dir,
        connect_attempts=args.connect_attempts,
        target_host=args.target_host,
        startup_seconds=args.startup_seconds,
        settle_seconds=args.settle_seconds,
        strace_bin=args.strace_bin,
        sudo_bin=args.sudo_bin,
    )
    result["summary_relpath"] = _relative_to_run(output_dir / "summary.json")
    result["trace_relpath"] = _relative_to_run(Path(result["trace_path"]))
    emit_target_event(
        primitive="runtime_syscall_trace",
        event="syscall_trace_completed",
        payload=result,
        level="info" if result["total_syscalls"] > 0 and result["connect_successes"] > 0 else "error",
    )
    print(
        "target_node={target_node} pid={pid} port={port} total_syscalls={total_syscalls} "
        "unique_syscalls={unique_syscalls} connect_successes={connect_successes} "
        "top_syscall={top_syscall} summary_relpath={summary_relpath} trace_relpath={trace_relpath}".format(**result)
    )
    return 0 if result["total_syscalls"] > 0 and result["connect_successes"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
