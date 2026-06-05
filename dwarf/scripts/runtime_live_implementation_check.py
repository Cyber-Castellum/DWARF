#!/usr/bin/env python3

import json
import socket
import subprocess
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_telemetry import emit_runtime_metric, emit_target_event  # noqa: E402


def target_implementation_from_scenario(path: Path) -> str:
    body = json.loads(path.read_text(encoding="utf-8"))
    target = body.get("target") or {}
    implementation = target.get("implementation")
    if implementation not in {"cardano-node", "amaru"}:
        raise RuntimeError(f"unsupported target implementation in {path}: {implementation!r}")
    return implementation


def _compose_project_for_runtime(runtime_root: Path) -> str:
    name = runtime_root.name
    if not name:
        raise RuntimeError(f"cannot derive compose project from runtime root: {runtime_root}")
    return f"dwarf-{name}"


def _runtime_json_descriptor(runtime_root: Path, target_implementation: str) -> dict | None:
    runtime_json = runtime_root / "runtime.json"
    if not runtime_json.exists():
        return None
    body = json.loads(runtime_json.read_text(encoding="utf-8"))
    if all(key in body for key in ("listen_address", "pid_file", "log_path", "chain_dir")):
        listener_host, listener_port_text = str(body["listen_address"]).rsplit(":", 1)
        return {
            "mode": "host",
            "session": body.get("session"),
            "listener_host": listener_host,
            "listener_port": int(listener_port_text),
            "data_dir": Path(str(body["chain_dir"])),
            "log_path": Path(str(body["log_path"])),
            "pid_file": Path(str(body["pid_file"])),
        }
    node_key = "haskell_nodes" if target_implementation == "cardano-node" else "amaru_nodes"
    nodes = body.get(node_key)
    if not isinstance(nodes, list) or not nodes:
        return None
    first = nodes[0]
    return {
        "mode": "host",
        "session": first.get("session"),
        "listener_host": "127.0.0.1",
        "listener_port": int(first["port"]),
        "data_dir": Path(first["db_dir"] if target_implementation == "cardano-node" else first["chain_dir"]),
        "log_path": Path(first["log_path"]),
        "pid_file": Path(first["pid_file"]),
    }


def _service_descriptor(runtime_root: Path, target_implementation: str) -> dict:
    host_descriptor = _runtime_json_descriptor(runtime_root, target_implementation)
    if host_descriptor is not None:
        return host_descriptor
    compose_project = _compose_project_for_runtime(runtime_root)
    if target_implementation == "cardano-node":
        log_path = runtime_root / "env" / "logs" / "node1" / "stdout.log"
        if not log_path.exists():
            alt = runtime_root / "logs" / "node1" / "stdout.log"
            log_path = alt if alt.exists() else log_path
        return {
            "container_name": f"{compose_project}-node1-1",
            "listener_port": 3001,
            "data_dir": runtime_root / "env" / "node-data" / "node1" / "db",
            "log_path": log_path,
        }
    return {
        "container_name": f"{compose_project}-amaru1-1",
        "listener_port": 3000,
        "data_dir": runtime_root / "amaru" / "amaru1",
        "log_path": None,
    }


def _docker_json(*args: str):
    proc = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"docker {' '.join(args)} failed")
    return json.loads(proc.stdout)


def _container_running(container_name: str) -> bool:
    proc = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Status}}", container_name],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    return proc.returncode == 0 and proc.stdout.strip() == "running"


def _container_ip(container_name: str) -> str:
    data = _docker_json("inspect", container_name)
    networks = ((data[0] or {}).get("NetworkSettings") or {}).get("Networks") or {}
    for network in networks.values():
        address = network.get("IPAddress")
        if address:
            return str(address)
    raise RuntimeError(f"no container IP found for {container_name}")


def _listener_ok(address: str, port: int) -> bool:
    try:
        with socket.create_connection((address, port), timeout=2):
            return True
    except OSError:
        return False


def _docker_log_bytes(container_name: str) -> int:
    proc = subprocess.run(
        ["docker", "logs", "--tail", "200", container_name],
        capture_output=True,
        text=False,
        check=False,
        timeout=20,
    )
    return len((proc.stdout or b"") + (proc.stderr or b""))


def _pid_running(pid_file: Path) -> bool:
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    try:
        subprocess.run(["kill", "-0", str(pid)], check=True, capture_output=True, timeout=5)
        return True
    except subprocess.CalledProcessError:
        return False


