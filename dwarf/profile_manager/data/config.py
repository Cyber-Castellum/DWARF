"""Pure config and host-discovery helpers for the dashboard data layer."""
from __future__ import annotations

import socket
import subprocess
from pathlib import Path

from profile_manager.config import config_exists, config_path, load_config


def _discover_project_root(source_path=None):
    source = Path(source_path) if source_path else Path(__file__).resolve()
    candidates = []
    if len(source.parents) >= 2:
        candidates.append(source.parents[1])
    if len(source.parents) >= 3:
        candidates.append(source.parents[2])
    for candidate in candidates:
        if (
            (candidate / "agent").exists()
            or (candidate / "user").exists()
            or (candidate / "codebases").exists()
        ):
            return candidate
    return candidates[0] if candidates else source.parent


def default_dashboard_dir():
    from profile_manager.dashboard import DASHBOARD_ROOT

    return DASHBOARD_ROOT


def _local_interface_urls(port):
    urls = [f"http://127.0.0.1:{port}/"]
    seen = {"127.0.0.1"}
    try:
        host_name = socket.gethostname()
        addresses = socket.getaddrinfo(host_name, None, family=socket.AF_INET)
    except OSError:
        addresses = []
    for address in addresses:
        ip = address[4][0]
        if ip in seen or ip.startswith("169.254."):
            continue
        seen.add(ip)
        urls.append(f"http://{ip}:{port}/")
    try:
        completed = subprocess.run(["ifconfig"], text=True, capture_output=True, check=False)
    except OSError:
        completed = None
    if completed and completed.stdout:
        for line in completed.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] == "inet":
                ip = parts[1]
                if ip not in seen and not ip.startswith("169.254."):
                    seen.add(ip)
                    urls.append(f"http://{ip}:{port}/")
    return urls


def _safe_project_file(relative):
    from profile_manager.dashboard import PROJECT_ROOT

    if not relative or relative.startswith("/") or ".." in Path(relative).parts:
        return None, (400, "text/plain; charset=utf-8", b"invalid path\n")
    target = (PROJECT_ROOT / relative).resolve()
    project_root = PROJECT_ROOT.resolve()
    if target != project_root and project_root not in target.parents:
        return None, (400, "text/plain; charset=utf-8", b"invalid path\n")
    if not target.is_file():
        return None, (404, "text/plain; charset=utf-8", b"not found\n")
    return target, None


def _config_payload():
    if not config_exists():
        return {
            "present": False,
            "message": "Config missing. Run intake first.",
            "path": str(config_path()),
        }
    cfg = load_config()
    return {
        "present": True,
        "deployment_name": cfg.deployment_name,
        "host": cfg.host,
        "ssh_user": cfg.ssh_user,
        "remote_base_path": cfg.remote_base_path,
        "path": str(config_path()),
    }
