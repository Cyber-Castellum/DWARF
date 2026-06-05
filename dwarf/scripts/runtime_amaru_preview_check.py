#!/usr/bin/env python3

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_telemetry import emit_runtime_metric, emit_target_event  # noqa: E402


def _dir_size_bytes(path: Path) -> int:
    return sum(node.stat().st_size for node in path.rglob("*") if node.is_file())


def _listener_ok(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def _pid_running(pid_file: Path) -> bool:
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (TypeError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def run_proof(*, runtime_root: Path, sample_seconds: int) -> int:
    metadata_path = runtime_root / "runtime.json"
    if not metadata_path.exists():
        raise RuntimeError(f"missing runtime metadata: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    listen_host, listen_port_text = str(metadata["listen_address"]).rsplit(":", 1)
    listen_port = int(listen_port_text)
    chain_dir = Path(str(metadata["chain_dir"]))
    log_path = Path(str(metadata["log_path"]))
    pid_file = Path(str(metadata["pid_file"]))
    if not chain_dir.exists():
        raise RuntimeError(f"missing chain dir: {chain_dir}")
    if not log_path.exists():
        raise RuntimeError(f"missing log path: {log_path}")
    if not _pid_running(pid_file):
        raise RuntimeError(f"amaru pid is not running: {pid_file}")
    if not _listener_ok(listen_host, listen_port):
        raise RuntimeError(f"listener probe failed: {listen_host}:{listen_port}")
    chain_before = _dir_size_bytes(chain_dir)
    log_before = log_path.stat().st_size
    time.sleep(sample_seconds)
    if not _pid_running(pid_file):
        raise RuntimeError(f"amaru pid exited during proof window: {pid_file}")
    chain_after = _dir_size_bytes(chain_dir)
    log_after = log_path.stat().st_size
    chain_delta = max(0, chain_after - chain_before)
    log_delta = max(0, log_after - log_before)
    progress_ok = 1 if chain_delta > 0 or log_delta > 0 else 0
    emit_runtime_metric("amaru_preview_listener_port", value=listen_port)
    emit_runtime_metric("amaru_preview_listener_ok", value=1)
    emit_runtime_metric("amaru_preview_chain_bytes_before", value=chain_before)
    emit_runtime_metric("amaru_preview_chain_bytes_after", value=chain_after)
    emit_runtime_metric("amaru_preview_chain_bytes_delta", value=chain_delta)
    emit_runtime_metric("amaru_preview_log_bytes_before", value=log_before)
    emit_runtime_metric("amaru_preview_log_bytes_after", value=log_after)
    emit_runtime_metric("amaru_preview_log_bytes_delta", value=log_delta)
    emit_runtime_metric("amaru_preview_progress_ok", value=progress_ok)
    emit_target_event(
        primitive="runtime_amaru_preview_check",
        event="preview_proof_of_life",
        payload={
            "listen_address": metadata["listen_address"],
            "upstream_peer_address": metadata["upstream_peer_address"],
            "chain_bytes_before": chain_before,
            "chain_bytes_after": chain_after,
            "chain_bytes_delta": chain_delta,
            "log_bytes_before": log_before,
            "log_bytes_after": log_after,
            "log_bytes_delta": log_delta,
            "progress_ok": bool(progress_ok),
        },
        level="info" if progress_ok else "error",
    )
    if progress_ok != 1:
        raise RuntimeError("amaru preview proof saw no chain or log growth during the sample window")
    print(
        f"listen_address={metadata['listen_address']} "
        f"chain_delta_bytes={chain_delta} "
        f"log_delta_bytes={log_delta} "
        f"progress_ok=true"
    )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] != "proof":
        print("usage: runtime_amaru_preview_check.py proof --runtime-root PATH [--sample-seconds N]", file=sys.stderr)
        return 2
    runtime_root = None
    sample_seconds = 20
    i = 2
    while i < len(argv):
        if argv[i] == "--runtime-root" and i + 1 < len(argv):
            runtime_root = Path(argv[i + 1])
            i += 2
            continue
        if argv[i] == "--sample-seconds" and i + 1 < len(argv):
            sample_seconds = int(argv[i + 1])
            i += 2
            continue
        print(f"unknown argument: {argv[i]}", file=sys.stderr)
        return 2
    if runtime_root is None:
        print("proof mode requires --runtime-root", file=sys.stderr)
        return 2
    return run_proof(runtime_root=runtime_root, sample_seconds=sample_seconds)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
