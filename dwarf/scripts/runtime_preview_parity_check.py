#!/usr/bin/env python3

import json
import os
import socket
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_telemetry import emit_runtime_metric, emit_target_event  # noqa: E402
from runtime_preview_trace_metrics import collect_amaru_trace_metrics, collect_cardano_trace_metrics, read_log_window  # noqa: E402


DEFAULT_RUNTIME_ROOTS = {
    "amaru": "/opt/dwarf/cardano-profiles/profile-d-amaru-preview-proof",
    "cardano-node": "/opt/dwarf/cardano-profiles/profile-e-haskell-preview-proof",
}


def target_implementation_from_scenario(path: Path) -> str:
    body = json.loads(path.read_text(encoding="utf-8"))
    target = body.get("target") or {}
    implementation = target.get("implementation")
    if implementation not in DEFAULT_RUNTIME_ROOTS:
        raise RuntimeError(f"unsupported target implementation in {path}: {implementation!r}")
    return implementation


def runtime_root_for_implementation(implementation: str) -> Path:
    env_key = f"ADA2_DWARF_PREVIEW_{implementation.upper().replace('-', '_')}_ROOT"
    return Path(os.environ.get(env_key, DEFAULT_RUNTIME_ROOTS[implementation]))


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


def run_baseline(*, scenario_path: Path, sample_seconds: int) -> int:
    target_implementation = target_implementation_from_scenario(scenario_path)
    runtime_root = runtime_root_for_implementation(target_implementation)
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
        raise RuntimeError(f"process pid is not running: {pid_file}")
    if not _listener_ok(listen_host, listen_port):
        raise RuntimeError(f"listener probe failed: {listen_host}:{listen_port}")
    chain_before = _dir_size_bytes(chain_dir)
    log_before = log_path.stat().st_size
    time.sleep(sample_seconds)
    if not _pid_running(pid_file):
        raise RuntimeError(f"process exited during proof window: {pid_file}")
    chain_after = _dir_size_bytes(chain_dir)
    log_after = log_path.stat().st_size
    chain_delta = max(0, chain_after - chain_before)
    log_delta = max(0, log_after - log_before)
    progress_ok = 1 if chain_delta > 0 or log_delta > 0 else 0
    meta = {"target_implementation": target_implementation}
    emit_runtime_metric("preview_listener_port", value=listen_port, meta=meta)
    emit_runtime_metric("preview_listener_ok", value=1, meta=meta)
    emit_runtime_metric("preview_chain_bytes_before", value=chain_before, meta=meta)
    emit_runtime_metric("preview_chain_bytes_after", value=chain_after, meta=meta)
    emit_runtime_metric("preview_chain_bytes_delta", value=chain_delta, meta=meta)
    emit_runtime_metric("preview_log_bytes_before", value=log_before, meta=meta)
    emit_runtime_metric("preview_log_bytes_after", value=log_after, meta=meta)
    emit_runtime_metric("preview_log_bytes_delta", value=log_delta, meta=meta)
    emit_runtime_metric("preview_progress_ok", value=progress_ok, meta=meta)
    trace_window = read_log_window(log_path, log_before, log_after)
    amaru_trace_metrics = None
    if target_implementation == "amaru":
        amaru_trace_metrics = collect_amaru_trace_metrics(trace_window)
        emit_runtime_metric("preview_adopted_tip_count", value=amaru_trace_metrics["adopted_tip_count"], meta=meta)
        emit_runtime_metric("preview_tip_slot_delta", value=amaru_trace_metrics["tip_slot_delta"], meta=meta)
        emit_runtime_metric("preview_amaru_adopted_tip_count", value=amaru_trace_metrics["adopted_tip_count"], meta=meta)
        emit_runtime_metric("preview_amaru_tip_slot_delta", value=amaru_trace_metrics["tip_slot_delta"], meta=meta)
        emit_runtime_metric("preview_amaru_peer_connected_count", value=amaru_trace_metrics["peer_connected_count"], meta=meta)
        emit_runtime_metric("preview_amaru_peer_connection_died_count", value=amaru_trace_metrics["peer_connection_died_count"], meta=meta)
    elif target_implementation == "cardano-node":
        cardano_trace_metrics = collect_cardano_trace_metrics(trace_window)
        emit_runtime_metric("preview_adopted_tip_count", value=cardano_trace_metrics["adopted_tip_count"], meta=meta)
        emit_runtime_metric("preview_tip_slot_delta", value=cardano_trace_metrics["tip_slot_delta"], meta=meta)
        emit_runtime_metric("preview_peer_connected_count", value=cardano_trace_metrics["peer_connected_count"], meta=meta)
        emit_runtime_metric(
            "preview_peer_connection_died_count",
            value=cardano_trace_metrics["peer_connection_died_count"],
            meta=meta,
        )
    if target_implementation == "amaru":
        emit_runtime_metric("preview_peer_connected_count", value=amaru_trace_metrics["peer_connected_count"], meta=meta)
        emit_runtime_metric(
            "preview_peer_connection_died_count",
            value=amaru_trace_metrics["peer_connection_died_count"],
            meta=meta,
        )
    emit_target_event(
        primitive="runtime_preview_parity_check",
        event="preview_runtime_baseline",
        payload={
            "target_implementation": target_implementation,
            "runtime_root": str(runtime_root),
            "listen_address": metadata["listen_address"],
            "upstream_peer_address": metadata["upstream_peer_address"],
            "chain_bytes_before": chain_before,
            "chain_bytes_after": chain_after,
            "chain_bytes_delta": chain_delta,
            "log_bytes_before": log_before,
            "log_bytes_after": log_after,
            "log_bytes_delta": log_delta,
            "progress_ok": bool(progress_ok),
            "amaru_trace_metrics": amaru_trace_metrics,
        },
        level="info" if progress_ok else "error",
    )
    if progress_ok != 1:
        raise RuntimeError(f"{target_implementation} preview baseline saw no chain or log growth during the sample window")
    print(
        f"target_implementation={target_implementation} "
        f"listen_address={metadata['listen_address']} "
        f"chain_delta_bytes={chain_delta} "
        f"log_delta_bytes={log_delta} "
        f"progress_ok=true"
    )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] != "baseline":
        print(
            "usage: runtime_preview_parity_check.py baseline --scenario-path PATH [--sample-seconds N]",
            file=sys.stderr,
        )
        return 2
    scenario_path = None
    sample_seconds = 20
    i = 2
    while i < len(argv):
        if argv[i] == "--scenario-path" and i + 1 < len(argv):
            scenario_path = Path(argv[i + 1])
            i += 2
            continue
        if argv[i] == "--sample-seconds" and i + 1 < len(argv):
            sample_seconds = int(argv[i + 1])
            i += 2
            continue
        print(f"unknown argument: {argv[i]}", file=sys.stderr)
        return 2
    if scenario_path is None:
        print("baseline mode requires --scenario-path", file=sys.stderr)
        return 2
    return run_baseline(scenario_path=scenario_path, sample_seconds=sample_seconds)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
