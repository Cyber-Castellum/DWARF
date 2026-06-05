"""Pure health/runtime-tip helpers for the dashboard data layer."""
from __future__ import annotations

import json
import re
import socket
import subprocess

from profile_manager.config import config_exists, load_config
from profile_manager.data.files import _latest_files, _read_text
from profile_manager.inspect import inspect_health_command
from profile_manager.profiles import load_profiles
from profile_manager.remote import CommandResult, ssh_command


def _local_ipv4_addresses() -> set[str]:
    """Return every IPv4 address bound on a local interface, plus loopback.

    Used by _is_local_host to decide whether the dashboard process can reach
    its target via direct subprocess exec instead of SSH-loopback.
    """
    addresses: set[str] = {"127.0.0.1"}
    try:
        addresses.add(socket.gethostbyname(socket.gethostname()))
    except (OSError, socket.gaierror):
        pass
    try:
        result = subprocess.run(
            ["ip", "-4", "addr", "show"],
            text=True, capture_output=True, timeout=2, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return addresses
    for match in re.finditer(r"\binet (\d+\.\d+\.\d+\.\d+)", result.stdout or ""):
        addresses.add(match.group(1))
    return addresses


def _is_local_host(host: str) -> bool:
    """True iff `host` resolves to an IP bound on this machine.

    The deployed dashboard can run on the same host as the configured target;
    SSH-to-itself works in principle but
    requires loopback authorized_keys and a path-portable ssh_key_path.
    Direct exec is simpler and matches the operator's mental model: the
    dashboard inspects the substrate it lives on. The macOS workstation
    case still uses the SSH path because config.host won't resolve local.
    """
    if not host:
        return False
    if host in {"localhost", "127.0.0.1"}:
        return True
    locals_ = _local_ipv4_addresses()
    if host in locals_:
        return True
    try:
        return socket.gethostbyname(host) in locals_
    except (OSError, socket.gaierror):
        return False


def _local_command(remote_command: str, *, timeout: int | None = None) -> CommandResult:
    """Run a shell command locally via bash -c and return CommandResult."""
    try:
        completed = subprocess.run(
            ["bash", "-c", remote_command],
            text=True, capture_output=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # exc.stdout/exc.stderr are bytes when capture_output=True even with
        # text=True; coerce defensively before string concatenation.
        def _decode(value):
            if value is None:
                return ""
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            return str(value)
        return CommandResult(
            returncode=124,
            stdout=_decode(exc.stdout),
            stderr=_decode(exc.stderr) + f"\n[timed out after {timeout}s]",
            rendered_command=f"bash -c <inspect-health command, {len(remote_command)} chars>",
        )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        rendered_command=f"bash -c <inspect-health command, {len(remote_command)} chars>",
    )


def _latest_profile_health():
    from profile_manager.dashboard import PROJECT_ROOT

    root = PROJECT_ROOT / "agent" / "testing" / "devnet-profiles" / "profile-a-haskell-peersharing-disabled"
    files = _latest_files(root, ["*-inspect-health.md"], count=1)
    if not files:
        return None, ""
    return files[0], _read_text(files[0], limit=200000)


def _extract_health_value(body, key):
    prefix = f"{key}="
    value = "unknown"
    for line in body.splitlines():
        if line.startswith(prefix):
            value = line.split("=", 1)[1].strip()
    return value


def _extract_tip_json(body):
    marker = "## tip"
    if marker not in body:
        return {}
    after = body.rsplit(marker, 1)[1]
    start = after.find("{")
    end = after.find("}\n")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(after[start : end + 1])
    except json.JSONDecodeError:
        return {}


def _health_from_body(body, evidence_path=None, returncode=0, stderr=""):
    from profile_manager.dashboard import PROJECT_ROOT

    tip = _extract_tip_json(body)
    return {
        "returncode": returncode,
        "stderr": stderr,
        "stdout": body,
        "evidence_path": str(evidence_path.relative_to(PROJECT_ROOT)) if evidence_path else None,
        "parsed": {
            "cardano_node_processes": _extract_health_value(body, "cardano_node_processes"),
            "socket_count": _extract_health_value(body, "socket_count"),
            "listener_count": _extract_health_value(body, "listener_count"),
            "loopback_only": _extract_health_value(body, "loopback_only"),
            "tip_block": tip.get("block", "unknown"),
            "sync_progress": tip.get("syncProgress", "unknown"),
        },
    }


def _live_health(profile_id):
    if not config_exists():
        return {
            "enabled": False,
            "error": "config missing",
            "health": _health_from_body(""),
        }
    profile = next((item for item in load_profiles() if item.id == profile_id), None)
    if not profile:
        return {
            "enabled": False,
            "error": f"profile not found: {profile_id}",
            "health": _health_from_body(""),
        }
    cfg = load_config()
    command = inspect_health_command(profile.remote_runtime_root)
    # Slice 27: when the dashboard runs on the same host it inspects, run
    # the inspect-health script directly via subprocess instead of going
    # through SSH-to-self (which requires loopback authorized_keys and
    # path-portable ssh_key_path — neither holds on the deployed box).
    transport = "local" if _is_local_host(cfg.host) else "ssh"
    if transport == "local":
        result = _local_command(command, timeout=30)
    else:
        result = ssh_command(cfg, command, timeout=30)
    return {
        "enabled": True,
        "profile_id": profile.id,
        "runtime_root": profile.remote_runtime_root,
        "transport": transport,
        "health": _health_from_body(result.stdout, returncode=result.returncode, stderr=result.stderr),
    }
