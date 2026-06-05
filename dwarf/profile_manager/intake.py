from profile_manager.config import DeploymentConfig, save_config


DEFAULTS = {
    "deployment_name": "cardano-box",
    "host": "192.168.30.16",
    "ssh_user": "nigel",
    "ssh_key_path": "/Users/nigel/.ssh/cardano-box",
    "remote_base_path": "/home/nigel/cardano-profiles",
}


def _prompt(defaults_key, label):
    default = DEFAULTS[defaults_key]
    value = input(f"{label} [{default}]: ").strip()
    return value or default


def _yes_no(label, default=False):
    suffix = "[Y/n]" if default else "[y/N]"
    value = input(f"{label} {suffix}: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes"}


def run_intake():
    print("New deployment intake")
    config = DeploymentConfig(
        deployment_name=_prompt("deployment_name", "Deployment name"),
        host=_prompt("host", "Host/IP"),
        ssh_user=_prompt("ssh_user", "SSH username"),
        ssh_key_path=_prompt("ssh_key_path", "SSH key path"),
        remote_base_path=_prompt("remote_base_path", "Remote base path"),
        allow_prereq_install=_yes_no("Allow prerequisite installation?"),
        allow_sudo=_yes_no("Allow sudo commands?"),
    )
    path = save_config(config)
    print(f"Saved deployment config: {path}")
    return 0


def ensure_config_or_intake(command_name):
    if command_name == "intake":
        return None
    print("No deployment config found.")
    print()
    print("1. New deployment")
    print("2. Import existing config")
    print("3. Exit")
    choice = input("Select option [1]: ").strip() or "1"
    if choice == "1":
        run_intake()
        return None
    raise SystemExit("Deployment config is required before running this command.")
