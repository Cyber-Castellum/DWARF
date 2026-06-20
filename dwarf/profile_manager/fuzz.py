import json
import os
import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from profile_manager.profiles import load_profiles


FUZZ_ROOT = Path(__file__).resolve().parents[1] / "fuzz-tests"
DEFAULT_FUZZ_EVIDENCE_ROOT = Path(__file__).resolve().parents[2] / "agent" / "testing" / "fuzz"
FUZZ_EVIDENCE_ENV = "ADA2_PROFILE_MANAGER_FUZZ_EVIDENCE_ROOT"


@dataclass(frozen=True)
class FuzzTest:
    id: str
    label: str
    category: str
    target_package: str
    profile_required: str | None
    safety_level: str
    requires_deployed_testnet: bool
    touches_public_network: bool
    timeout_seconds: int
    working_directory: str
    environment: dict
    commands: tuple
    related_candidates: tuple
    related_scenarios: tuple
    limitations: tuple
    evidence_outputs: tuple
    disposition: str = "candidate-evidence-only"

    @classmethod
    def from_dict(cls, data):
        return cls(
            id=data["id"],
            label=data["label"],
            category=data["category"],
            target_package=data["target_package"],
            profile_required=data.get("profile_required"),
            safety_level=data["safety_level"],
            requires_deployed_testnet=bool(data["requires_deployed_testnet"]),
            touches_public_network=bool(data["touches_public_network"]),
            timeout_seconds=int(data["timeout_seconds"]),
            working_directory=data["working_directory"],
            environment=dict(data.get("environment", {})),
            commands=tuple(data.get("commands", [])),
            related_candidates=tuple(data.get("related_candidates", [])),
            related_scenarios=tuple(data.get("related_scenarios", [])),
            limitations=tuple(data.get("limitations", [])),
            evidence_outputs=tuple(data.get("evidence_outputs", [])),
            disposition=data.get("disposition", "candidate-evidence-only"),
        )


