#!/usr/bin/env python3

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_telemetry import emit_runtime_metric, emit_target_event  # noqa: E402
from runtime_preview_trace_metrics import collect_amaru_trace_metrics, collect_cardano_trace_metrics, read_log_window, read_timestamp_window  # noqa: E402


DEFAULT_RUNTIME_ROOTS = {
    "amaru": "/opt/dwarf/cardano-profiles/profile-d-amaru-preview-proof",
    "cardano-node": "/opt/dwarf/cardano-profiles/profile-e-haskell-preview-proof",
}
FILTER_PREF = 103
REMOTE_ADDR_RE = re.compile(r"(\d+\.\d+\.\d+\.\d+):(\d+)")
AMARU_TRACE_PRE_GRACE_MS = 250
AMARU_TRACE_POST_GRACE_MS = 2000


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


def _resolve_upstream_peer(address: str) -> tuple[str, int, list[str]]:
    host, port_text = address.rsplit(":", 1)
    port = int(port_text)
    infos = socket.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_STREAM)
    ips = sorted({info[4][0] for info in infos})
    if not ips:
        raise RuntimeError(f"no IPv4 upstream addresses resolved for {address}")
    return host, port, ips


def _active_upstream_ips_for_process(pid: int, process_name: str, port: int) -> list[str]:
    try:
        result = subprocess.run(["ss", "-tnp", "state", "established"], text=True, capture_output=True, check=False)
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ss failed")
    hits = []
    for line in result.stdout.splitlines():
        if f":{port}" not in line:
            continue
        if f"pid={pid}," not in line and f'("{process_name}"' not in line:
            continue
        addrs = REMOTE_ADDR_RE.findall(line)
        if len(addrs) < 2:
            continue
        for ip, port_text in addrs[1:]:
            if int(port_text) == port:
                hits.append(ip)
    return sorted(set(hits))


def _run_tc(args: list[str]) -> None:
    result = subprocess.run(["sudo", "tc", *args], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"tc failed: {' '.join(args)}")


def _run_tc_maybe(args: list[str]) -> None:
    subprocess.run(["sudo", "tc", *args], text=True, capture_output=True, check=False)


def _run_ip(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["sudo", "ip", *args], text=True, capture_output=True, check=False)


def _run_ip_strict(args: list[str]) -> None:
    result = _run_ip(args)
    if result is None:
        return
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ip failed: {' '.join(args)}")


