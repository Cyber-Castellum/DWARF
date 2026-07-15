import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from profile_manager.remote import CommandResult


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "evidence-packages"
MILESTONE_EVIDENCE_ROOT = Path(__file__).resolve().parents[2] / "agent" / "testing" / "milestone-1"


@dataclass(frozen=True)
class EvidencePackage:
    id: str
    label: str
    runnable: bool
    status: str
    candidate_ids: tuple
    evidence_paths: tuple
    blockers: tuple
    read_only_actions: tuple
    runtime_profile: str = ""
    runtime_root: str = ""

    @classmethod
    def from_dict(cls, data):
        return cls(
            id=data["id"],
            label=data["label"],
            runnable=bool(data.get("runnable", False)),
            status=data["status"],
            candidate_ids=tuple(data.get("candidate_ids", [])),
            evidence_paths=tuple(data.get("evidence_paths", [])),
            blockers=tuple(data.get("blockers", [])),
            read_only_actions=tuple(data.get("read_only_actions", [])),
            runtime_profile=data.get("runtime_profile", ""),
            runtime_root=data.get("runtime_root", ""),
        )


def load_evidence_packages():
    packages = []
    for path in sorted(PACKAGE_ROOT.glob("*.yaml")):
        packages.append(EvidencePackage.from_dict(json.loads(path.read_text(encoding="utf-8"))))
    return packages


def find_evidence_package(package_id):
    for package in load_evidence_packages():
        if package.id == package_id:
            return package
    raise KeyError(f"Unknown evidence package: {package_id}")


def evidence_package_list_text():
    lines = ["Evidence packages:"]
    for package in load_evidence_packages():
        runnable = "runnable" if package.runnable else "status-only"
        lines.append(f"- {package.id}: {package.label} ({runnable}; {package.status})")
    return "\n".join(lines) + "\n"


def evidence_package_status_text(package):
    lines = [
        f"Package: {package.id}",
        f"Label: {package.label}",
        f"Runnable: {'yes' if package.runnable else 'no'}",
        f"Status: {package.status}",
        "",
        "Candidate IDs:",
    ]
    lines.extend(f"- {candidate}" for candidate in package.candidate_ids)
    lines.extend(["", "Evidence Paths:"])
    lines.extend(f"- {path}" for path in package.evidence_paths)
    lines.extend(["", "Blockers:"])
    lines.extend(f"- {blocker}" for blocker in package.blockers)
    if package.read_only_actions:
        lines.extend(["", "Read-only actions:"])
        lines.extend(f"- {action}" for action in package.read_only_actions)
    return "\n".join(lines) + "\n"


def evidence_package_dry_run_text(package):
    if not package.runnable:
        return (
            f"Package: {package.id}\n"
            f"{package.id} is status-only.\n"
            "No remote state changed.\n"
        )
    lines = [
        "DRY RUN",
        f"Package: {package.id}",
        f"Runtime profile: {package.runtime_profile}",
        f"Runtime root: {package.runtime_root}",
        "",
        "Candidate IDs:",
    ]
    lines.extend(f"- {candidate}" for candidate in package.candidate_ids)
    lines.extend([
        "",
        "Read-only actions:",
    ])
    lines.extend(f"- {action}" for action in package.read_only_actions)
    lines.append("No remote state changed.")
    return "\n".join(lines) + "\n"


def package_c_remote_command(package):
    runtime = package.runtime_root
    socket = f"{runtime}/env/socket/node1/sock"
    return f"""set -e
runtime={runtime}
socket={socket}
echo "PACKAGE_C_BASELINE"
date -u
echo "RUNTIME_ROOT=$runtime"
echo "TMUX"
tmux ls 2>/dev/null | grep -E 'cardano-profile-|cardano-devnet' || true
echo "CONFIG_PEERSHARING"
grep -n '"PeerSharing"' "$runtime/env/configuration.yaml" || true
echo "TIP"
CARDANO_NODE_SOCKET_PATH="$socket" /home/nigel/.local/bin/cardano-cli query tip --testnet-magic 42 || true
echo "NODE_PROCESSES"
pgrep -af 'cardano-node run' || true
echo "LISTENERS"
ss -ltnp 2>/dev/null | grep -E 'cardano-node|cardano-testnet' || true
echo "TOPOLOGY"
for f in "$runtime"/env/node-data/node*/topology.json; do
  echo "## $f"
  sed -n '1,120p' "$f"
done
echo "CHAIN_SYNC_BLOCKFETCH_LOG_SAMPLE"
grep -R -E 'ChainSync|BlockFetch|rollback|Rollback|intersection|Intersect' "$runtime"/env/logs "$runtime"/logs 2>/dev/null | tail -n 80 || true
echo "LOG_FILES"
find "$runtime" -maxdepth 4 -type f \\( -name '*.log' -o -name 'stdout.log' \\) -print 2>/dev/null | sort
"""


def package_c_note(package, command_result):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    directory = MILESTONE_EVIDENCE_ROOT / "package-c"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "M1-PC-001-package-c-baseline.md"
    lines = [
        "# M1-PC-001 Package C Baseline",
        "",
        "## Purpose",
        "",
        "Capture read-only baseline evidence for Package C before ChainSync, rollback, and BlockFetch-specific review.",
        "",
        "This note does not promote, score, accept, or close any risk.",
        "",
        "## Package",
        "",
        f"- Package: `{package.id}`",
        f"- Label: {package.label}",
        f"- Timestamp UTC: {timestamp}",
        f"- Runtime Profile: `{package.runtime_profile}`",
        f"- Runtime Root: `{package.runtime_root}`",
        "",
        "## Candidate Rows",
        "",
    ]
    lines.extend(f"- `{candidate}`" for candidate in package.candidate_ids)
    lines.extend(
        [
            "",
            "## Read-Only Actions",
            "",
        ]
    )
    lines.extend(f"- {action}" for action in package.read_only_actions)
    lines.extend(
        [
            "",
            "## Remote Command",
            "",
            "```bash",
            command_result.rendered_command,
            "```",
            f"- Exit Code: `{command_result.returncode}`",
            "",
            "## Output",
            "",
            "```text",
            (command_result.stdout or "").strip(),
            "```",
            "",
            "## Errors",
            "",
            "```text",
            (command_result.stderr or "").strip(),
            "```",
            "",
            "## Disposition",
            "",
            "Package C remains candidate-only. This baseline supports later source and runtime review but does not establish a finding, accepted risk, severity, likelihood, or mitigation requirement.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def unsupported_package_result(package):
    return CommandResult(
        1,
        f"Package: {package.id}\n{package.id} is status-only.\nNo remote state changed.\n",
        "",
        "",
    )
