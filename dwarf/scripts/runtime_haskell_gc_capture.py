#!/usr/bin/env python3

import argparse
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_connection_state import resolve_target_process  # noqa: E402
from runtime_telemetry import emit_target_event  # noqa: E402


DEFAULT_OUTPUT_NAME = "runtime-haskell-gc-capture"
BYTE_RE = re.compile(r"^\s*([\d,]+)\s+bytes\s+(allocated in the heap|copied during GC|maximum residency)")
MEMORY_RE = re.compile(r"^\s*([\d,]+)\s+MiB total memory in use")
GEN_RE = re.compile(r"^\s*Gen\s+(\d+)\s+([\d,]+)\s+colls,.*?([0-9.]+)s\s*$")
TIME_RE = re.compile(r"^\s*(INIT|MUT|GC|EXIT|Total)\s+time\s+([0-9.]+)s")
PRODUCTIVITY_RE = re.compile(r"^\s*Productivity\s+([0-9.]+)% of total user,\s+([0-9.]+)% of total elapsed")


def _load_runtime_node(runtime_metadata_path: Path, target_node: str) -> dict:
    body = json.loads(runtime_metadata_path.read_text(encoding="utf-8"))
    nodes = body.get("haskell_nodes")
    if not isinstance(nodes, list):
        raise RuntimeError(f"runtime metadata does not contain haskell_nodes: {runtime_metadata_path}")
    for node in nodes:
        if node.get("name") == target_node:
            return dict(node)
    raise RuntimeError(f"runtime metadata missing target node {target_node!r}: {runtime_metadata_path}")


def _ps_args(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "args="],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"could not resolve command line for pid {pid}")
    return result.stdout.strip()


