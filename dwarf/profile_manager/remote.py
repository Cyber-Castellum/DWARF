import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    rendered_command: str


def render_ssh_command(config, remote_command):
    target = f"{config.ssh_user}@{config.host}"
    ssh_key_path = resolve_ssh_key_path(config)
    return [
        "ssh",
        "-n",
        "-o",
        "BatchMode=yes",
        "-i",
        ssh_key_path,
        target,
        remote_command,
    ]


def resolve_ssh_key_path(config) -> str:
    configured = Path(config.ssh_key_path).expanduser()
    if configured.exists():
        return str(configured)

    parts = configured.parts
    if len(parts) >= 5 and parts[:4] == ("/", "home", "dwarf", ".ssh"):
        candidates = [
            Path.home() / ".ssh" / configured.name,
            Path("/home") / str(config.ssh_user) / ".ssh" / configured.name,
            Path("/Users") / str(config.ssh_user) / ".ssh" / configured.name,
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

    return str(configured)


def shell_join(argv):
    return " ".join(shlex.quote(part) for part in argv)


def run_moog_create_test(config, moog_config, command, timeout=900):
    """Run a prebuilt `moog requester create-test` command on the remote host,
    injecting the wallet passphrase + GitHub PAT from their on-host files at
    runtime (never logged). `config` is the deployment config (ssh target);
    `command` is from moog.build_moog_create_test_command."""
    from profile_manager.moog import normalize_moog_config

    cfg = normalize_moog_config(moog_config)
    secrets_root = cfg["secrets_root"]
    host_config = "${DWARF_ROOT}/var/state/config.yaml"
    script = (
        "set -uo pipefail\n"
        f'export MOOG_WALLET_PASSPHRASE="$(cat {secrets_root}/requester/wallet.passphrase)"\n'
        "export MOOG_GITHUB_PAT=\"$(python3 -c 'import json;"
        f"print(json.load(open(\"{host_config}\"))[\"moog\"][\"github_pat\"])')\"\n"
        f"{command}\n"
    )
    return ssh_command(config, script, timeout=timeout)


def fetch_test_run_facts(config, moog_config, test_run_id, timeout=60):
    """Return parsed JSON from `moog facts test-runs --test-run-id <id>` on the host."""
    import json

    from profile_manager.moog import normalize_moog_config

    cfg = normalize_moog_config(moog_config)
    command = (
        f"MOOG_MPFS_HOST={shlex.quote(cfg['mpfs_host'])} "
        f"MOOG_TOKEN_ID={shlex.quote(cfg['token_id'])} "
        f"{shlex.quote(cfg['moog_binary'])} facts test-runs --test-run-id {shlex.quote(test_run_id)}"
    )
    result = ssh_command(config, command, timeout=timeout)
    try:
        return json.loads(result.stdout) if result.stdout.strip() else []
    except (ValueError, AttributeError):
        return []


def ssh_command(config, remote_command, timeout=None, dry_run=False):
    argv = render_ssh_command(config, remote_command)
    rendered = shell_join(argv)
    if dry_run:
        return CommandResult(0, rendered + "\n", "", rendered)
    completed = subprocess.run(
        argv,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(
        completed.returncode,
        completed.stdout,
        completed.stderr,
        rendered,
    )


def rsync_to(config, local_path, remote_path, dry_run=False):
    ssh_key_path = resolve_ssh_key_path(config)
    argv = [
        "rsync",
        "-a",
        "-e",
        f"ssh -i {shlex.quote(ssh_key_path)}",
        str(local_path),
        f"{config.ssh_user}@{config.host}:{remote_path}",
    ]
    rendered = shell_join(argv)
    if dry_run:
        return CommandResult(0, rendered + "\n", "", rendered)
    completed = subprocess.run(argv, text=True, capture_output=True, check=False)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr, rendered)


def rsync_from(config, remote_path, local_path, dry_run=False):
    ssh_key_path = resolve_ssh_key_path(config)
    argv = [
        "rsync",
        "-a",
        "-e",
        f"ssh -i {shlex.quote(ssh_key_path)}",
        f"{config.ssh_user}@{config.host}:{remote_path}",
        str(local_path),
    ]
    rendered = shell_join(argv)
    if dry_run:
        return CommandResult(0, rendered + "\n", "", rendered)
    completed = subprocess.run(argv, text=True, capture_output=True, check=False)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr, rendered)
