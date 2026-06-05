from dataclasses import dataclass

from profile_manager.remote import ssh_command


# Host-level prerequisites for the dockerized devnet flow.
# cardano-node / cardano-cli / cardano-testnet are no longer installed on the
# host — they live inside the built Docker image and are invoked via `docker exec`.
APT_PACKAGES = ("docker.io", "docker-compose-plugin", "git", "curl", "jq", "rsync", "python3")


@dataclass(frozen=True)
class Prereq:
    name: str
    command: str
    required: bool = True


CHECKS = (
    Prereq("architecture", "uname -m"),
    Prereq("os-release", "cat /etc/os-release"),
    Prereq("cpu-count", "nproc"),
    Prereq("memory", "free -h"),
    Prereq("disk", "df -h /"),
    Prereq("docker", "docker --version"),
    Prereq("docker-compose", "docker compose version"),
    Prereq("docker-daemon", "docker info --format '{{.ServerVersion}}'"),
    Prereq("git", "command -v git"),
    Prereq("curl", "command -v curl"),
    Prereq("jq", "command -v jq"),
    Prereq("rsync", "command -v rsync"),
    Prereq("python3", "command -v python3"),
    Prereq(
        "dwarf-cardano-node-image",
        "docker image ls 'dwarf/cardano-node' --format '{{.Repository}}:{{.Tag}}' | head -n1",
        required=False,
    ),
    Prereq(
        "pumba-image",
        "docker image ls 'gaiaadm/pumba' --format '{{.Repository}}:{{.Tag}}' | head -n1",
        required=True,
    ),
)


def run_checks(config, dry_run=False):
    results = []
    for check in CHECKS:
        result = ssh_command(config, check.command, timeout=30, dry_run=dry_run)
        results.append((check, result))
    return results


def format_check_results(results):
    lines = ["Prerequisite check results:"]
    missing = []
    for check, result in results:
        status = "PASS" if result.returncode == 0 else "MISSING"
        if status == "MISSING" and check.required:
            missing.append(check.name)
        output = (result.stdout or result.stderr).strip().splitlines()
        first_line = output[0] if output else ""
        lines.append(f"- {check.name}: {status} {first_line}".rstrip())
    if missing:
        lines.append("")
        lines.append("Missing prerequisites:")
        for item in missing:
            lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def install_command():
    return "sudo apt-get update && sudo apt-get install -y " + " ".join(APT_PACKAGES)