def _ifb_exists(ifb_name: str) -> bool:
    result = subprocess.run(
        ["sudo", "ip", "link", "show", "dev", ifb_name],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _ensure_ifb_device(ifb_name: str) -> None:
    _run_ip(["link", "del", ifb_name])
    try:
        _run_ip_strict(["link", "add", ifb_name, "type", "ifb"])
    except RuntimeError as exc:
        message = str(exc)
        if "File exists" not in message or not _ifb_exists(ifb_name):
            raise
    try:
        _run_ip_strict(["link", "set", "dev", ifb_name, "up"])
    except RuntimeError as exc:
        message = str(exc)
        if "Cannot find device" not in message:
            raise
        _run_ip(["link", "del", ifb_name])
        _run_ip_strict(["link", "add", ifb_name, "type", "ifb"])
        _run_ip_strict(["link", "set", "dev", ifb_name, "up"])


def _tc_qdisc_show(interface: str) -> str:
    result = subprocess.run(["sudo", "tc", "qdisc", "show", "dev", interface], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"tc qdisc show failed for {interface}")
    return result.stdout


def _route_interface_for_ip(ip: str) -> str:
    result = subprocess.run(["ip", "route", "get", ip], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ip route get failed for {ip}")
    parts = result.stdout.strip().split()
    if "dev" not in parts:
        raise RuntimeError(f"route lookup returned no interface for {ip}: {result.stdout.strip()!r}")
    return parts[parts.index("dev") + 1]


def _ifb_name_for_implementation(implementation: str) -> str:
    return "ifbdwprva" if implementation == "amaru" else "ifbdwprvh"


def _clear_delay_filters(interface: str, upstream_ips: list[str]) -> None:
    for i, _ in enumerate(upstream_ips):
        _run_tc_maybe(["filter", "del", "dev", interface, "egress", "pref", str(FILTER_PREF + i), "protocol", "ip", "flower"])


def _apply_delay_fault(*, interface: str, ifb_name: str, upstream_ips: list[str], upstream_port: int, delay_ms: int, jitter_ms: int) -> dict:
    existing_qdisc = _tc_qdisc_show(interface)
    added_clsact = "clsact" not in existing_qdisc
    _ensure_ifb_device(ifb_name)
    if added_clsact:
        try:
            _run_tc(["qdisc", "add", "dev", interface, "clsact"])
        except RuntimeError as exc:
            message = str(exc)
            if "Exclusivity flag on" in message or "File exists" in message:
                added_clsact = False
            else:
                raise
    _clear_delay_filters(interface, upstream_ips)
    for i, upstream_ip in enumerate(upstream_ips):
        _run_tc(
            [
                "filter",
                "add",
                "dev",
                interface,
                "egress",
                "pref",
                str(FILTER_PREF + i),
                "protocol",
                "ip",
                "flower",
                "dst_ip",
                f"{upstream_ip}/32",
                "ip_proto",
                "tcp",
                "dst_port",
                str(upstream_port),
                "action",
                "mirred",
                "egress",
                "redirect",
                "dev",
                ifb_name,
            ]
        )
    _run_tc(["qdisc", "add", "dev", ifb_name, "root", "netem", "delay", f"{delay_ms}ms", f"{jitter_ms}ms"])
    return {
        "interface": interface,
        "ifb_name": ifb_name,
        "upstream_ips": list(upstream_ips),
        "upstream_port": upstream_port,
        "added_clsact": added_clsact,
    }


def _remove_delay_fault(state: dict) -> None:
    interface = state["interface"]
    ifb_name = state["ifb_name"]
    _run_tc_maybe(["qdisc", "del", "dev", ifb_name, "root"])
    for i, _ in enumerate(state.get("upstream_ips", [])):
        _run_tc_maybe(["filter", "del", "dev", interface, "egress", "pref", str(FILTER_PREF + i), "protocol", "ip", "flower"])
    if state.get("added_clsact"):
        _run_tc_maybe(["qdisc", "del", "dev", interface, "clsact"])
    _run_ip(["link", "del", ifb_name])


def _window_delta(before: int, after: int) -> int:
    return max(0, after - before)


def _emit_window_metrics(
    prefix: str,
    *,
    chain_before: int,
    chain_after: int,
    log_before: int,
    log_after: int,
    log_path: Path,
    target_implementation: str,
    window_start_epoch_ms: int | None = None,
    window_end_epoch_ms: int | None = None,
) -> None:
    chain_delta = _window_delta(chain_before, chain_after)
    log_delta = _window_delta(log_before, log_after)
    progress_ok = 1 if chain_delta > 0 or log_delta > 0 else 0
    meta = {"target_implementation": target_implementation, "window": prefix}
    emit_runtime_metric(f"{prefix}_chain_bytes_delta", value=chain_delta, meta=meta)
    emit_runtime_metric(f"{prefix}_log_bytes_delta", value=log_delta, meta=meta)
    emit_runtime_metric(f"{prefix}_progress_ok", value=progress_ok, meta=meta)
    amaru_trace_metrics = None
    trace_window = read_log_window(log_path, log_before, log_after)
    if (
        target_implementation == "amaru"
        and window_start_epoch_ms is not None
        and window_end_epoch_ms is not None
    ):
        timestamp_window = read_timestamp_window(
            log_path,
            start_epoch_ms=max(0, window_start_epoch_ms - AMARU_TRACE_PRE_GRACE_MS),
            end_epoch_ms=window_end_epoch_ms + AMARU_TRACE_POST_GRACE_MS,
            target_implementation=target_implementation,
        )
        if timestamp_window:
            trace_window = timestamp_window
    if target_implementation == "amaru":
        amaru_trace_metrics = collect_amaru_trace_metrics(trace_window)
        emit_runtime_metric(f"{prefix}_adopted_tip_count", value=amaru_trace_metrics["adopted_tip_count"], meta=meta)
        emit_runtime_metric(f"{prefix}_tip_slot_delta", value=amaru_trace_metrics["tip_slot_delta"], meta=meta)
        emit_runtime_metric(f"{prefix}_peer_connected_count", value=amaru_trace_metrics["peer_connected_count"], meta=meta)
        emit_runtime_metric(f"{prefix}_peer_connection_died_count", value=amaru_trace_metrics["peer_connection_died_count"], meta=meta)
        emit_runtime_metric(f"{prefix}_amaru_adopted_tip_count", value=amaru_trace_metrics["adopted_tip_count"], meta=meta)
        emit_runtime_metric(f"{prefix}_amaru_tip_slot_delta", value=amaru_trace_metrics["tip_slot_delta"], meta=meta)
        emit_runtime_metric(f"{prefix}_amaru_peer_connected_count", value=amaru_trace_metrics["peer_connected_count"], meta=meta)
        emit_runtime_metric(f"{prefix}_amaru_peer_connection_died_count", value=amaru_trace_metrics["peer_connection_died_count"], meta=meta)
    elif target_implementation == "cardano-node":
        cardano_trace_metrics = collect_cardano_trace_metrics(trace_window)
        emit_runtime_metric(f"{prefix}_adopted_tip_count", value=cardano_trace_metrics["adopted_tip_count"], meta=meta)
        emit_runtime_metric(f"{prefix}_tip_slot_delta", value=cardano_trace_metrics["tip_slot_delta"], meta=meta)
        emit_runtime_metric(f"{prefix}_peer_connected_count", value=cardano_trace_metrics["peer_connected_count"], meta=meta)
        emit_runtime_metric(f"{prefix}_peer_connection_died_count", value=cardano_trace_metrics["peer_connection_died_count"], meta=meta)
    emit_target_event(
        primitive="runtime_preview_upstream_delay_check",
        event=f"{prefix}_observed",
        payload={
            "target_implementation": target_implementation,
            "chain_bytes_before": chain_before,
            "chain_bytes_after": chain_after,
            "chain_bytes_delta": chain_delta,
            "log_bytes_before": log_before,
            "log_bytes_after": log_after,
            "log_bytes_delta": log_delta,
            "progress_ok": bool(progress_ok),
            "amaru_trace_metrics": amaru_trace_metrics,
        },
    )


def run_delay(*, scenario_path: Path, fault_seconds: int, recovery_seconds: int, delay_ms: int, jitter_ms: int) -> int:
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
    pid = int(pid_file.read_text(encoding="utf-8").strip())
    process_name = "amaru" if target_implementation == "amaru" else "cardano-node"
    if not _listener_ok(listen_host, listen_port):
        raise RuntimeError(f"listener probe failed: {listen_host}:{listen_port}")
    upstream_host, upstream_port, upstream_ips = _resolve_upstream_peer(str(metadata["upstream_peer_address"]))
    active_upstream_ips = _active_upstream_ips_for_process(pid, process_name, upstream_port)
    if not active_upstream_ips:
        active_upstream_ips = list(upstream_ips)
    if not active_upstream_ips:
        raise RuntimeError(f"delay fault found no active upstream peer IPs for pid={pid} port={upstream_port}")
    interface_names = sorted({_route_interface_for_ip(ip) for ip in active_upstream_ips})
    if len(interface_names) != 1:
        raise RuntimeError(f"delay fault requires one route interface for active upstream peers, got {interface_names!r}")
    interface = interface_names[0]
    ifb_name = _ifb_name_for_implementation(target_implementation)

    chain_before = _dir_size_bytes(chain_dir)
    log_before = log_path.stat().st_size
    emit_target_event(
        primitive="runtime_preview_upstream_delay_check",
        event="fault_planned",
        payload={
            "target_implementation": target_implementation,
            "runtime_root": str(runtime_root),
            "listen_address": metadata["listen_address"],
            "upstream_peer_address": metadata["upstream_peer_address"],
            "resolved_upstream_ips": upstream_ips,
            "active_upstream_ips": active_upstream_ips,
            "interface": interface,
            "ifb_name": ifb_name,
            "fault_seconds": fault_seconds,
            "recovery_seconds": recovery_seconds,
            "delay_ms": delay_ms,
            "jitter_ms": jitter_ms,
        },
    )

    applied_epoch_ms = int(time.time() * 1000)
    fault_state = _apply_delay_fault(
        interface=interface,
        ifb_name=ifb_name,
        upstream_ips=active_upstream_ips,
        upstream_port=upstream_port,
        delay_ms=delay_ms,
        jitter_ms=jitter_ms,
    )
    fault_active = True
    try:
        emit_runtime_metric("preview_fault_applied_epoch_ms", value=applied_epoch_ms, meta={"target_implementation": target_implementation})
        emit_target_event(
            primitive="runtime_preview_upstream_delay_check",
            event="fault_applied",
            payload={
                "target_implementation": target_implementation,
                "upstream_host": upstream_host,
                "upstream_ips": active_upstream_ips,
                "upstream_port": upstream_port,
                "interface": interface,
                "ifb_name": ifb_name,
                "delay_ms": delay_ms,
                "jitter_ms": jitter_ms,
                "applied_epoch_ms": applied_epoch_ms,
            },
        )
        time.sleep(fault_seconds)
        chain_fault_end = _dir_size_bytes(chain_dir)
        log_fault_end = log_path.stat().st_size
        _emit_window_metrics(
            "preview_fault_window",
            chain_before=chain_before,
            chain_after=chain_fault_end,
            log_before=log_before,
            log_after=log_fault_end,
            log_path=log_path,
            target_implementation=target_implementation,
            window_start_epoch_ms=applied_epoch_ms,
            window_end_epoch_ms=int(time.time() * 1000),
        )

        removed_epoch_ms = int(time.time() * 1000)
        _remove_delay_fault(fault_state)
        fault_active = False
        emit_runtime_metric("preview_fault_removed_epoch_ms", value=removed_epoch_ms, meta={"target_implementation": target_implementation})
        emit_target_event(
            primitive="runtime_preview_upstream_delay_check",
            event="fault_removed",
            payload={
                "target_implementation": target_implementation,
                "upstream_host": upstream_host,
                "upstream_ips": active_upstream_ips,
                "upstream_port": upstream_port,
                "interface": interface,
                "ifb_name": ifb_name,
                "removed_epoch_ms": removed_epoch_ms,
            },
        )

        time.sleep(recovery_seconds)
        if not _pid_running(pid_file):
            raise RuntimeError(f"process exited during recovery window: {pid_file}")
        chain_recovery_end = _dir_size_bytes(chain_dir)
        log_recovery_end = log_path.stat().st_size
        _emit_window_metrics(
            "preview_postfault_window",
            chain_before=chain_fault_end,
            chain_after=chain_recovery_end,
            log_before=log_fault_end,
            log_after=log_recovery_end,
            log_path=log_path,
            target_implementation=target_implementation,
            window_start_epoch_ms=removed_epoch_ms,
            window_end_epoch_ms=int(time.time() * 1000),
        )

        total_chain_delta = _window_delta(chain_before, chain_recovery_end)
        total_log_delta = _window_delta(log_before, log_recovery_end)
        overall_progress_ok = 1 if total_chain_delta > 0 or total_log_delta > 0 else 0
        meta = {"target_implementation": target_implementation}
        emit_runtime_metric("preview_chain_bytes_delta", value=total_chain_delta, meta=meta)
        emit_runtime_metric("preview_log_bytes_delta", value=total_log_delta, meta=meta)
        emit_runtime_metric("preview_progress_ok", value=overall_progress_ok, meta=meta)
        emit_runtime_metric("preview_listener_ok", value=1 if _listener_ok(listen_host, listen_port) else 0, meta=meta)
        emit_runtime_metric("preview_listener_port", value=listen_port, meta=meta)
        emit_target_event(
            primitive="runtime_preview_upstream_delay_check",
            event="fault_check_completed",
            payload={
                "target_implementation": target_implementation,
                "upstream_peer_address": metadata["upstream_peer_address"],
                "active_upstream_ips": active_upstream_ips,
                "interface": interface,
                "ifb_name": ifb_name,
                "fault_seconds": fault_seconds,
                "recovery_seconds": recovery_seconds,
                "delay_ms": delay_ms,
                "jitter_ms": jitter_ms,
                "total_chain_bytes_delta": total_chain_delta,
                "total_log_bytes_delta": total_log_delta,
                "overall_progress_ok": bool(overall_progress_ok),
            },
            level="info",
        )
        print(
            f"target_implementation={target_implementation} "
            f"upstream_peer={metadata['upstream_peer_address']} "
            f"fault_window_chain_delta={_window_delta(chain_before, chain_fault_end)} "
            f"postfault_window_chain_delta={_window_delta(chain_fault_end, chain_recovery_end)} "
            f"overall_progress_ok={bool(overall_progress_ok)}"
        )
        return 0
    finally:
        if fault_active:
            _remove_delay_fault(fault_state)


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] != "delay":
        print(
            "usage: runtime_preview_upstream_delay_check.py delay --scenario-path PATH [--fault-seconds N] [--recovery-seconds N] [--delay-ms N] [--jitter-ms N]",
            file=sys.stderr,
        )
        return 2
    scenario_path = None
    fault_seconds = 15
    recovery_seconds = 20
    delay_ms = 400
    jitter_ms = 100
    i = 2
    while i < len(argv):
        if argv[i] == "--scenario-path" and i + 1 < len(argv):
            scenario_path = Path(argv[i + 1])
            i += 2
            continue
        if argv[i] == "--fault-seconds" and i + 1 < len(argv):
            fault_seconds = int(argv[i + 1])
            i += 2
            continue
        if argv[i] == "--recovery-seconds" and i + 1 < len(argv):
            recovery_seconds = int(argv[i + 1])
            i += 2
            continue
        if argv[i] == "--delay-ms" and i + 1 < len(argv):
            delay_ms = int(argv[i + 1])
            i += 2
            continue
        if argv[i] == "--jitter-ms" and i + 1 < len(argv):
            jitter_ms = int(argv[i + 1])
            i += 2
            continue
        print(f"unknown argument: {argv[i]}", file=sys.stderr)
        return 2
    if scenario_path is None:
        print("delay mode requires --scenario-path", file=sys.stderr)
        return 2
    return run_delay(
        scenario_path=scenario_path,
        fault_seconds=fault_seconds,
        recovery_seconds=recovery_seconds,
        delay_ms=delay_ms,
        jitter_ms=jitter_ms,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv))