def _emit_container_metrics(
    *,
    target_implementation: str,
    container_name: str,
    listener_port: int,
    listener_ok: bool,
    data_dir_bytes: int,
    log_bytes: int,
) -> None:
    emit_runtime_metric("live_impl_listener_port", value=listener_port, meta={"target_implementation": target_implementation})
    emit_runtime_metric("live_impl_listener_ok", value=1 if listener_ok else 0, meta={"target_implementation": target_implementation})
    emit_runtime_metric("live_impl_data_dir_bytes", value=data_dir_bytes, meta={"target_implementation": target_implementation})
    emit_runtime_metric("live_impl_log_bytes", value=log_bytes, meta={"target_implementation": target_implementation})
    emit_target_event(
        primitive="runtime_live_implementation_check",
        event="live_runtime_baseline",
        payload={
            "target_implementation": target_implementation,
            "container_name": container_name,
            "listener_port": listener_port,
            "listener_ok": listener_ok,
            "data_dir_bytes": data_dir_bytes,
            "log_bytes": log_bytes,
        },
        level="info" if listener_ok and data_dir_bytes > 0 and log_bytes > 0 else "error",
    )


def run_live_baseline(*, runtime_root: Path, scenario_path: Path) -> int:
    started = time.perf_counter()
    target_implementation = target_implementation_from_scenario(scenario_path)
    service = _service_descriptor(runtime_root, target_implementation)
    mode = service.get("mode", "container")
    container_name = service.get("container_name", service.get("session", "host-service"))
    if mode == "host":
        pid_file = Path(service["pid_file"])
        if not _pid_running(pid_file):
            raise RuntimeError(f"host process not running: {pid_file}")
        listener_ok = _listener_ok(service.get("listener_host", "127.0.0.1"), service["listener_port"])
        if not listener_ok:
            raise RuntimeError(
                f"listener probe failed for host service {container_name} on "
                f"{service.get('listener_host', '127.0.0.1')}:{service['listener_port']}"
            )
    else:
        if not _container_running(container_name):
            raise RuntimeError(f"container not running: {container_name}")
        container_ip = _container_ip(container_name)
        listener_ok = _listener_ok(container_ip, service["listener_port"])
        if not listener_ok:
            raise RuntimeError(f"listener probe failed for {container_name} on {container_ip}:{service['listener_port']}")
    data_dir = Path(service["data_dir"])
    if not data_dir.exists():
        raise RuntimeError(f"missing data directory: {data_dir}")
    data_dir_bytes = sum(path.stat().st_size for path in data_dir.rglob("*") if path.is_file())
    if service["log_path"] is None:
        log_bytes = _docker_log_bytes(container_name)
    else:
        log_path = Path(service["log_path"])
        if not log_path.exists():
            raise RuntimeError(f"missing log file: {log_path}")
        log_bytes = log_path.stat().st_size
    if data_dir_bytes <= 0:
        raise RuntimeError(f"empty data directory for {container_name}: {data_dir}")
    if log_bytes <= 0:
        raise RuntimeError(f"no runtime log bytes observed for {container_name}")
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    _emit_container_metrics(
        target_implementation=target_implementation,
        container_name=container_name,
        listener_port=service["listener_port"],
        listener_ok=listener_ok,
        data_dir_bytes=data_dir_bytes,
        log_bytes=log_bytes,
    )
    emit_runtime_metric("live_impl_elapsed_ms", value=elapsed_ms, meta={"target_implementation": target_implementation})
    print(
        f"target_implementation={target_implementation} "
        f"container={container_name} listener_port={service['listener_port']} "
        f"listener_ok={str(listener_ok).lower()} data_dir_bytes={data_dir_bytes} log_bytes={log_bytes}"
    )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] != "baseline":
        print(
            "usage: runtime_live_implementation_check.py baseline [--runtime-root PATH] [--scenario-path PATH]",
            file=sys.stderr,
        )
        return 2
    runtime_root = None
    scenario_path = None
    i = 2
    while i < len(argv):
        if argv[i] == "--runtime-root" and i + 1 < len(argv):
            runtime_root = Path(argv[i + 1])
            i += 2
            continue
        if argv[i] == "--scenario-path" and i + 1 < len(argv):
            scenario_path = Path(argv[i + 1])
            i += 2
            continue
        print(f"unknown argument: {argv[i]}", file=sys.stderr)
        return 2
    if runtime_root is None or scenario_path is None:
        print("baseline mode requires --runtime-root and --scenario-path", file=sys.stderr)
        return 2
    return run_live_baseline(runtime_root=runtime_root, scenario_path=scenario_path)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
