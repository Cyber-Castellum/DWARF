import json
import os
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SMOKE_ROOT = Path(__file__).resolve().parents[1] / "smoke-tests"
DEFAULT_SMOKE_EVIDENCE_ROOT = Path(__file__).resolve().parents[2] / "agent" / "testing" / "smoke-tests"
SMOKE_EVIDENCE_ENV = "ADA2_PROFILE_MANAGER_SMOKE_EVIDENCE_ROOT"


@dataclass(frozen=True)
class SmokeTest:
    id: str
    label: str
    category: str
    status: str
    source_reference: str
    working_directory: str
    timeout_seconds: int
    environment: dict
    commands: tuple
    limitations: tuple
    safety_notes: tuple

    @classmethod
    def from_dict(cls, data):
        return cls(
            id=data["id"],
            label=data["label"],
            category=data["category"],
            status=data["status"],
            source_reference=data.get("source_reference", ""),
            working_directory=data["working_directory"],
            timeout_seconds=int(data["timeout_seconds"]),
            environment=dict(data.get("environment", {})),
            commands=tuple(data.get("commands", [])),
            limitations=tuple(data.get("limitations", [])),
            safety_notes=tuple(data.get("safety_notes", [])),
        )


def smoke_evidence_root():
    override = os.environ.get(SMOKE_EVIDENCE_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_SMOKE_EVIDENCE_ROOT


def load_smoke_tests():
    tests = []
    for path in sorted(SMOKE_ROOT.glob("*.json")):
        tests.append(SmokeTest.from_dict(json.loads(path.read_text(encoding="utf-8"))))
    return tests


def find_smoke_test(smoke_id):
    for smoke in load_smoke_tests():
        if smoke.id == smoke_id:
            return smoke
    raise KeyError(f"Unknown smoke test: {smoke_id}")


def smoke_list_text():
    lines = ["Smoke tests:"]
    for smoke in load_smoke_tests():
        lines.append(f"- {smoke.id}: {smoke.label} ({smoke.status}; {smoke.category})")
    return "\n".join(lines) + "\n"


def smoke_status_text(smoke):
    lines = [
        f"Smoke Test: {smoke.id}",
        f"Label: {smoke.label}",
        f"Category: {smoke.category}",
        f"Status: {smoke.status}",
        f"Source Reference: {smoke.source_reference or 'not specified'}",
        f"Working Directory: {smoke.working_directory}",
        f"Timeout Seconds: {smoke.timeout_seconds}",
        "",
        "Environment:",
    ]
    if smoke.environment:
        lines.extend(f"- {key}={value}" for key, value in sorted(smoke.environment.items()))
    else:
        lines.append("- none")
    lines.extend(["", "Commands:"])
    lines.extend(f"- {command}" for command in smoke.commands)
    lines.extend(["", "Limitations:"])
    lines.extend(f"- {limitation}" for limitation in smoke.limitations)
    lines.extend(["", "Safety Notes:"])
    lines.extend(f"- {note}" for note in smoke.safety_notes)
    return "\n".join(lines) + "\n"


def smoke_remote_command(smoke):
    exports = "\n".join(
        f"export {key}={shlex.quote(str(value))}" for key, value in sorted(smoke.environment.items())
    )
    command_lines = "\n".join(smoke.commands)
    return f"""set -e
echo "SMOKE_ID={smoke.id}"
echo "SMOKE_LABEL={smoke.label}"
echo "SMOKE_CATEGORY={smoke.category}"
echo "SMOKE_STATUS={smoke.status}"
echo "SOURCE_REFERENCE={smoke.source_reference}"
echo "WORKING_DIRECTORY={smoke.working_directory}"
echo "TIMEOUT_SECONDS={smoke.timeout_seconds}"
echo "No public Cardano network is contacted by this smoke manifest."
cd {shlex.quote(smoke.working_directory)}
export PATH=/home/nigel/.local/bin:$PATH
{exports}
{command_lines}
"""


def write_smoke_evidence(smoke, action, config_path, config, command_result, dry_run=False):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = smoke_evidence_root() / smoke.id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{timestamp}-{action}.md"
    json_path = directory / f"{timestamp}-{action}.json"
    lines = [
        f"# Smoke Test Evidence - {smoke.id}",
        "",
        f"- Action: {action}",
        f"- Timestamp UTC: {timestamp}",
        f"- Config Path: {config_path}",
        f"- Deployment: {config.deployment_name}",
        f"- Remote Host: {config.ssh_user}@{config.host}",
        f"- Working Directory: {smoke.working_directory}",
        f"- Timeout Seconds: {smoke.timeout_seconds}",
        f"- Dry Run: {dry_run}",
        "",
        "## Manifest",
        "",
        smoke_status_text(smoke),
        "## Command",
        "",
        "```bash",
        command_result.rendered_command,
        "```",
        f"- Exit Code: {command_result.returncode}",
        "",
        "## Stdout",
        "",
        "```text",
        (command_result.stdout or "").strip(),
        "```",
        "",
        "## Stderr",
        "",
        "```text",
        (command_result.stderr or "").strip(),
        "```",
        "",
        "## Disposition",
        "",
        "Smoke evidence only. This run does not establish a finding, accepted risk, risk score, or mitigation sufficiency.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sidecar = {
        "smoke_id": smoke.id,
        "label": smoke.label,
        "category": smoke.category,
        "status": smoke.status,
        "source_reference": smoke.source_reference,
        "action": action,
        "timestamp_utc": timestamp,
        "config_path": str(config_path),
        "deployment": config.deployment_name,
        "remote_host": f"{config.ssh_user}@{config.host}",
        "working_directory": smoke.working_directory,
        "timeout_seconds": smoke.timeout_seconds,
        "dry_run": dry_run,
        "limitations": list(smoke.limitations),
        "safety_notes": list(smoke.safety_notes),
        "commands": [
            {
                "rendered_command": command_result.rendered_command,
                "exit_code": command_result.returncode,
                "stdout": command_result.stdout or "",
                "stderr": command_result.stderr or "",
            }
        ],
    }
    json_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
    return path