def _session_exists(session: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return result.returncode == 0


def _kill_session(session: str) -> None:
    if not _session_exists(session):
        return
    result = subprocess.run(
        ["tmux", "kill-session", "-t", session],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0 and _session_exists(session):
        raise RuntimeError(result.stderr.strip() or f"failed to kill tmux session {session}")


def _start_session(session: str, command: str) -> None:
    result = subprocess.run(
        ["tmux", "new-session", "-d", "-s", session, "bash", "-lc", command],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"failed to start tmux session {session}")


def _wait_for_session_exit(session: str, timeout_seconds: float) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _session_exists(session):
            return
        time.sleep(0.5)
    raise RuntimeError(f"timed out waiting for tmux session {session} to exit")


def _wait_for_pid_exit(pid: int, timeout_seconds: float) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not Path(f"/proc/{pid}/status").exists():
            return
        time.sleep(0.5)
    raise RuntimeError(f"timed out waiting for pid {pid} to exit")


def _wait_for_listener(host: str, port: int, timeout_seconds: float) -> None:
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"timed out waiting for listener {host}:{port}: {last_error}")


def _wait_for_restarted_process(runtime_metadata_path: Path, target_node: str, previous_pid: int, timeout_seconds: float) -> dict:
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            target = resolve_target_process(runtime_metadata_path, target_node)
            if int(target["pid"]) != previous_pid:
                return target
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(
        f"timed out waiting for restarted process for {target_node}: previous_pid={previous_pid} last_error={last_error}"
    )


def _connect_burst(host: str, port: int, connect_attempts: int) -> tuple[int, int]:
    successes = 0
    failures = 0
    for _ in range(connect_attempts):
        try:
            with socket.create_connection((host, port), timeout=1.0):
                successes += 1
        except OSError:
            failures += 1
        time.sleep(0.1)
    return successes, failures


def _signal_process_for_rts_exit(pid: int) -> None:
    os.kill(pid, signal.SIGINT)


def _signal_process_for_shutdown(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGINT)
    except ProcessLookupError:
        return


def _tail_after_offset(path: Path, offset: int) -> str:
    if not path.exists():
        return ""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        return handle.read()


def _parse_rts_stats(text: str) -> dict:
    summary = {
        "total_bytes_allocated": 0,
        "bytes_copied_during_gc": 0,
        "max_residency_bytes": 0,
        "total_memory_in_use_mib": 0,
        "gen_0_collections": 0,
        "gen_1_collections": 0,
        "max_pause_seconds": 0.0,
        "mutator_cpu_seconds": 0.0,
        "gc_cpu_seconds": 0.0,
        "total_cpu_seconds": 0.0,
        "productivity_user_pct": 0.0,
        "productivity_elapsed_pct": 0.0,
    }
    for line in text.splitlines():
        byte_match = BYTE_RE.match(line)
        if byte_match:
            value = int(byte_match.group(1).replace(",", ""))
            label = byte_match.group(2)
            if label == "allocated in the heap":
                summary["total_bytes_allocated"] = value
            elif label == "copied during GC":
                summary["bytes_copied_during_gc"] = value
            elif label == "maximum residency":
                summary["max_residency_bytes"] = value
            continue
        memory_match = MEMORY_RE.match(line)
        if memory_match:
            summary["total_memory_in_use_mib"] = int(memory_match.group(1).replace(",", ""))
            continue
        gen_match = GEN_RE.match(line)
        if gen_match:
            generation = int(gen_match.group(1))
            colls = int(gen_match.group(2).replace(",", ""))
            max_pause = float(gen_match.group(3))
            summary[f"gen_{generation}_collections"] = colls
            summary["max_pause_seconds"] = max(summary["max_pause_seconds"], max_pause)
            continue
        time_match = TIME_RE.match(line)
        if time_match:
            label = time_match.group(1)
            value = float(time_match.group(2))
            if label == "MUT":
                summary["mutator_cpu_seconds"] = value
            elif label == "GC":
                summary["gc_cpu_seconds"] = value
            elif label == "Total":
                summary["total_cpu_seconds"] = value
            continue
        productivity_match = PRODUCTIVITY_RE.match(line)
        if productivity_match:
            summary["productivity_user_pct"] = float(productivity_match.group(1))
            summary["productivity_elapsed_pct"] = float(productivity_match.group(2))
    return summary


def _capture_session_command(node: dict, args: str, gc_log_path: Path) -> str:
    pid_file = shlex.quote(str(node["pid_file"]))
    stdout_log = shlex.quote(str(node["log_path"]))
    gc_log = shlex.quote(str(gc_log_path))
    return (
        f"echo $$ > {pid_file}; "
        f"export GHCRTS=-s; "
        f"exec {args} >> {stdout_log} 2>> {gc_log}"
    )


def _restore_session_command(node: dict, args: str) -> str:
    pid_file = shlex.quote(str(node["pid_file"]))
    stdout_log = shlex.quote(str(node["log_path"]))
    return (
        f"echo $$ > {pid_file}; "
        f"exec {args} >> {stdout_log} 2>&1"
    )


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


def run_gc_capture(
    *,
    runtime_metadata_path: Path,
    target_node: str,
    output_dir: Path,
    target_host: str,
    connect_attempts: int,
    sample_seconds: float,
    startup_timeout_seconds: float,
    restore_timeout_seconds: float,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    node = _load_runtime_node(runtime_metadata_path, target_node)
    session = str(node["session"])
    initial = resolve_target_process(runtime_metadata_path, target_node)
    args = _ps_args(int(initial["pid"]))
    gc_log_path = output_dir / "capture.stderr.log"
    gc_log_path.touch(exist_ok=True)
    gc_log_offset = gc_log_path.stat().st_size

    _signal_process_for_shutdown(int(initial["pid"]))
    _wait_for_pid_exit(int(initial["pid"]), timeout_seconds=startup_timeout_seconds)
    _kill_session(session)
    _start_session(session, _capture_session_command(node, args, gc_log_path))
    capture = _wait_for_restarted_process(
        runtime_metadata_path,
        target_node,
        previous_pid=int(initial["pid"]),
        timeout_seconds=startup_timeout_seconds,
    )
    _wait_for_listener(target_host, int(node["port"]), startup_timeout_seconds)
    connect_successes, connect_failures = _connect_burst(target_host, int(node["port"]), connect_attempts)
    time.sleep(sample_seconds)
    _signal_process_for_rts_exit(int(capture["pid"]))
    _wait_for_pid_exit(int(capture["pid"]), timeout_seconds=restore_timeout_seconds)
    _wait_for_session_exit(session, timeout_seconds=restore_timeout_seconds)

    rts_text = _tail_after_offset(gc_log_path, gc_log_offset)
    if not rts_text.strip():
        raise RuntimeError("GHCRTS=-s did not produce any RTS summary output")
    (output_dir / "rts-stderr.txt").write_text(rts_text, encoding="utf-8")
    summary = _parse_rts_stats(rts_text)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    _start_session(session, _restore_session_command(node, args))
    restored = _wait_for_restarted_process(
        runtime_metadata_path,
        target_node,
        previous_pid=int(capture["pid"]),
        timeout_seconds=restore_timeout_seconds,
    )
    _wait_for_listener(target_host, int(node["port"]), restore_timeout_seconds)

    return {
        "target_node": target_node,
        "session": session,
        "capture_pid": int(capture["pid"]),
        "restored_pid": int(restored["pid"]),
        "port": int(node["port"]),
        "connect_successes": int(connect_successes),
        "connect_failures": int(connect_failures),
        "restored_listener_ok": True,
        "summary_relpath": _relative_to_run(output_dir / "summary.json"),
        "rts_stderr_relpath": _relative_to_run(output_dir / "rts-stderr.txt"),
        **summary,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Capture GHC RTS GC summary for a live Haskell runtime node")
    parser.add_argument("--runtime-metadata-path", required=True)
    parser.add_argument("--target-node", required=True)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--connect-attempts", type=int, default=2)
    parser.add_argument("--sample-seconds", type=float, default=2.0)
    parser.add_argument("--startup-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--restore-timeout-seconds", type=float, default=30.0)
    args = parser.parse_args(argv[1:])

    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()
    runtime_metadata_path = Path(args.runtime_metadata_path)
    emit_target_event(
        primitive="runtime_haskell_gc_capture",
        event="haskell_gc_capture_started",
        payload={
            "runtime_metadata_path": str(runtime_metadata_path),
            "target_node": args.target_node,
            "output_dir": str(output_dir),
            "target_host": args.target_host,
            "connect_attempts": args.connect_attempts,
            "sample_seconds": args.sample_seconds,
        },
    )
    result = run_gc_capture(
        runtime_metadata_path=runtime_metadata_path,
        target_node=args.target_node,
        output_dir=output_dir,
        target_host=args.target_host,
        connect_attempts=args.connect_attempts,
        sample_seconds=args.sample_seconds,
        startup_timeout_seconds=args.startup_timeout_seconds,
        restore_timeout_seconds=args.restore_timeout_seconds,
    )
    emit_target_event(
        primitive="runtime_haskell_gc_capture",
        event="haskell_gc_capture_completed",
        payload=result,
        level="info" if result["total_bytes_allocated"] > 0 and result["restored_listener_ok"] else "error",
    )
    print(
        "target_node={target_node} capture_pid={capture_pid} restored_pid={restored_pid} port={port} "
        "connect_successes={connect_successes} total_bytes_allocated={total_bytes_allocated} "
        "max_residency_bytes={max_residency_bytes} gc_cpu_seconds={gc_cpu_seconds} "
        "max_pause_seconds={max_pause_seconds} restored_listener_ok={restored_listener_ok} "
        "summary_relpath={summary_relpath} rts_stderr_relpath={rts_stderr_relpath}".format(**result)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
