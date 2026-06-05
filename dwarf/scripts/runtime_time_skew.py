from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_resource_abuse_fault import (
    _default_observation_overrides,
    _ensure_tmux_session_absent,
    _find_node,
    _kill_tmux_session,
    _read_metadata,
    _write_metadata,
    _launch_haskell_node,
)
from runtime_substrate_common import run_command, wait_for_nodes_healthy, write_json


DEFAULT_LIBFAKETIME = "/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_result_body(*, skew_seconds: int, host_time_at_apply: str, host_time_at_release: str) -> dict:
    return {
        "skew_applied_seconds": int(skew_seconds),
        "host_time_at_apply": str(host_time_at_apply),
        "host_time_at_release": str(host_time_at_release),
    }


def _launch_skewed_haskell_node(node: dict, *, skew_seconds: int, libfaketime_path: str) -> None:
    _ensure_tmux_session_absent(str(node["session"]))
    command_parts = [
        str(node["resolved_binary"]),
        "run",
        "--config",
        str(node["config_path"]),
        "--topology",
        str(node["topology_path"]),
        "--database-path",
        str(node["db_dir"]),
        "--socket-path",
        str(node["socket_path"]),
        "--port",
        str(node["port"]),
        "--host-addr",
        "127.0.0.1",
    ]
    sign = "+" if int(skew_seconds) >= 0 else ""
    command = (
        f"echo $$ > {json.dumps(str(node['pid_file']))}; "
        f"export LD_PRELOAD={json.dumps(libfaketime_path)}; "
        f"export FAKETIME={json.dumps(f'{sign}{int(skew_seconds)}s')}; "
        f"exec {' '.join(json.dumps(part) for part in command_parts)} 2>&1 | tee -a {json.dumps(str(node['log_path']))}"
    )
    result = run_command(["tmux", "new-session", "-d", "-s", str(node["session"]), f"bash -lc {json.dumps(command)}"])
    if result.returncode != 0:
        raise RuntimeError(f"failed to launch skewed node {node['id']}: {result.stderr or result.stdout}")


def _spawn_release_session(*, session: str, config_path: Path, duration_seconds: float) -> None:
    _ensure_tmux_session_absent(session)
    command = (
        f"sleep {duration_seconds}; "
        f"exec python3 {json.dumps(str(SCRIPT_DIR / 'runtime_time_skew.py'))} --config {json.dumps(str(config_path))} --mode release"
    )
    result = run_command(["tmux", "new-session", "-d", "-s", session, f"bash -lc {json.dumps(command)}"])
    if result.returncode != 0:
        raise RuntimeError(f"failed to launch time-skew release session {session}: {result.stderr or result.stdout}")


def apply_time_skew(
    *,
    runtime_metadata_path: Path,
    output_dir: Path,
    target_node: str,
    skew_seconds: int,
    duration_seconds: float,
    healthy_timeout_seconds: float,
    libfaketime_path: str,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    if not Path(libfaketime_path).exists():
        raise FileNotFoundError(f"missing libfaketime shared object: {libfaketime_path}")
    metadata = _read_metadata(runtime_metadata_path)
    node = _find_node(metadata, target_node)
    if str(node.get("compose_mode") or metadata.get("compose_mode") or "") == "docker":
        raise ValueError("runtime_time_skew currently supports host-mode composed substrates only")
    _kill_tmux_session(str(node["session"]))
    _launch_skewed_haskell_node(node, skew_seconds=skew_seconds, libfaketime_path=libfaketime_path)
    health = wait_for_nodes_healthy([node], timeout_seconds=healthy_timeout_seconds)
    node_healthy = bool(health and health[0].get("healthy"))
    overrides = dict(metadata.get("observation_overrides") or {})
    overrides.update(_default_observation_overrides(metadata))
    metadata["observation_overrides"] = overrides
    apply_time = _utc_now()
    result_path = output_dir / "result.json"
    report = {
        "mode": "time_skew",
        "runtime_metadata_path": str(runtime_metadata_path),
        "result": build_result_body(
            skew_seconds=skew_seconds,
            host_time_at_apply=apply_time,
            host_time_at_release=apply_time,
        ),
    }
    write_json(result_path, report)
    release_config_path = output_dir / "runtime_time_skew-release.json"
    release_config = {
        "runtime_metadata_path": str(runtime_metadata_path),
        "output_dir": str(output_dir),
        "target_node": target_node,
        "skew_seconds": int(skew_seconds),
        "healthy_timeout_seconds": float(healthy_timeout_seconds),
    }
    write_json(release_config_path, release_config)
    release_session = f"{metadata['compose_project']}-timeskew-{target_node}"
    _spawn_release_session(session=release_session, config_path=release_config_path, duration_seconds=duration_seconds)
    metadata.setdefault("faults", []).append(
        {"kind": "time_skew", "target_node_id": target_node, "release_session": release_session}
    )
    metadata.setdefault("aux_sessions", []).append(
        {"id": f"time-skew-{target_node}", "kind": "time_skew_release", "session": release_session}
    )
    _write_metadata(runtime_metadata_path, metadata)
    return report if node_healthy else report


def release_time_skew(*, runtime_metadata_path: Path, output_dir: Path, target_node: str, skew_seconds: int, healthy_timeout_seconds: float) -> dict:
    metadata = _read_metadata(runtime_metadata_path)
    node = _find_node(metadata, target_node)
    _kill_tmux_session(str(node["session"]))
    _launch_haskell_node(node)
    wait_for_nodes_healthy([node], timeout_seconds=healthy_timeout_seconds)
    result_path = output_dir / "result.json"
    body = json.loads(result_path.read_text(encoding="utf-8"))
    body["result"] = build_result_body(
        skew_seconds=skew_seconds,
        host_time_at_apply=body.get("result", {}).get("host_time_at_apply", _utc_now()),
        host_time_at_release=_utc_now(),
    )
    write_json(result_path, body)
    metadata["aux_sessions"] = [
        item for item in list(metadata.get("aux_sessions") or []) if item.get("kind") != "time_skew_release"
    ]
    metadata["faults"] = [item for item in list(metadata.get("faults") or []) if item.get("kind") != "time_skew"]
    _write_metadata(runtime_metadata_path, metadata)
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=("apply", "release"), required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.mode == "apply":
        report = apply_time_skew(
            runtime_metadata_path=Path(config["runtime_metadata_path"]),
            output_dir=Path(config["output_dir"]),
            target_node=str(config["target_node"]),
            skew_seconds=int(config["skew_seconds"]),
            duration_seconds=float(config.get("duration_seconds", 20)),
            healthy_timeout_seconds=float(config.get("healthy_timeout_seconds", 90)),
            libfaketime_path=str(config.get("libfaketime_path", DEFAULT_LIBFAKETIME)),
        )
    else:
        report = release_time_skew(
            runtime_metadata_path=Path(config["runtime_metadata_path"]),
            output_dir=Path(config["output_dir"]),
            target_node=str(config["target_node"]),
            skew_seconds=int(config["skew_seconds"]),
            healthy_timeout_seconds=float(config.get("healthy_timeout_seconds", 90)),
        )
    print(f"mode=time_skew target_node={config.get('target_node', '')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
