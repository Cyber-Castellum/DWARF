#!/usr/bin/env python3

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_telemetry import emit_target_event  # noqa: E402


def _load_runtime_node(runtime_metadata_path: Path, target_node: str) -> dict:
    body = json.loads(runtime_metadata_path.read_text(encoding="utf-8"))
    nodes = body.get("nodes")
    if not isinstance(nodes, list):
        nodes = []
        for key in ("haskell_nodes", "amaru_nodes"):
            group = body.get(key)
            if isinstance(group, list):
                nodes.extend(group)
    if not nodes:
        services = ((body.get("identity") or {}).get("services") or {})
        if isinstance(services, dict):
            for name, service in services.items():
                if not isinstance(service, dict) or not service.get("container"):
                    continue
                nodes.append(
                    {
                        "id": str(name),
                        "name": str(name),
                        "impl": (
                            "amaru" if str(name).startswith("amaru-") else "cardano-node"
                        ),
                        "container_name": str(service["container"]),
                    }
                )
    if not nodes:
        raise RuntimeError(
            f"runtime metadata does not contain nodes, haskell_nodes, or amaru_nodes: {runtime_metadata_path}"
        )
    for node in nodes:
        if node.get("name") == target_node or node.get("id") == target_node:
            return node
    raise RuntimeError(f"runtime metadata missing target node {target_node!r}: {runtime_metadata_path}")


def _proc_exists(pid: int, proc_root: Path) -> bool:
    return (proc_root / str(pid) / "status").exists()


def _expected_process_names(node: dict) -> set[str]:
    if node.get("impl") == "amaru":
        return {"amaru", "amaru-node"}
    return {"cardano-node"}


def _is_expected_process(node: dict, comm: str, args: str) -> bool:
    expected_names = _expected_process_names(node)
    if comm in expected_names:
        return True
    # Patched Cardano images invoke the packaged dynamic loader explicitly so
    # their private libraries do not leak through a container-wide
    # LD_LIBRARY_PATH.  In that mode Linux reports the loader as ``comm`` even
    # though the real argv contains the exact cardano-node executable.
    if not comm.startswith("ld-linux"):
        return False
    try:
        argv = shlex.split(args)
    except ValueError:
        argv = args.split()
    return any(Path(token).name in expected_names for token in argv)


