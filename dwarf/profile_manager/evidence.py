import json
import os
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_EVIDENCE_ROOT = Path(__file__).resolve().parents[2] / "agent" / "testing" / "devnet-profiles"
EVIDENCE_ENV = "ADA2_PROFILE_MANAGER_EVIDENCE_ROOT"


def evidence_root():
    override = os.environ.get(EVIDENCE_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_EVIDENCE_ROOT


def write_evidence(profile_id, action, config_path, config, command_results, limitations=None):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = evidence_root() / profile_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{timestamp}-{action}.md"
    json_path = directory / f"{timestamp}-{action}.json"
    lines = [
        f"# Devnet Profile Evidence - {profile_id}",
        "",
        f"- Action: {action}",
        f"- Timestamp UTC: {timestamp}",
        f"- Config Path: {config_path}",
        f"- Deployment: {config.deployment_name}",
        f"- Remote Host: {config.ssh_user}@{config.host}",
        f"- Remote Base Path: {config.remote_base_path}",
        "",
        "## Commands",
        "",
    ]
    if not command_results:
        lines.append("- No remote commands executed.")
    for result in command_results:
        lines.extend(
            [
                "```bash",
                result.rendered_command,
                "```",
                f"- Exit Code: {result.returncode}",
                "",
                "### Stdout",
                "",
                "```text",
                (result.stdout or "").strip(),
                "```",
                "",
                "### Stderr",
                "",
                "```text",
                (result.stderr or "").strip(),
                "```",
                "",
            ]
        )
    if limitations:
        lines.extend(["## Limitations", ""])
        for limitation in limitations:
            lines.append(f"- {limitation}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sidecar = {
        "profile_id": profile_id,
        "action": action,
        "timestamp_utc": timestamp,
        "config_path": str(config_path),
        "deployment": config.deployment_name,
        "remote_host": f"{config.ssh_user}@{config.host}",
        "remote_base_path": config.remote_base_path,
        "limitations": limitations or [],
        "commands": [
            {
                "rendered_command": result.rendered_command,
                "exit_code": result.returncode,
                "stdout": result.stdout or "",
                "stderr": result.stderr or "",
            }
            for result in command_results
        ],
    }
    json_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
    return path
