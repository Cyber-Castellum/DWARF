#!/usr/bin/env python3

import argparse
import json
import os
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
    nodes = body.get("haskell_nodes")
    if not isinstance(nodes, list):
        nodes = body.get("nodes")
    if not isinstance(nodes, list):
        raise RuntimeError(f"runtime metadata does not contain haskell_nodes or nodes: {runtime_metadata_path}")
    for node in nodes:
        if node.get("name") == target_node or node.get("id") == target_node:
            return node
    raise RuntimeError(f"runtime metadata missing target node {target_node!r}: {runtime_metadata_path}")


def _proc_exists(pid: int, proc_root: Path) -> bool:
    return (proc_root / str(pid) / "status").exists()


def _scan_for_runtime_pid(node: dict) -> int:
    result = subprocess.run(
        ["ps", "-eo", "pid=,comm=,args="],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError("runtime_resource_profile could not inspect process table")
    name = str(node.get("name") or "")
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
        if comm != "cardano-node":
            continue
        if socket_hint and socket_hint in args:
            return int(pid_text)
        if db_hint and db_hint in args:
            return int(pid_text)
    raise RuntimeError(f"runtime_resource_profile could not resolve runtime pid for node {name!r}")


def _resolve_docker_pid(node: dict) -> int | None:
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
    return pid or None


def resolve_target_pid(runtime_metadata_path: Path, target_node: str, *, proc_root: Path = Path("/proc")) -> int:
    node = _load_runtime_node(runtime_metadata_path, target_node)
    docker_pid = _resolve_docker_pid(node)
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
    return _scan_for_runtime_pid(node)


def _parse_status(status_text: str) -> dict:
    body = {}
    for line in status_text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        body[key.strip()] = value.strip()
    rss_text = body.get("VmRSS", "0 kB").split()[0]
    threads_text = body.get("Threads", "0").split()[0]
    vol_text = body.get("voluntary_ctxt_switches", "0").split()[0]
    nonvol_text = body.get("nonvoluntary_ctxt_switches", "0").split()[0]
    return {
        "rss_bytes": int(rss_text) * 1024,
        "threads": int(threads_text),
        "voluntary_ctxt_switches": int(vol_text),
        "nonvoluntary_ctxt_switches": int(nonvol_text),
        "state": body.get("State", ""),
        "name": body.get("Name", ""),
    }


def collect_samples(*, pid: int, sample_count: int, sample_interval_seconds: float, proc_root: Path = Path("/proc")) -> list[dict]:
    samples = []
    for idx in range(sample_count):
        status_path = proc_root / str(pid) / "status"
        fd_path = proc_root / str(pid) / "fd"
        if not status_path.exists():
            raise RuntimeError(f"missing proc status for pid {pid}: {status_path}")
        status = _parse_status(status_path.read_text(encoding="utf-8"))
        fd_count = len(list(fd_path.iterdir())) if fd_path.exists() else 0
        samples.append(
            {
                "sample_index": idx,
                "pid": pid,
                "ts_epoch_s": time.time(),
                "fd_count": fd_count,
                **status,
            }
        )
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
    max_rss_bytes = max(sample["rss_bytes"] for sample in samples)
    max_fd_count = max(sample["fd_count"] for sample in samples)
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
