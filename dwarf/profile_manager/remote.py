import shlex
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    rendered_command: str


def render_ssh_command(config, remote_command):
    target = f"{config.ssh_user}@{config.host}"
    return [
        "ssh",
        "-n",
        "-o",
        "BatchMode=yes",
        "-i",
        config.ssh_key_path,
        target,
        remote_command,
    ]


def shell_join(argv):
    return " ".join(shlex.quote(part) for part in argv)


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
    argv = [
        "rsync",
        "-a",
        "-e",
        f"ssh -i {shlex.quote(config.ssh_key_path)}",
        str(local_path),
        f"{config.ssh_user}@{config.host}:{remote_path}",
    ]
    rendered = shell_join(argv)
    if dry_run:
        return CommandResult(0, rendered + "\n", "", rendered)
    completed = subprocess.run(argv, text=True, capture_output=True, check=False)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr, rendered)


def rsync_from(config, remote_path, local_path, dry_run=False):
    argv = [
        "rsync",
        "-a",
        "-e",
        f"ssh -i {shlex.quote(config.ssh_key_path)}",
        f"{config.ssh_user}@{config.host}:{remote_path}",
        str(local_path),
    ]
    rendered = shell_join(argv)
    if dry_run:
        return CommandResult(0, rendered + "\n", "", rendered)
    completed = subprocess.run(argv, text=True, capture_output=True, check=False)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr, rendered)
