from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_substrate_common import run_command, write_json


def _ssh_base_command(host: dict) -> list[str]:
    command = ["ssh", "-n", "-o", "BatchMode=yes"]
    ssh_key_path = host.get("ssh_key_path")
    if ssh_key_path:
        command.extend(["-i", str(ssh_key_path)])
    command.append(str(host["ssh_target"]))
    return command


def _run_remote_command(host: dict, script: str, runner=run_command):
    return runner(_ssh_base_command(host) + [f"bash -lc {json.dumps(script)}"])


def teardown_substrate(*, runtime_metadata_path: Path, output_dir: Path, runner=run_command) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(runtime_metadata_path.read_text(encoding="utf-8"))
    if str(metadata.get("compose_mode") or "") == "docker":
        if bool(metadata.get("multi_host")):
            hosts = list(metadata.get("hosts") or [])
            host_reports = []
            report_nodes = []
            remaining_sessions = 0
            for host in hosts:
                result = _run_remote_command(
                    host,
                    f"cd {json.dumps(str(host['runtime_root']))} && docker compose --project-name {json.dumps(str(host['compose_project']))} down --volumes --remove-orphans",
                    runner=runner,
                )
                stopped = result.returncode == 0
                if not stopped:
                    remaining_sessions += 1
                host_reports.append(
                    {
                        "id": host["id"],
                        "ssh_target": host.get("ssh_target"),
                        "compose_project": host.get("compose_project"),
                        "runtime_root": host.get("runtime_root"),
                        "stopped": stopped,
                        "stderr": result.stderr.strip(),
                    }
                )
            for node in list(metadata.get("nodes") or []):
                report_nodes.append(
                    {
                        "id": str(node.get("id") or ""),
                        "container_name": node.get("container_name"),
                        "kind": "container",
                        "host_id": node.get("host_id"),
                        "stopped": True,
                        "stderr": "",
                    }
                )
            report = {
                "runtime_metadata_path": str(runtime_metadata_path),
                "compose_mode": "docker",
                "multi_host": True,
                "stopped_count": len(report_nodes),
                "remaining_sessions": remaining_sessions,
                "remaining_session_names": [],
                "hosts": host_reports,
                "nodes": report_nodes,
            }
            write_json(output_dir / "teardown-report.json", report)
            return report
        compose_project = str(metadata.get("compose_project") or "")
        runtime_root = Path(str(metadata.get("runtime_root") or runtime_metadata_path.parent))
        if not compose_project:
            raise RuntimeError(f"docker substrate metadata missing compose_project: {runtime_metadata_path}")
        result = runner(
            ["docker", "compose", "--project-name", compose_project, "down", "--volumes", "--remove-orphans"],
            cwd=runtime_root,
        )
        report = {
            "runtime_metadata_path": str(runtime_metadata_path),
            "compose_mode": "docker",
            "compose_project": compose_project,
            "runtime_root": str(runtime_root),
            "stopped_count": len(list(metadata.get("nodes") or [])),
            "remaining_sessions": 0 if result.returncode == 0 else 1,
            "remaining_session_names": [],
            "nodes": [
                {
                    "id": str(node.get("id") or ""),
                    "container_name": node.get("container_name"),
                    "kind": "container",
                    "stopped": result.returncode == 0,
                    "stderr": result.stderr.strip(),
                }
                for node in list(metadata.get("nodes") or [])
            ],
        }
        write_json(output_dir / "teardown-report.json", report)
        return report
    nodes = list(metadata.get("haskell_nodes") or []) + list(metadata.get("amaru_nodes") or [])
    aux_sessions = list(metadata.get("aux_sessions") or [])
    for fault in list(metadata.get("faults") or []):
        if str(fault.get("kind") or "") == "disk_full_probe":
            fill_file_path = fault.get("fill_file_path")
            if fill_file_path:
                try:
                    fill_path = Path(str(fill_file_path))
                    if fill_path.exists():
                        fill_path.unlink()
                except OSError:
                    pass
    report_nodes = []
    remaining_sessions = []

    for node in nodes + aux_sessions:
        session = str(node["session"])
        result = runner(["tmux", "kill-session", "-t", session])
        stopped = result.returncode == 0
        report_nodes.append(
            {
                "id": node["id"],
                "session": session,
                "kind": node.get("kind", "node"),
                "stopped": stopped,
                "stderr": result.stderr.strip(),
            }
        )
        check = runner(["tmux", "has-session", "-t", session])
        if check.returncode == 0:
            remaining_sessions.append(session)
    report = {
        "runtime_metadata_path": str(runtime_metadata_path),
        "stopped_count": sum(1 for node in report_nodes if node["stopped"]),
        "remaining_sessions": len(remaining_sessions),
        "remaining_session_names": remaining_sessions,
        "nodes": report_nodes,
    }
    write_json(output_dir / "teardown-report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = teardown_substrate(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        output_dir=Path(config["output_dir"]),
    )
    print(f"stopped_count={report['stopped_count']}")
    return 0 if report["remaining_sessions"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