def _scan_for_runtime_pid(node: dict, *, proc_root: Path = Path("/proc")) -> int:
    result = subprocess.run(
        ["ps", "-eo", "pid=,comm=,args="],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError("runtime_resource_profile could not inspect process table")
    name = str(node.get("name") or node.get("id") or "")
    socket_hint = f"socket/{name}/sock" if name else ""
    db_hint = f"node-data/{name}/db" if name else ""
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_text, comm, args = parts
        if not _is_expected_process(node, comm, args):
            continue
        if (
            (socket_hint and socket_hint in args)
            or (db_hint and db_hint in args)
            or (name and name in args)
        ):
            pid = int(pid_text)
            if _proc_exists(pid, proc_root):
                return pid
    raise RuntimeError(f"runtime_resource_profile could not resolve runtime pid for node {name!r}")


def _process_name(pid: int, proc_root: Path) -> str | None:
    try:
        return _parse_status(
            (proc_root / str(pid) / "status").read_text(encoding="utf-8")
        ).get("name")
    except OSError:
        return None


def _resolve_docker_pid(node: dict, *, proc_root: Path = Path("/proc")) -> int | None:
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
        root_pid = int(((body.get("State") or {}).get("Pid")) or 0)
    except (IndexError, ValueError, TypeError, json.JSONDecodeError):
        return None
    top = subprocess.run(
        ["docker", "top", container_name, "-eo", "pid,comm,args"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if top.returncode == 0:
        for line in top.stdout.splitlines()[1:]:
            parts = line.strip().split(None, 2)
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            args = parts[2] if len(parts) > 2 else ""
            if _is_expected_process(node, parts[1], args) and _proc_exists(pid, proc_root):
                return pid
    if (
        root_pid
        and _proc_exists(root_pid, proc_root)
        and _process_name(root_pid, proc_root) in _expected_process_names(node)
    ):
        return root_pid
    return None


def resolve_target_pid(runtime_metadata_path: Path, target_node: str, *, proc_root: Path = Path("/proc")) -> int:
    node = _load_runtime_node(runtime_metadata_path, target_node)
    docker_pid = _resolve_docker_pid(node, proc_root=proc_root)
    if docker_pid is not None and _proc_exists(docker_pid, proc_root):
        return docker_pid
    pid_file = node.get("pid_file")
    if isinstance(pid_file, str) and pid_file:
        try:
            pid = int(Path(pid_file).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = None
        if pid is not None and _proc_exists(pid, proc_root):
            return pid
    return _scan_for_runtime_pid(node, proc_root=proc_root)


def _parse_status(status_text: str) -> dict:
    body = {}
    for line in status_text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        body[key.strip()] = value.strip()
    def integer(name):
        try:
            return int(body[name].split()[0])
        except (KeyError, ValueError, IndexError):
            return None

    rss_kb = integer("VmRSS")
    return {
        "rss_bytes": rss_kb * 1024 if rss_kb is not None else None,
        "threads": integer("Threads"),
        "voluntary_ctxt_switches": integer("voluntary_ctxt_switches"),
        "nonvoluntary_ctxt_switches": integer("nonvoluntary_ctxt_switches"),
        "state": body.get("State", ""),
        "name": body.get("Name", ""),
    }


def _read_key_values(path: Path) -> dict[str, int]:
    try:
        if not path.is_file():
            return {}
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    values = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        try:
            values[key.strip()] = int(raw.strip().split()[0])
        except (ValueError, IndexError):
            continue
    return values


def _read_cpu_time(path: Path, clock_ticks_per_second: int) -> float | None:
    if clock_ticks_per_second <= 0:
        return None
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    close = text.rfind(")")
    if close < 0:
        return None
    fields = text[close + 1 :].split()
    try:
        # fields begins with proc stat field 3; utime/stime are fields 14/15.
        return (int(fields[11]) + int(fields[12])) / clock_ticks_per_second
    except (IndexError, ValueError):
        return None


def _read_network(path: Path) -> tuple[int | None, int | None]:
    try:
        if not path.is_file():
            return None, None
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None, None
    rx = 0
    tx = 0
    found = False
    for line in text.splitlines():
        if ":" not in line:
            continue
        interface, raw = line.split(":", 1)
        if interface.strip() == "lo":
            continue
        fields = raw.split()
        if len(fields) < 9:
            continue
        try:
            rx += int(fields[0])
            tx += int(fields[8])
            found = True
        except ValueError:
            continue
    return (rx, tx) if found else (None, None)


def _read_fd_count(path: Path) -> int | None:
    try:
        return len(list(path.iterdir())) if path.exists() else None
    except OSError:
        return None


def collect_samples(
    *,
    pid: int,
    sample_count: int,
    sample_interval_seconds: float,
    proc_root: Path = Path("/proc"),
    clock_ticks_per_second: int | None = None,
) -> list[dict]:
    if sample_count <= 0:
        raise ValueError("sample_count must be positive")
    ticks = clock_ticks_per_second or int(os.sysconf("SC_CLK_TCK"))
    samples = []
    previous_cpu = None
    previous_monotonic = None
    for idx in range(sample_count):
        sampled_monotonic = time.monotonic()
        status_path = proc_root / str(pid) / "status"
        fd_path = proc_root / str(pid) / "fd"
        if not status_path.exists():
            raise ProcessLookupError(f"missing proc status for pid {pid}: {status_path}")
        status = _parse_status(status_path.read_text(encoding="utf-8"))
        fd_count = _read_fd_count(fd_path)
        cpu_time = _read_cpu_time(proc_root / str(pid) / "stat", ticks)
        cpu_percent = None
        if (
            cpu_time is not None
            and previous_cpu is not None
            and previous_monotonic is not None
            and sampled_monotonic > previous_monotonic
        ):
            cpu_percent = (
                (cpu_time - previous_cpu) / (sampled_monotonic - previous_monotonic)
            ) * 100
        io = _read_key_values(proc_root / str(pid) / "io")
        network_rx, network_tx = _read_network(proc_root / str(pid) / "net" / "dev")
        samples.append(
            {
                "sample_index": idx,
                "pid": pid,
                "monotonic_seconds": sampled_monotonic,
                "ts_epoch_s": time.time(),
                "fd_count": fd_count,
                "cpu_time_seconds": cpu_time,
                "cpu_percent": cpu_percent,
                "disk_read_bytes": io.get("read_bytes"),
                "disk_write_bytes": io.get("write_bytes"),
                "network_rx_bytes": network_rx,
                "network_tx_bytes": network_tx,
                "network_scope": (
                    "process-network-namespace"
                    if network_rx is not None or network_tx is not None
                    else "unavailable"
                ),
                **status,
            }
        )
        previous_cpu = cpu_time
        previous_monotonic = sampled_monotonic
        if idx + 1 < sample_count:
            time.sleep(sample_interval_seconds)
    return samples


def _write_samples(output_dir: Path, samples: list[dict]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "samples.json").write_text(json.dumps(samples, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Collect bounded /proc resource profile snapshots for a target runtime process")
    parser.add_argument("--runtime-metadata-path", required=True)
    parser.add_argument("--target-node", required=True)
    parser.add_argument("--sample-count", type=int, default=5)
    parser.add_argument("--sample-interval-seconds", type=float, default=0.5)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args(argv[1:])

    output_dir = Path(args.output_dir) if args.output_dir else Path(os.environ.get("ADA2_DWARF_RUN_DIR", ".")) / "outputs" / "runtime-resource-profile"
    runtime_metadata_path = Path(args.runtime_metadata_path)
    pid = resolve_target_pid(runtime_metadata_path, args.target_node)
    emit_target_event(
        primitive="runtime_resource_profile",
        event="resource_profile_started",
        payload={
            "runtime_metadata_path": str(runtime_metadata_path),
            "target_node": args.target_node,
            "pid": pid,
            "sample_count": args.sample_count,
            "sample_interval_seconds": args.sample_interval_seconds,
        },
    )
    samples = collect_samples(
        pid=pid,
        sample_count=args.sample_count,
        sample_interval_seconds=args.sample_interval_seconds,
    )
    _write_samples(output_dir, samples)
    def maximum(name):
        values = [sample[name] for sample in samples if sample.get(name) is not None]
        return max(values) if values else None

    max_rss_bytes = maximum("rss_bytes")
    max_fd_count = maximum("fd_count")
    final_threads = samples[-1]["threads"]
    payload = {
        "target_node": args.target_node,
        "pid": pid,
        "sample_count": len(samples),
        "max_rss_bytes": max_rss_bytes,
        "max_fd_count": max_fd_count,
        "final_threads": final_threads,
        "samples_relpath": str((output_dir / "samples.json")),
    }
    emit_target_event(
        primitive="runtime_resource_profile",
        event="resource_profile_completed",
        payload=payload,
        level="info" if samples else "error",
    )
    print(
        "target_node={target_node} pid={pid} sample_count={sample_count} max_rss_bytes={max_rss_bytes} "
        "max_fd_count={max_fd_count} final_threads={final_threads}".format(**payload)
    )
    return 0 if samples else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