def fuzz_evidence_root():
    override = os.environ.get(FUZZ_EVIDENCE_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_FUZZ_EVIDENCE_ROOT


def load_fuzz_tests():
    if not FUZZ_ROOT.exists():
        return []

    tests = []
    for path in sorted(FUZZ_ROOT.glob("*.json")):
        tests.append(FuzzTest.from_dict(json.loads(path.read_text(encoding="utf-8"))))
    return tests


def find_fuzz_test(fuzz_id):
    for fuzz in load_fuzz_tests():
        if fuzz.id == fuzz_id:
            return fuzz
    raise KeyError(f"Unknown fuzz test: {fuzz_id}")


def fuzz_list_text():
    lines = ["Fuzz tests:"]
    for fuzz in load_fuzz_tests():
        profile = fuzz.profile_required or "none"
        lines.append(f"- {fuzz.id}: {fuzz.label} ({fuzz.category}; target={fuzz.target_package}; profile={profile})")
    return "\n".join(lines) + "\n"


def _yes_no(value):
    return "yes" if value else "no"


def fuzz_status_text(fuzz):
    lines = [
        f"Fuzz Test: {fuzz.id}",
        f"Label: {fuzz.label}",
        f"Category: {fuzz.category}",
        f"Target Package: {fuzz.target_package}",
        f"Profile Required: {fuzz.profile_required or 'none'}",
        f"Safety Level: {fuzz.safety_level}",
        f"Requires Deployed Testnet: {_yes_no(fuzz.requires_deployed_testnet)}",
        f"Touches Public Network: {_yes_no(fuzz.touches_public_network)}",
        f"Timeout Seconds: {fuzz.timeout_seconds}",
        f"Working Directory: {fuzz.working_directory}",
        f"Disposition: {fuzz.disposition}",
        "",
        "Environment:",
    ]
    if fuzz.environment:
        lines.extend(f"- {key}={value}" for key, value in sorted(fuzz.environment.items()))
    else:
        lines.append("- none")
    lines.extend(["", "Commands:"])
    lines.extend(f"- {command}" for command in fuzz.commands)
    lines.extend(["", "Related Candidates:"])
    lines.extend(f"- {candidate}" for candidate in fuzz.related_candidates)
    lines.extend(["", "Related Scenarios:"])
    lines.extend(f"- {scenario}" for scenario in fuzz.related_scenarios)
    lines.extend(["", "Evidence Outputs:"])
    lines.extend(f"- {output}" for output in fuzz.evidence_outputs)
    lines.extend(["", "Limitations:"])
    lines.extend(f"- {limitation}" for limitation in fuzz.limitations)
    return "\n".join(lines) + "\n"


def validate_fuzz_test(fuzz):
    errors = []
    if fuzz.touches_public_network:
        errors.append("fuzz tests must not touch public Cardano networks by default")
    if fuzz.safety_level not in {"safe", "controlled", "approval-required", "destructive-copy-state"}:
        errors.append(f"unknown safety_level: {fuzz.safety_level}")
    if fuzz.requires_deployed_testnet and not fuzz.profile_required:
        errors.append("requires_deployed_testnet requires profile_required")
    for command in fuzz.commands:
        lowered = command.lower()
        if "cloudflare" in lowered or "nextcloud" in lowered:
            errors.append("fuzz commands must not target Cloudflare or Nextcloud")
        if "mainnet" in lowered or "preprod" in lowered or "preview" in lowered:
            errors.append("fuzz commands must not target public Cardano networks")
    return tuple(errors)


def _override_campaign_seconds(command: str, seconds_override: int | None) -> str:
    if seconds_override is None:
        return command
    return re.sub(r"(--seconds)\s+\d+\b", rf"\1 {int(seconds_override)}", command)


def _normalize_remote_command(command: str) -> str:
    command = command.replace("python3 dwarf/scripts/", "python3 scripts/")
    command = command.replace("${HOME}/dwarf-fw/dwarf/", "${HOME}/dwarf-fw/")
    return command


def fuzz_remote_command(fuzz, *, seconds_override: int | None = None):
    exports = "\n".join(
        f"export {key}={shlex.quote(str(value))}" for key, value in sorted(fuzz.environment.items())
    )
    command_lines = "\n".join(
        _normalize_remote_command(_override_campaign_seconds(command, seconds_override))
        for command in fuzz.commands
    )
    return f"""set -e
echo "FUZZ_ID={fuzz.id}"
echo "FUZZ_LABEL={fuzz.label}"
echo "FUZZ_CATEGORY={fuzz.category}"
echo "TARGET_PACKAGE={fuzz.target_package}"
echo "PROFILE_REQUIRED={fuzz.profile_required or 'none'}"
echo "SAFETY_LEVEL={fuzz.safety_level}"
echo "DISPOSITION={fuzz.disposition}"
echo "WORKING_DIRECTORY={fuzz.working_directory}"
echo "TIMEOUT_SECONDS={fuzz.timeout_seconds}"
echo "No public Cardano network is contacted by this fuzz manifest."
cd {shlex.quote(fuzz.working_directory)}
export PATH=${HOME}/.local/bin:$PATH
{exports}
{command_lines}
"""


def manifest_target_implementation(*parts):
    text = " ".join(str(part) for part in parts if part).lower()
    if "amaru" in text:
        return "amaru"
    return "cardano-node"


def manifest_runtime_and_profile(working_directory, explicit_profile_id=None):
    if explicit_profile_id:
        return "devnet", explicit_profile_id
    working_directory = Path(working_directory)
    for profile in load_profiles():
        runtime_root = Path(profile.remote_runtime_root)
        if working_directory == runtime_root or runtime_root in working_directory.parents:
            return "devnet", profile.id
    return "library", None


def fuzz_v1_scenario_bytes(fuzz, *, seconds_override: int | None = None):
    raw_command = fuzz_remote_command(fuzz, seconds_override=seconds_override)
    command = f"bash -lc {shlex.quote(raw_command)}"
    runtime, profile_id = manifest_runtime_and_profile(fuzz.working_directory, fuzz.profile_required)
    timeout_seconds = fuzz.timeout_seconds
    if seconds_override is not None:
        timeout_seconds = max(timeout_seconds, int(seconds_override) + 300)
    body = {
        "spec_version": "v1",
        "id": fuzz.id,
        "title": fuzz.label,
        "authors": ["dwarf"],
        "tags": ["fuzz", fuzz.category, fuzz.target_package],
        "target": {
            "implementation": manifest_target_implementation(
                fuzz.id,
                fuzz.label,
                fuzz.target_package,
                fuzz.working_directory,
                raw_command,
            ),
            "version": "any",
        },
        "runtime": runtime,
        "profile": profile_id,
        "seed": "0xD00D0001",
        "load": [
            {
                "primitive": "load_shell_command",
                "command": command,
                "timeout_seconds": timeout_seconds,
                "expect_exit": 0,
            }
        ],
        "probes": [],
        "assertions": [{"primitive": "load_events_are_ok", "min_completed": 1}],
        "teardown": [],
    }
    return (json.dumps(body, indent=2) + "\n").encode("utf-8")


def write_fuzz_evidence(fuzz, action, config_path, config, command_result, dry_run=False):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = fuzz_evidence_root() / fuzz.id
    artifact_dir = directory / f"{timestamp}-artifacts"
    directory.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = directory / f"{timestamp}-{action}.md"
    json_path = directory / f"{timestamp}-{action}.json"
    stdout_path = artifact_dir / "stdout.log"
    stderr_path = artifact_dir / "stderr.log"
    stdout_path.write_text(command_result.stdout or "", encoding="utf-8")
    stderr_path.write_text(command_result.stderr or "", encoding="utf-8")

    lines = [
        f"# Fuzz Test Evidence - {fuzz.id}",
        "",
        f"- Action: {action}",
        f"- Timestamp UTC: {timestamp}",
        f"- Config Path: {config_path}",
        f"- Deployment: {config.deployment_name}",
        f"- Remote Host: {config.ssh_user}@{config.host}",
        f"- Fuzz ID: {fuzz.id}",
        f"- Target Package: {fuzz.target_package}",
        f"- Category: {fuzz.category}",
        f"- Profile Required: {fuzz.profile_required or 'none'}",
        f"- Working Directory: {fuzz.working_directory}",
        f"- Timeout Seconds: {fuzz.timeout_seconds}",
        f"- Safety Level: {fuzz.safety_level}",
        f"- Requires Deployed Testnet: {_yes_no(fuzz.requires_deployed_testnet)}",
        f"- Touches Public Network: {_yes_no(fuzz.touches_public_network)}",
        f"- Dry Run: {dry_run}",
        f"- Disposition: {fuzz.disposition}",
        f"- Artifact Directory: {artifact_dir}",
        "",
        "## Manifest",
        "",
        fuzz_status_text(fuzz),
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
        "Fuzz evidence only. This run does not establish a finding, accepted risk, risk score, or mitigation sufficiency.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sidecar = {
        "fuzz_id": fuzz.id,
        "label": fuzz.label,
        "category": fuzz.category,
        "target_package": fuzz.target_package,
        "profile_required": fuzz.profile_required,
        "safety_level": fuzz.safety_level,
        "requires_deployed_testnet": fuzz.requires_deployed_testnet,
        "touches_public_network": fuzz.touches_public_network,
        "action": action,
        "timestamp_utc": timestamp,
        "config_path": str(config_path),
        "deployment": config.deployment_name,
        "remote_host": f"{config.ssh_user}@{config.host}",
        "working_directory": fuzz.working_directory,
        "environment": fuzz.environment,
        "timeout_seconds": fuzz.timeout_seconds,
        "dry_run": dry_run,
        "disposition": fuzz.disposition,
        "related_candidates": list(fuzz.related_candidates),
        "related_scenarios": list(fuzz.related_scenarios),
        "limitations": list(fuzz.limitations),
        "evidence_outputs": list(fuzz.evidence_outputs),
        "artifact_dir": str(artifact_dir),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "crash_found": command_result.returncode != 0,
        "repro_command": fuzz_remote_command(fuzz),
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
