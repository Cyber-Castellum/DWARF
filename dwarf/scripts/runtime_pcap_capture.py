#!/usr/bin/env python3

import argparse
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

from runtime_telemetry import emit_target_event  # noqa: E402


DEFAULT_WORKLOAD_SCRIPT = SCRIPT_DIR / "runtime_fetch_check.py"


def count_packets(pcap_path: Path, *, tcpdump_bin: str) -> int:
    proc = subprocess.run(
        [tcpdump_bin, "-n", "-r", str(pcap_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return 0
    count = 0
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("reading from file "):
            continue
        count += 1
    return count


def run_capture(
    *,
    interface: str,
    output_dir: Path,
    workload_mode: str,
    target_host: str,
    target_ports: list[int],
    connect_attempts: int,
    workload_script: str,
    python_bin: str,
    tcpdump_bin: str,
    sudo_bin: str,
    startup_seconds: float,
    settle_seconds: float,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    pcap_path = output_dir / "capture.pcap"
    tcpdump_cmd = [
        sudo_bin,
        "-n",
        tcpdump_bin,
        "-i",
        interface,
        "-n",
        "-s",
        "0",
        "-U",
        "-w",
        str(pcap_path),
        "tcp",
    ]
    workload_cmd = [python_bin, workload_script, workload_mode]

    tcpdump_proc = subprocess.Popen(
        tcpdump_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        time.sleep(startup_seconds)
        if workload_mode == "tcp-connect-burst":
            connect_successes = 0
            for _ in range(connect_attempts):
                for port in target_ports:
                    with socket.create_connection((target_host, port), timeout=1.0):
                        connect_successes += 1
            workload = subprocess.CompletedProcess(
                ["tcp-connect-burst", target_host, *[str(port) for port in target_ports]],
                0,
                stdout=f"connect_successes={connect_successes}\n",
                stderr="",
            )
        else:
            connect_successes = 0
            workload = subprocess.run(
                workload_cmd,
                capture_output=True,
                text=True,
                check=False,
            )
        time.sleep(settle_seconds)
    finally:
        tcpdump_proc.send_signal(signal.SIGINT)
        try:
            tcpdump_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            tcpdump_proc.kill()
            tcpdump_proc.wait(timeout=5)

    packet_count = count_packets(pcap_path, tcpdump_bin=tcpdump_bin) if pcap_path.exists() else 0
    return {
        "interface": interface,
        "workload_mode": workload_mode,
        "pcap_path": str(pcap_path),
        "packet_count": packet_count,
        "pcap_size_bytes": pcap_path.stat().st_size if pcap_path.exists() else 0,
        "workload_exit_code": int(workload.returncode),
        "tcpdump_exit_code": int(tcpdump_proc.returncode or 0),
        "connect_successes": int(connect_successes),
        "workload_stdout": (workload.stdout or "")[:4096],
        "workload_stderr": (workload.stderr or "")[:1024],
    }


def _default_output_dir() -> Path:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir) / "outputs" / "runtime-pcap-capture"
    return Path.cwd() / "outputs" / "runtime-pcap-capture"


def _relative_pcap_path(pcap_path: Path) -> str:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        try:
            return str(pcap_path.relative_to(Path(run_dir)))
        except ValueError:
            pass
    return str(pcap_path)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Capture bounded runtime pcap while running a stable fetch workload")
    parser.add_argument("--interface", default="lo")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--workload-mode", default="tcp-connect-burst")
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-ports", nargs="+", type=int, default=[33001, 33002, 33003])
    parser.add_argument("--connect-attempts", type=int, default=3)
    parser.add_argument("--workload-script", default=str(DEFAULT_WORKLOAD_SCRIPT))
    parser.add_argument("--python-bin", default="python3")
    parser.add_argument("--tcpdump-bin", default="tcpdump")
    parser.add_argument("--sudo-bin", default="sudo")
    parser.add_argument("--startup-seconds", type=float, default=1.0)
    parser.add_argument("--settle-seconds", type=float, default=0.5)
    args = parser.parse_args(argv[1:])

    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()
    emit_target_event(
        primitive="runtime_pcap_capture",
        event="pcap_capture_started",
        payload={
            "interface": args.interface,
            "workload_mode": args.workload_mode,
            "target_host": args.target_host,
            "target_ports": args.target_ports,
            "output_dir": str(output_dir),
        },
    )

    result = run_capture(
        interface=args.interface,
        output_dir=output_dir,
        workload_mode=args.workload_mode,
        target_host=args.target_host,
        target_ports=args.target_ports,
        connect_attempts=args.connect_attempts,
        workload_script=args.workload_script,
        python_bin=args.python_bin,
        tcpdump_bin=args.tcpdump_bin,
        sudo_bin=args.sudo_bin,
        startup_seconds=args.startup_seconds,
        settle_seconds=args.settle_seconds,
    )
    result["pcap_relpath"] = _relative_pcap_path(Path(result["pcap_path"]))
    emit_target_event(
        primitive="runtime_pcap_capture",
        event="pcap_capture_completed",
        payload=result,
        level="info" if result["workload_exit_code"] == 0 and result["packet_count"] > 0 else "error",
    )
    print(
        "interface={interface} workload_mode={workload_mode} packet_count={packet_count} "
        "connect_successes={connect_successes} "
        "pcap_relpath={pcap_relpath} pcap_size_bytes={pcap_size_bytes} "
        "workload_exit_code={workload_exit_code} tcpdump_exit_code={tcpdump_exit_code}".format(**result)
    )
    return 0 if result["workload_exit_code"] == 0 and result["packet_count"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
