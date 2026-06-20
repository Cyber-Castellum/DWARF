from __future__ import annotations

import base64
import json
import shlex
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any

from profile_manager.remote import CommandResult, ssh_command
from profile_manager.wallets import wallet_statuses


DEFAULT_MOOG_CONFIG: dict[str, Any] = {
    "enabled": True,
    "deploy_root": "${HOME}/moog-deploy",
    "moog_binary": "${HOME}/bin/moog",
    "secrets_root": "${HOME}/moog-secrets",
    "mpfs_host": "https://mpfs.plutimus.com",
    "token_id": "21c523c3b4565f1fc1ad7e54e82ca976f60997d8e7e9946826813fabf341069b",
    "requester_wallet_id": "moog-requester",
    "requester_wallet_file": "${HOME}/moog-secrets/requester/requester.json",
    "oracle_service": "moog-oracle.service",
    "github_user": "",
    "github_repo": "",
    "github_pat": "",
    "target_directory": "",
    "target_commit": "",
    "asset_dir": "",
    "duration_hours": "1",
    "antithesis_launch_url": "https://amaru-cardano.antithesis.com/api/v1/launch/amaru-cardano",
    "antithesis_user": "pragma",
    "antithesis_password": "",
    "antithesis_registry": "us-central1-docker.pkg.dev/molten-verve-216720/cardano-repository",
    "antithesis_api_key": "",
    "docker_config_path": "",
    "agent_email_user": "",
    "agent_email_password": "",
}

MOOG_ASSET_SCAFFOLD_FILES: dict[str, str] = {
    "docker-compose.yaml": """services:
  workload:
    image: alpine:3.20
    command: ["sh", "-c", "echo 'replace this with the Dwarf security workload'; sleep 5"]
    labels:
      antithesis.description: "Placeholder Dwarf security workload"
""",
    "README.md": """# Moog Test Asset

This directory is a target-agnostic starting point for a Moog/Antithesis test asset.

Before live submission, replace the placeholder workload with the real Dwarf security test services for the target repository. Do not commit wallet files, passphrases, GitHub PATs, Docker auth, Antithesis credentials, or other secrets into this directory.
""",
}

SECRET_FILE_PATTERNS = (
    ".env",
    "secret",
    "secrets",
    "wallet",
    "passphrase",
    "mnemonic",
    "private",
    "token",
    "pat",
    "docker-config",
    "config.json",
)


def normalize_moog_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    config = dict(DEFAULT_MOOG_CONFIG)
    if isinstance(raw, dict):
        for key, value in raw.items():
            if value is not None:
                config[key] = value
    config["enabled"] = bool(config.get("enabled"))
    for key in (
        "deploy_root",
        "moog_binary",
        "secrets_root",
        "mpfs_host",
        "token_id",
        "requester_wallet_id",
        "requester_wallet_file",
        "oracle_service",
        "github_user",
        "github_repo",
        "github_pat",
        "target_directory",
        "target_commit",
        "asset_dir",
        "duration_hours",
        "antithesis_launch_url",
        "antithesis_user",
        "antithesis_password",
        "antithesis_registry",
        "antithesis_api_key",
        "docker_config_path",
        "agent_email_user",
        "agent_email_password",
    ):
        config[key] = str(config.get(key) or DEFAULT_MOOG_CONFIG[key])
    return config


def set_moog_config(config, values: dict[str, Any]):
    return replace(config, moog=normalize_moog_config(values))


def build_moog_bootstrap_plan(moog_config: dict[str, Any]) -> dict[str, Any]:
    config = normalize_moog_config(moog_config)
    actions = [
        {
            "id": "create_deploy_root",
            "title": "Create Moog deploy directories",
            "state": "planned",
            "mode": "opt-in",
            "path": config["deploy_root"],
            "detail": "Creates deploy root plus ops and state subdirectories on the configured remote host.",
        },
        {
            "id": "create_secrets_root",
            "title": "Create Moog secret directory skeleton",
            "state": "planned",
            "mode": "opt-in",
            "path": config["secrets_root"],
            "detail": "Creates requester/oracle secret directories only; it does not create, read, or copy wallet secret files.",
        },
        {
            "id": "write_operator_plan",
            "title": "Write remote bootstrap plan file",
            "state": "planned",
            "mode": "opt-in",
            "path": f"{config['deploy_root']}/ops/dwarf-moog-bootstrap-plan.txt",
            "detail": "Records the remaining manual binary, wallet, oracle, agent, GitHub, and Antithesis setup steps.",
        },
        {
            "id": "install_binaries",
            "title": "Install Moog binaries",
            "state": "external",
            "mode": "manual",
            "path": config["moog_binary"],
            "detail": "Operator must install or symlink moog, moog-agent, and moog-oracle; Dwarf does not fetch release artifacts.",
        },
        {
            "id": "configure_wallets",
            "title": "Create wallet files and public metadata",
            "state": "external",
            "mode": "manual",
            "path": config["secrets_root"],
            "detail": "Operator must create encrypted requester/oracle wallets and public wallet-info JSON; Dwarf never writes wallet secrets.",
        },
        {
            "id": "configure_services",
            "title": "Configure oracle/agent services",
            "state": "external",
            "mode": "manual",
            "path": config["oracle_service"],
            "detail": "Operator must decide which Moog roles run here before enabling services or adding PAT/Antithesis credentials.",
        },
    ]
    healthcheck_plan = [
        {
            "order": 1,
            "command": "cardano-profile moog bootstrap --json",
            "purpose": "Review the opt-in bootstrap plan without changing remote state.",
        },
        {
            "order": 2,
            "command": "cardano-profile moog bootstrap --approve --json",
            "purpose": "Create the safe remote directory skeleton and operator plan file.",
        },
        {
            "order": 3,
            "command": "cardano-profile moog healthcheck --json",
            "purpose": "Verify binary path, deploy directories, public metadata, MPFS/token config, and oracle unit state.",
        },
        {
            "order": 4,
            "command": f"cardano-profile wallet healthcheck {config['requester_wallet_id']} --json",
            "purpose": "Verify Dwarf can observe the requester wallet address, balance, and recent transactions.",
        },
        {
            "order": 5,
            "command": "cardano-profile moog readiness --repo <org/repo> --github-user <user> --json",
            "purpose": "Check requester funding, GitHub profile vkey, CODEOWNERS, Moog facts, and whitelist state.",
        },
        {
            "order": 6,
            "command": "cardano-profile moog preflight --asset-dir <dir> --repo <org/repo> --github-user <user> --directory <path> --commit <sha> --json",
            "purpose": "Run the combined health, requester, asset, and create-test planning gate.",
        },
    ]
    return {
        "state": "planned",
        "applied": False,
        "actions": actions,
        "healthcheck_plan": healthcheck_plan,
        "deploy_root": config["deploy_root"],
        "secrets_root": config["secrets_root"],
        "moog_binary": config["moog_binary"],
        "mpfs_host": config["mpfs_host"],
        "token_id": config["token_id"],
        "requester_wallet_id": config["requester_wallet_id"],
        "requester_wallet_file": config["requester_wallet_file"],
        "oracle_service": config["oracle_service"],
        "guardrails": [
            "Does not run unless explicitly approved.",
            "Does not install release binaries from the network.",
            "Does not create, read, print, or copy wallet secret contents.",
            "Does not write GitHub PATs, Antithesis credentials, Docker auth, or wallet passphrases.",
            "Does not enable or start moog-oracle, moog-agent, or moog-antithesis-proxy.",
        ],
    }


def build_moog_bootstrap_command(moog_config: dict[str, Any], apply: bool = True) -> str:
    config = normalize_moog_config(moog_config)
    script = r'''
import json
import os
import stat
import subprocess
from pathlib import Path

apply = os.environ.get("DWARF_MOOG_APPLY") == "1"
deploy_root = Path(os.environ["DWARF_MOOG_DEPLOY_ROOT"])
secrets_root = Path(os.environ["DWARF_MOOG_SECRETS_ROOT"])
moog_binary = Path(os.environ["DWARF_MOOG_BINARY"])
mpfs_host = os.environ["DWARF_MOOG_MPFS_HOST"]
token_id = os.environ["DWARF_MOOG_TOKEN_ID"]
requester_wallet_id = os.environ["DWARF_MOOG_REQUESTER_WALLET_ID"]
oracle_service = os.environ["DWARF_MOOG_ORACLE_SERVICE"]
state_root = deploy_root / "state"
ops_root = deploy_root / "ops"

checks = []
healthcheck_plan = [
    {"order": 1, "command": "cardano-profile moog healthcheck --json", "purpose": "Verify Moog deployment state."},
    {"order": 2, "command": f"cardano-profile wallet healthcheck {requester_wallet_id} --json", "purpose": "Verify requester wallet telemetry."},
    {"order": 3, "command": "cardano-profile moog readiness --repo <org/repo> --github-user <user> --json", "purpose": "Verify requester registration prerequisites."},
    {"order": 4, "command": "cardano-profile moog preflight --asset-dir <dir> --repo <org/repo> --github-user <user> --directory <path> --commit <sha> --json", "purpose": "Run final non-live preflight before create-test."},
]

def check(check_id, state, detail):
    checks.append({"id": check_id, "state": state, "detail": str(detail)})

def ensure_dir(path, mode):
    if apply:
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(mode)
    if path.is_dir():
        current_mode = stat.S_IMODE(path.stat().st_mode)
        check(path.name or str(path), "ok", f"{path} mode {oct(current_mode)}")
    else:
        check(path.name or str(path), "error" if apply else "warn", f"{path} missing")

ensure_dir(deploy_root, 0o755)
ensure_dir(ops_root, 0o755)
ensure_dir(state_root, 0o755)
ensure_dir(secrets_root, 0o700)
ensure_dir(secrets_root / "requester", 0o700)
ensure_dir(secrets_root / "oracle", 0o700)

plan_path = ops_root / "dwarf-moog-bootstrap-plan.txt"
if apply and ops_root.is_dir():
    plan_path.write_text(
        "\n".join([
            "Dwarf Moog bootstrap plan",
            "",
            "Created by: cardano-profile moog bootstrap --approve",
            f"Deploy root: {deploy_root}",
            f"Secrets root: {secrets_root}",
            f"Moog binary: {moog_binary}",
            f"MPFS host: {mpfs_host}",
            f"Token id: {token_id}",
            f"Requester wallet id: {requester_wallet_id}",
            f"Oracle service: {oracle_service}",
            "",
            "Remaining manual setup:",
            "- Install or update moog, moog-agent, and moog-oracle release binaries.",
            "- Create encrypted requester/oracle wallet files without exposing secret contents.",
            "- Export public wallet metadata into deploy_root/state.",
            "- Add GitHub PAT and Antithesis credentials only through the chosen service secret mechanism.",
            "- Enable/start oracle or agent units only after role and secret readiness are confirmed.",
            "- Run cardano-profile moog healthcheck --json after every setup step.",
            "",
        ]),
        encoding="utf-8",
    )
check("operator_plan", "ok" if plan_path.is_file() else ("error" if apply else "warn"), plan_path)

if moog_binary.exists() and os.access(moog_binary, os.X_OK):
    try:
        completed = subprocess.run([str(moog_binary), "--version"], text=True, capture_output=True, check=False, timeout=5)
        detail = (completed.stdout or completed.stderr or "").strip()
        check("binary", "ok" if completed.returncode == 0 else "warn", detail or moog_binary)
    except Exception as exc:
        check("binary", "warn", exc)
else:
    check("binary", "warn", moog_binary)

for filename in ("requester-wallet-info.json", "oracle-wallet-info.json"):
    metadata_path = state_root / filename
    check(filename, "ok" if metadata_path.is_file() else "warn", metadata_path)

check("mpfs_host", "ok" if mpfs_host.startswith("https://") else "warn", mpfs_host)
check("token_id", "ok" if token_id else "error", token_id or "missing")
check("oracle_service_guard", "warn", f"{oracle_service} not enabled or started by bootstrap")

states = {row["state"] for row in checks}
state = "error" if "error" in states else ("warn" if "warn" in states else "ok")
print(json.dumps({
    "state": state,
    "applied": apply,
    "checks": checks,
    "healthcheck_plan": healthcheck_plan,
    "deploy_root": str(deploy_root),
    "secrets_root": str(secrets_root),
    "moog_binary": str(moog_binary),
    "mpfs_host": mpfs_host,
    "token_id": token_id,
    "requester_wallet_id": requester_wallet_id,
    "oracle_service": oracle_service,
}, sort_keys=True))
'''
    env = {
        "DWARF_MOOG_APPLY": "1" if apply else "0",
        "DWARF_MOOG_DEPLOY_ROOT": config["deploy_root"],
        "DWARF_MOOG_SECRETS_ROOT": config["secrets_root"],
        "DWARF_MOOG_BINARY": config["moog_binary"],
        "DWARF_MOOG_MPFS_HOST": config["mpfs_host"],
        "DWARF_MOOG_TOKEN_ID": config["token_id"],
        "DWARF_MOOG_REQUESTER_WALLET_ID": config["requester_wallet_id"],
        "DWARF_MOOG_ORACLE_SERVICE": config["oracle_service"],
    }
    exports = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
    return f"{exports} python3 - <<'PY'\n{script}\nPY"


def query_moog_bootstrap(config, timeout: int = 60) -> dict[str, Any]:
    moog_config = normalize_moog_config(getattr(config, "moog", None))
    result = ssh_command(config, build_moog_bootstrap_command(moog_config, apply=True), timeout=timeout)
    return parse_moog_bootstrap_result(result)


def parse_moog_bootstrap_result(result: CommandResult) -> dict[str, Any]:
    parsed: dict[str, Any] = {
        "state": "error",
        "applied": False,
        "returncode": result.returncode,
        "rendered_command": result.rendered_command,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "checks": [],
        "healthcheck_plan": [],
    }
    if result.returncode != 0:
        parsed["error"] = result.stderr.strip() or result.stdout.strip() or f"moog bootstrap command exited {result.returncode}"
        return parsed
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        parsed["error"] = f"invalid moog bootstrap JSON: {exc}"
        return parsed
    if not isinstance(payload, dict):
        parsed["error"] = "moog bootstrap JSON was not an object"
        return parsed
    checks = payload.get("checks")
    if not isinstance(checks, list):
        parsed["error"] = "moog bootstrap JSON did not include checks"
        return parsed
    parsed.update(payload)
    states = {str(check.get("state") or "error") for check in checks if isinstance(check, dict)}
    if "error" in states:
        parsed["state"] = "error"
    elif "warn" in states:
        parsed["state"] = "warn"
    else:
        parsed["state"] = "ok"
    return parsed


def moog_bootstrap_summary(result: dict[str, Any]) -> dict[str, Any]:
    checks = [check for check in (result.get("checks") or []) if isinstance(check, dict)]
    actions = [action for action in (result.get("actions") or []) if isinstance(action, dict)]
    healthcheck_plan = [step for step in (result.get("healthcheck_plan") or []) if isinstance(step, dict)]
    return {
        "state": result.get("state") or "unknown",
        "applied": bool(result.get("applied")),
        "check_count": len(checks),
        "ok_count": _count_checks(checks, "ok"),
        "warn_count": _count_checks(checks, "warn"),
        "error_count": _count_checks(checks, "error"),
        "action_count": len(actions),
        "planned_count": _count_actions(actions, "planned"),
        "external_count": _count_actions(actions, "external"),
        "healthcheck_steps": len(healthcheck_plan),
        "deploy_root": result.get("deploy_root"),
        "secrets_root": result.get("secrets_root"),
        "moog_binary": result.get("moog_binary"),
        "requester_wallet_id": result.get("requester_wallet_id"),
    }


def build_moog_health_command(moog_config: dict[str, Any]) -> str:
    config = normalize_moog_config(moog_config)
    script = r'''
import json
import os
import subprocess

deploy_root = os.environ["DWARF_MOOG_DEPLOY_ROOT"]
moog_binary = os.environ["DWARF_MOOG_BINARY"]
mpfs_host = os.environ["DWARF_MOOG_MPFS_HOST"]
token_id = os.environ["DWARF_MOOG_TOKEN_ID"]
oracle_service = os.environ["DWARF_MOOG_ORACLE_SERVICE"]
state_root = os.path.join(deploy_root, "state")

def check(check_id, state, detail):
    checks.append({"id": check_id, "state": state, "detail": str(detail)})

def run(argv):
    try:
        completed = subprocess.run(argv, text=True, capture_output=True, check=False, timeout=5)
    except Exception as exc:
        return None, str(exc), 127
    detail = (completed.stdout or completed.stderr or "").strip()
    return detail, detail, completed.returncode

checks = []
wallets = {}

version, detail, rc = run([moog_binary, "--version"])
check("binary", "ok" if rc == 0 else "error", detail or "moog --version failed")

for rel, check_id in (("", "deploy_root"), ("ops", "ops_dir"), ("state", "state_dir")):
    path = os.path.join(deploy_root, rel)
    check(check_id, "ok" if os.path.isdir(path) else "error", path)

for name, filename in (("requester", "requester-wallet-info.json"), ("oracle", "oracle-wallet-info.json")):
    path = os.path.join(state_root, filename)
    if not os.path.exists(path):
        check(f"{name}_wallet_metadata", "error", path)
        continue
    try:
        with open(path, "r", encoding="utf-8") as handle:
            wallets[name] = json.load(handle)
        check(f"{name}_wallet_metadata", "ok", path)
    except Exception as exc:
        check(f"{name}_wallet_metadata", "error", exc)

check("mpfs_host", "ok" if mpfs_host.startswith("https://") else "warn", mpfs_host)
check("token_id", "ok" if token_id else "error", token_id or "missing")

enabled, detail, rc = run(["systemctl", "--user", "is-enabled", oracle_service])
if rc == 0:
    check("oracle_service_enabled", "ok", detail)
else:
    check("oracle_service_enabled", "warn", detail or "disabled")

active, detail, rc = run(["systemctl", "--user", "is-active", oracle_service])
if rc == 0:
    check("oracle_service_active", "ok", detail)
else:
    check("oracle_service_active", "warn", detail or "inactive")

print(json.dumps({
    "checks": checks,
    "wallets": wallets,
    "deploy_root": deploy_root,
    "mpfs_host": mpfs_host,
    "token_id": token_id,
    "oracle_service": oracle_service,
}, sort_keys=True))
'''
    env = {
        "DWARF_MOOG_DEPLOY_ROOT": config["deploy_root"],
        "DWARF_MOOG_BINARY": config["moog_binary"],
        "DWARF_MOOG_MPFS_HOST": config["mpfs_host"],
        "DWARF_MOOG_TOKEN_ID": config["token_id"],
        "DWARF_MOOG_ORACLE_SERVICE": config["oracle_service"],
    }
    exports = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
    return f"{exports} python3 - <<'PY'\n{script}\nPY"


def query_moog_health(config, timeout: int = 20, dry_run: bool = False) -> dict[str, Any]:
    moog_config = normalize_moog_config(getattr(config, "moog", None))
    result = ssh_command(config, build_moog_health_command(moog_config), timeout=timeout, dry_run=dry_run)
    return parse_moog_health_result(result)


def build_moog_facts_command(moog_config: dict[str, Any]) -> str:
    config = normalize_moog_config(moog_config)
    script = r'''
import json
import os
import subprocess

moog_binary = os.environ["DWARF_MOOG_BINARY"]
mpfs_host = os.environ["DWARF_MOOG_MPFS_HOST"]
token_id = os.environ["DWARF_MOOG_TOKEN_ID"]

env = os.environ.copy()
env["MOOG_MPFS_HOST"] = mpfs_host
env["MOOG_TOKEN_ID"] = token_id

facts = {}
checks = []
for key, fact_name in (("users", "users"), ("roles", "roles"), ("white_list", "white-list")):
    argv = [moog_binary, "facts", fact_name]
    try:
        completed = subprocess.run(argv, env=env, text=True, capture_output=True, check=False, timeout=20)
    except Exception as exc:
        checks.append({"id": f"facts_{key}", "state": "warn", "detail": str(exc)})
        facts[key] = []
        continue
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        checks.append({"id": f"facts_{key}", "state": "warn", "detail": detail or f"exit {completed.returncode}"})
        facts[key] = []
        continue
    try:
        parsed = json.loads(completed.stdout or "[]")
    except Exception as exc:
        checks.append({"id": f"facts_{key}", "state": "warn", "detail": f"invalid JSON: {exc}"})
        facts[key] = []
        continue
    facts[key] = parsed if isinstance(parsed, list) else []
    checks.append({"id": f"facts_{key}", "state": "ok", "detail": str(len(facts[key]))})

print(json.dumps({"facts": facts, "checks": checks}, sort_keys=True))
'''
    env = {
        "DWARF_MOOG_BINARY": config["moog_binary"],
        "DWARF_MOOG_MPFS_HOST": config["mpfs_host"],
        "DWARF_MOOG_TOKEN_ID": config["token_id"],
    }
    exports = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
    return f"{exports} python3 - <<'PY'\n{script}\nPY"


def query_moog_facts(config, timeout: int = 60, dry_run: bool = False) -> dict[str, Any]:
    moog_config = normalize_moog_config(getattr(config, "moog", None))
    result = ssh_command(config, build_moog_facts_command(moog_config), timeout=timeout, dry_run=dry_run)
    return parse_moog_facts_result(result)


def parse_moog_facts_result(result: CommandResult) -> dict[str, Any]:
    if result.returncode != 0:
        return {
            "users": [],
            "roles": [],
            "white_list": [],
            "_checks": [{"id": "facts_query", "state": "warn", "detail": result.stderr.strip() or result.stdout.strip()}],
            "_rendered_command": result.rendered_command,
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {
            "users": [],
            "roles": [],
            "white_list": [],
            "_checks": [{"id": "facts_query", "state": "warn", "detail": f"invalid JSON: {exc}"}],
            "_rendered_command": result.rendered_command,
        }
    facts = payload.get("facts") if isinstance(payload, dict) else {}
    if not isinstance(facts, dict):
        facts = {}
    return {
        "users": facts.get("users") if isinstance(facts.get("users"), list) else [],
        "roles": facts.get("roles") if isinstance(facts.get("roles"), list) else [],
        "white_list": facts.get("white_list") if isinstance(facts.get("white_list"), list) else [],
        "_checks": payload.get("checks") if isinstance(payload.get("checks"), list) else [],
        "_rendered_command": result.rendered_command,
    }


def query_moog_readiness(
    config,
    repo: str | None = None,
    github_user: str | None = None,
    github_token: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    moog_config = normalize_moog_config(getattr(config, "moog", None))
    health = query_moog_health(config, timeout=timeout)
    facts = query_moog_facts(config, timeout=max(timeout, 60))
    github = query_moog_github_artifacts(repo, github_user, github_token=github_token, timeout=timeout)
    wallets = wallet_statuses(config, timeout=10)
    return build_moog_readiness(
        moog_config=moog_config,
        health=health,
        wallet_status_rows=wallets,
        github=github,
        facts=facts,
        repo=repo,
        github_user=github_user,
    )


def query_moog_registration_plan(
    config,
    repo: str | None = None,
    github_user: str | None = None,
    github_token: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    moog_config = normalize_moog_config(getattr(config, "moog", None))
    health = query_moog_health(config, timeout=timeout)
    facts = query_moog_facts(config, timeout=max(timeout, 60))
    github = query_moog_github_artifacts(repo, github_user, github_token=github_token, timeout=timeout)
    return build_moog_registration_plan(
        moog_config=moog_config,
        health=health,
        github=github,
        facts=facts,
        repo=repo,
        github_user=github_user,
    )


def build_moog_readiness(
    moog_config: dict[str, Any],
    health: dict[str, Any] | None,
    wallet_status_rows: list[dict[str, Any]] | None,
    github: dict[str, Any] | None,
    facts: dict[str, Any] | None,
    repo: str | None = None,
    github_user: str | None = None,
) -> dict[str, Any]:
    config = normalize_moog_config(moog_config)
    health = health if isinstance(health, dict) else {}
    github = github if isinstance(github, dict) else {}
    facts = facts if isinstance(facts, dict) else {}
    checks: list[dict[str, Any]] = []

    def check(check_id: str, state: str, detail: Any, action: str | None = None):
        row = {"id": check_id, "state": state, "detail": str(detail)}
        if action:
            row["action"] = action
        checks.append(row)

    check("moog_enabled", "ok" if config["enabled"] else "warn", config["enabled"], "Enable moog config before submission.")
    check("mpfs_host", "ok" if config["mpfs_host"].startswith("https://") else "warn", config["mpfs_host"])
    check("token_id", "ok" if config["token_id"] else "error", config["token_id"] or "missing")

    health_state = health.get("state") or "unknown"
    if health_state == "error":
        check("moog_health", "error", health.get("error") or "healthcheck failed")
    elif health_state == "warn":
        check("moog_health", "warn", "healthcheck has warnings")
    elif health_state == "ok":
        check("moog_health", "ok", "healthcheck ok")
    else:
        check("moog_health", "warn", "healthcheck not available")

    requester = _requester_wallet_from_health(health)
    requester_address = requester.get("address")
    requester_vkey = _wallet_public_key(requester)
    check(
        "requester_wallet_metadata",
        "ok" if requester_address else "error",
        requester_address or "requester wallet metadata missing",
        "Run the Moog wallet metadata setup and ensure requester-wallet-info.json exists.",
    )
    check(
        "requester_vkey",
        "ok" if requester_vkey else "error",
        requester_vkey or "requester public key missing",
        "Run moog wallet info for the requester wallet and publish the vkey.",
    )

    requester_status = _wallet_status_by_id(wallet_status_rows or [], config["requester_wallet_id"])
    balance = int(requester_status.get("balance_lovelace") or 0) if requester_status else 0
    wallet_state = requester_status.get("state") if requester_status else "missing"
    check(
        "requester_wallet_funded",
        "ok" if requester_status and wallet_state in {"ok", "empty"} and balance > 0 else "error",
        requester_status.get("balance_tada") if requester_status else "wallet not configured in Dwarf",
        "Add/fund the requester wallet with preprod tADA from the faucet.",
    )

    if github_user:
        profile_vkey = str(github.get("profile_vkey") or "").strip()
        profile_state = "ok" if profile_vkey and (not requester_vkey or profile_vkey == requester_vkey) else "error"
        detail = github.get("profile_repo") or github.get("profile_error") or "missing moog.vkey"
        if profile_vkey and requester_vkey and profile_vkey != requester_vkey:
            detail = "moog.vkey does not match requester public key"
        check(
            "github_profile_vkey",
            profile_state,
            detail,
            f"Create {github_user}/{github_user}:moog.vkey containing the requester vkey.",
        )
    else:
        check("github_profile_vkey", "warn", "github user not supplied", "Run with --github-user <user>.")

    if repo and github_user:
        codeowners = str(github.get("codeowners") or "")
        owner_ref = f"@{github_user}".lower()
        codeowners_ok = "antithesis:" in codeowners.lower() and owner_ref in codeowners.lower()
        check(
            "github_codeowners",
            "ok" if codeowners_ok else "error",
            github.get("codeowners_path") or github.get("codeowners_error") or "missing antithesis owner",
            "Add a CODEOWNERS line like 'antithesis: @<github-user>' to the target repo.",
        )
    elif repo:
        check("github_codeowners", "warn", "github user not supplied", "Run with --github-user <user>.")
    else:
        check("github_codeowners", "warn", "repository not supplied", "Run with --repo <org/project>.")

    for fact_check in facts.get("_checks") or []:
        if isinstance(fact_check, dict):
            check(str(fact_check.get("id") or "facts_query"), str(fact_check.get("state") or "warn"), fact_check.get("detail") or "")

    users = facts.get("users") if isinstance(facts.get("users"), list) else []
    roles = facts.get("roles") if isinstance(facts.get("roles"), list) else []
    white_list = facts.get("white_list") if isinstance(facts.get("white_list"), list) else []
    if github_user:
        check(
            "moog_user_registered",
            "ok" if _has_registered_user(users, github_user, requester_vkey) else "error",
            github_user,
            "Run 'moog requester register-user --platform github --username <user> --vkey <vkey>'.",
        )
    else:
        check("moog_user_registered", "warn", "github user not supplied")
    if repo and github_user:
        check(
            "moog_role_registered",
            "ok" if _has_registered_role(roles, repo, github_user) else "error",
            f"{github_user} on {repo}",
            "Run 'moog requester register-role --platform github --username <user> --repository <org/repo>'.",
        )
        check(
            "moog_repo_whitelisted",
            "ok" if _has_whitelisted_repo(white_list, repo) else "warn",
            repo,
            "Ask the Moog operator to whitelist the repository before creating tests.",
        )
    else:
        check("moog_role_registered", "warn", "repository and github user not supplied")
        check("moog_repo_whitelisted", "warn", "repository not supplied")

    state = _overall_state(checks)
    return {
        "state": state,
        "checks": checks,
        "repo": repo,
        "github_user": github_user,
        "requester_address": requester_address,
        "requester_public_key": requester_vkey,
        "requester_wallet_id": config["requester_wallet_id"],
        "requester_balance_tada": requester_status.get("balance_tada") if requester_status else None,
        "mpfs_host": config["mpfs_host"],
        "token_id": config["token_id"],
    }


def moog_readiness_summary(readiness: dict[str, Any]) -> dict[str, Any]:
    checks = [check for check in (readiness.get("checks") or []) if isinstance(check, dict)]
    return {
        "state": readiness.get("state") or "unknown",
        "check_count": len(checks),
        "ok_count": _count_checks(checks, "ok"),
        "warn_count": _count_checks(checks, "warn"),
        "error_count": _count_checks(checks, "error"),
        "repo": readiness.get("repo"),
        "github_user": readiness.get("github_user"),
        "requester_address": readiness.get("requester_address"),
        "requester_wallet_id": readiness.get("requester_wallet_id"),
        "requester_balance_tada": readiness.get("requester_balance_tada"),
        "mpfs_host": readiness.get("mpfs_host"),
        "token_id": readiness.get("token_id"),
    }


def build_moog_registration_plan(
    moog_config: dict[str, Any],
    health: dict[str, Any] | None,
    github: dict[str, Any] | None,
    facts: dict[str, Any] | None,
    repo: str | None,
    github_user: str | None,
) -> dict[str, Any]:
    config = normalize_moog_config(moog_config)
    health = health if isinstance(health, dict) else {}
    github = github if isinstance(github, dict) else {}
    facts = facts if isinstance(facts, dict) else {}
    requester = _requester_wallet_from_health(health)
    requester_address = requester.get("address")
    requester_vkey = _wallet_public_key(requester)
    profile_repo = f"{github_user}/{github_user}" if github_user else None
    required_codeowners = f"antithesis: @{github_user}" if github_user else None
    users = facts.get("users") if isinstance(facts.get("users"), list) else []
    roles = facts.get("roles") if isinstance(facts.get("roles"), list) else []

    profile_vkey = str(github.get("profile_vkey") or "").strip()
    codeowners = str(github.get("codeowners") or "")
    profile_ok = bool(github_user and requester_vkey and profile_vkey == requester_vkey)
    codeowners_ok = bool(
        repo
        and github_user
        and "antithesis:" in codeowners.lower()
        and f"@{github_user}".lower() in codeowners.lower()
    )
    user_registered = bool(github_user and _has_registered_user(users, github_user, requester_vkey))
    role_registered = bool(repo and github_user and _has_registered_role(roles, repo, github_user))

    actions = [
        {
            "id": "publish_moog_vkey",
            "title": "Publish requester vkey in GitHub profile repo",
            "state": "satisfied" if profile_ok else "blocked",
            "detail": profile_repo or "github user not supplied",
            "required_content": requester_vkey,
            "path": "moog.vkey",
            "manual": True,
        },
        {
            "id": "ensure_codeowners",
            "title": "Ensure CODEOWNERS grants Antithesis requester role",
            "state": "satisfied" if codeowners_ok else "blocked",
            "detail": github.get("codeowners_path") or github.get("codeowners_error") or "CODEOWNERS missing",
            "required_line": required_codeowners,
            "manual": True,
        },
        {
            "id": "register_user",
            "title": "Register requester user with Moog",
            "state": "satisfied" if user_registered else "needed",
            "detail": github_user or "github user not supplied",
            "command": build_moog_requester_command(config, "register-user", repo=repo, github_user=github_user, requester_vkey=requester_vkey),
            "blocked_by": [] if profile_ok else ["publish_moog_vkey"],
        },
        {
            "id": "register_role",
            "title": "Register repository role with Moog",
            "state": "satisfied" if role_registered else "needed",
            "detail": repo or "repository not supplied",
            "command": build_moog_requester_command(config, "register-role", repo=repo, github_user=github_user, requester_vkey=requester_vkey),
            "blocked_by": [] if codeowners_ok else ["ensure_codeowners"],
        },
    ]
    blocking = [
        action["id"]
        for action in actions
        if action["state"] == "blocked" or (action["state"] == "needed" and action.get("blocked_by"))
    ]
    return {
        "state": "blocked" if blocking else ("ready" if any(action["state"] == "needed" for action in actions) else "satisfied"),
        "repo": repo,
        "github_user": github_user,
        "requester_address": requester_address,
        "requester_public_key": requester_vkey,
        "requester_wallet_file": config["requester_wallet_file"],
        "github": {
            "profile_repo": profile_repo,
            "required_moog_vkey": requester_vkey,
            "current_moog_vkey": profile_vkey or None,
            "codeowners_path": github.get("codeowners_path"),
            "required_codeowners_line": required_codeowners,
        },
        "actions": actions,
        "blocking": blocking,
    }


def build_moog_requester_command(
    moog_config: dict[str, Any],
    action: str,
    repo: str | None,
    github_user: str | None,
    requester_vkey: str | None = None,
) -> str:
    config = normalize_moog_config(moog_config)
    base = [
        config["moog_binary"],
        "requester",
        action,
        "-w",
        config["requester_wallet_file"],
        "-p",
        "github",
    ]
    if action == "register-user":
        base.extend(["-u", github_user or "<github-user>", "-v", requester_vkey or "<requester-vkey>"])
    elif action == "register-role":
        base.extend(["-r", repo or "<org/repo>", "-u", github_user or "<github-user>"])
    else:
        raise ValueError(f"unsupported requester action: {action}")
    env = {
        "MOOG_MPFS_HOST": config["mpfs_host"],
        "MOOG_TOKEN_ID": config["token_id"],
    }
    exports = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
    return f"{exports} {shlex.join(base)}"


def build_moog_registration_submit_command(plan: dict[str, Any], moog_config: dict[str, Any]) -> str:
    config = normalize_moog_config(moog_config)
    actions = [action for action in (plan.get("actions") or []) if isinstance(action, dict)]
    runnable = [action for action in actions if action.get("state") == "needed" and not action.get("blocked_by")]
    lines = [
        "set -euo pipefail",
        f"export MOOG_MPFS_HOST={shlex.quote(config['mpfs_host'])}",
        f"export MOOG_TOKEN_ID={shlex.quote(config['token_id'])}",
    ]
    if not runnable:
        lines.append("true")
    for action in runnable:
        command = str(action.get("command") or "")
        prefix = f"MOOG_MPFS_HOST={shlex.quote(config['mpfs_host'])} MOOG_TOKEN_ID={shlex.quote(config['token_id'])} "
        if command.startswith(prefix):
            command = command[len(prefix):]
        lines.append(command)
    return "\n".join(lines)


def moog_registration_summary(plan: dict[str, Any]) -> dict[str, Any]:
    actions = [action for action in (plan.get("actions") or []) if isinstance(action, dict)]
    return {
        "state": plan.get("state") or "unknown",
        "repo": plan.get("repo"),
        "github_user": plan.get("github_user"),
        "requester_address": plan.get("requester_address"),
        "requester_public_key": plan.get("requester_public_key"),
        "action_count": len(actions),
        "needed_count": _count_actions(actions, "needed"),
        "blocked_count": _count_actions(actions, "blocked"),
        "satisfied_count": _count_actions(actions, "satisfied"),
    }


def compute_next_try(test_run_facts, commit, directory, repository, requester, platform="github"):
    """Next attempt number = (count of matching existing test-run facts) + 1.

    Mirrors the CF cardano-node workflow: match on commit, directory, platform,
    repository (org/repo), and requester. `test_run_facts` is the parsed JSON
    array from `moog facts test-runs --whose <requester>`.
    """
    matches = 0
    for fact in test_run_facts or []:
        key = fact.get("key", {}) if isinstance(fact, dict) else {}
        repo_obj = key.get("repository", {}) or {}
        fact_repo = f"{repo_obj.get('organization', '')}/{repo_obj.get('repo', '')}"
        if (
            key.get("type") == "test-run"
            and key.get("commitId") == commit
            and key.get("directory") == directory
            and key.get("platform") == platform
            and fact_repo == repository
            and key.get("requester") == requester
        ):
            matches += 1
    return matches + 1


def parse_test_run_phase(test_run_facts):
    """Return the .value.phase of the first fact, or None.

    Input is the parsed JSON from `moog facts test-runs --test-run-id <id>`
    (mirrors CF's wait-for-test.sh, which reads `.[0].value.phase`).
    """
    if not test_run_facts:
        return None
    first = test_run_facts[0] if isinstance(test_run_facts, list) else test_run_facts
    return (first.get("value", {}) or {}).get("phase")


def build_moog_create_test_command(
    moog_config: dict[str, Any],
    repo: str | None,
    github_user: str | None,
    directory: str | None,
    commit: str | None,
    try_number: int | str | None = 1,
    duration_hours: int | str | None = 1,
    no_faults: bool = False,
    no_instrumentation: bool = False,
) -> str:
    config = normalize_moog_config(moog_config)
    base = [
        config["moog_binary"],
        "requester",
        "create-test",
        "-w",
        config["requester_wallet_file"],
        "-p",
        "github",
        "-r",
        repo or "<org/repo>",
        "-d",
        directory or "<test-directory>",
        "-c",
        commit or "<commit>",
        "--try",
        str(try_number or 1),
        "-u",
        github_user or "<github-user>",
        "-t",
        str(duration_hours or 1),
    ]
    if no_faults:
        base.append("--no-faults")
    if no_instrumentation:
        base.append("--no-instrumentation")
    env = {
        "MOOG_MPFS_HOST": config["mpfs_host"],
        "MOOG_TOKEN_ID": config["token_id"],
    }
    exports = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
    return f"{exports} {shlex.join(base)}"


def build_moog_create_test_plan(
    moog_config: dict[str, Any],
    repo: str | None,
    github_user: str | None,
    directory: str | None,
    commit: str | None,
    try_number: int | str | None = 1,
    duration_hours: int | str | None = 1,
    asset_dir: str | None = None,
    no_faults: bool = False,
    no_instrumentation: bool = False,
) -> dict[str, Any]:
    config = normalize_moog_config(moog_config)
    checks: list[dict[str, Any]] = []
    asset: dict[str, Any] = {"path": asset_dir}

    def check(check_id: str, state: str, detail: Any, action: str | None = None):
        row = {"id": check_id, "state": state, "detail": str(detail)}
        if action:
            row["action"] = action
        checks.append(row)

    org, project = _split_repo(repo or "")
    check(
        "repo",
        "ok" if org and project else "error",
        repo or "missing",
        "Provide --repo <org/repo> once the target repository is known.",
    )
    check(
        "github_user",
        "ok" if github_user else "error",
        github_user or "missing",
        "Provide --github-user <user> for the requester identity.",
    )
    check(
        "directory",
        "ok" if directory else "error",
        directory or "missing",
        "Provide --directory <repo-relative-test-directory>.",
    )
    check(
        "commit",
        "ok" if commit else "error",
        commit or "missing",
        "Provide --commit <sha> for the target repo revision.",
    )

    try_value = _positive_int(try_number)
    check("try", "ok" if try_value else "error", try_number or "missing", "Use --try 1 or another positive attempt number.")
    duration_value = _positive_int(duration_hours)
    check(
        "duration_hours",
        "ok" if duration_value else "error",
        duration_hours or "missing",
        "Use --duration-hours 1 or another positive duration.",
    )

    check("mpfs_host", "ok" if config["mpfs_host"].startswith("https://") else "warn", config["mpfs_host"])
    check("token_id", "ok" if config["token_id"] else "error", config["token_id"] or "missing")
    check("requester_wallet_file", "ok" if config["requester_wallet_file"] else "error", config["requester_wallet_file"] or "missing")

    if asset_dir:
        path = Path(asset_dir).expanduser()
        asset["path"] = str(path)
        if not path.exists():
            check("asset_dir", "error", path, "Create or point --asset-dir at the local Moog test asset directory.")
        elif not path.is_dir():
            check("asset_dir", "error", path, "--asset-dir must point to a directory.")
        else:
            check("asset_dir", "ok", path)
            compose_path = _find_docker_compose(path)
            if compose_path:
                asset["docker_compose"] = str(compose_path)
                check("docker_compose", "ok", compose_path)
            else:
                check("docker_compose", "error", "missing docker-compose.yaml", "Add docker-compose.yaml or docker-compose.yml.")
            asset["file_count"] = _asset_file_count(path)
    else:
        check("asset_dir", "warn", "not supplied", "Use --asset-dir to validate local test assets before submission.")

    command = build_moog_create_test_command(
        config,
        repo=repo,
        github_user=github_user,
        directory=directory,
        commit=commit,
        try_number=try_value or try_number or 1,
        duration_hours=duration_value or duration_hours or 1,
        no_faults=no_faults,
        no_instrumentation=no_instrumentation,
    )
    blocked = any(check_row.get("state") == "error" for check_row in checks)
    return {
        "state": "blocked" if blocked else "ready",
        "checks": checks,
        "repo": repo,
        "github_user": github_user,
        "directory": directory,
        "commit": commit,
        "try": try_value or try_number,
        "duration_hours": duration_value or duration_hours,
        "faults_enabled": not no_faults,
        "instrumentation_enabled": not no_instrumentation,
        "asset": asset,
        "command": command,
        "mpfs_host": config["mpfs_host"],
        "token_id": config["token_id"],
        "requester_wallet_file": config["requester_wallet_file"],
    }


def moog_create_test_summary(plan: dict[str, Any]) -> dict[str, Any]:
    checks = [check for check in (plan.get("checks") or []) if isinstance(check, dict)]
    return {
        "state": plan.get("state") or "unknown",
        "check_count": len(checks),
        "ok_count": _count_checks(checks, "ok"),
        "warn_count": _count_checks(checks, "warn"),
        "error_count": _count_checks(checks, "error"),
        "repo": plan.get("repo"),
        "github_user": plan.get("github_user"),
        "directory": plan.get("directory"),
        "commit": plan.get("commit"),
        "try": plan.get("try"),
        "duration_hours": plan.get("duration_hours"),
        "faults_enabled": plan.get("faults_enabled"),
        "instrumentation_enabled": plan.get("instrumentation_enabled"),
    }


def scaffold_moog_asset(target_dir: str, force: bool = False) -> dict[str, Any]:
    path = Path(target_dir).expanduser()
    files = {path / name: content for name, content in MOOG_ASSET_SCAFFOLD_FILES.items()}
    existing = [file_path.name for file_path in files if file_path.exists()]
    if existing and not force:
        return {
            "state": "blocked",
            "path": str(path),
            "existing_files": existing,
            "created_files": [],
            "detail": "refusing to overwrite existing scaffold files without force",
        }

    path.mkdir(parents=True, exist_ok=True)
    created = []
    for file_path, content in files.items():
        file_path.write_text(content, encoding="utf-8")
        created.append(str(file_path))

    return {
        "state": "created",
        "path": str(path),
        "created_files": created,
        "existing_files": existing,
    }


def validate_moog_asset(asset_dir: str | None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(check_id: str, state: str, detail: Any, action: str | None = None):
        row = {"id": check_id, "state": state, "detail": str(detail)}
        if action:
            row["action"] = action
        checks.append(row)

    if not asset_dir:
        check("asset_dir", "error", "missing", "Provide --asset-dir <local-test-asset-dir>.")
        return {"state": "blocked", "path": None, "checks": checks, "asset": {}}

    path = Path(asset_dir).expanduser()
    asset: dict[str, Any] = {"path": str(path)}
    if not path.exists():
        check("asset_dir", "error", path, "Create the asset directory or run moog asset scaffold.")
        return {"state": "blocked", "path": str(path), "checks": checks, "asset": asset}
    if not path.is_dir():
        check("asset_dir", "error", path, "--asset-dir must point to a directory.")
        return {"state": "blocked", "path": str(path), "checks": checks, "asset": asset}

    check("asset_dir", "ok", path)
    compose_path = _find_docker_compose(path)
    if compose_path:
        asset["docker_compose"] = str(compose_path)
        check("docker_compose", "ok", compose_path)
        compose_text = compose_path.read_text(encoding="utf-8", errors="replace")
        check(
            "compose_services",
            "ok" if "services:" in compose_text else "error",
            "services section present" if "services:" in compose_text else "services section missing",
            "Add at least one service under docker-compose.yaml services.",
        )
    else:
        check("docker_compose", "error", "missing docker-compose.yaml", "Add docker-compose.yaml or docker-compose.yml.")

    secret_files = _secret_like_asset_files(path)
    if secret_files:
        check(
            "secret_files",
            "error",
            ", ".join(secret_files),
            "Remove secrets and secret-like files from the asset directory.",
        )
    else:
        check("secret_files", "ok", "none")

    asset["file_count"] = _asset_file_count(path)
    state = _asset_state(checks)
    return {"state": state, "path": str(path), "checks": checks, "asset": asset}


def moog_asset_summary(result: dict[str, Any]) -> dict[str, Any]:
    checks = [check for check in (result.get("checks") or []) if isinstance(check, dict)]
    asset = result.get("asset") if isinstance(result.get("asset"), dict) else {}
    return {
        "state": result.get("state") or "unknown",
        "check_count": len(checks),
        "ok_count": _count_checks(checks, "ok"),
        "warn_count": _count_checks(checks, "warn"),
        "error_count": _count_checks(checks, "error"),
        "path": result.get("path"),
        "docker_compose": asset.get("docker_compose"),
        "file_count": asset.get("file_count"),
    }


def build_moog_preflight_report(
    health: dict[str, Any] | None,
    readiness: dict[str, Any] | None,
    asset_validation: dict[str, Any] | None,
    create_test: dict[str, Any] | None,
) -> dict[str, Any]:
    health = health if isinstance(health, dict) else {}
    readiness = readiness if isinstance(readiness, dict) else {}
    asset_validation = asset_validation if isinstance(asset_validation, dict) else {}
    create_test = create_test if isinstance(create_test, dict) else {}
    health_summary = moog_health_summary(health)
    readiness_summary = moog_readiness_summary(readiness)
    asset_summary = moog_asset_summary(asset_validation)
    create_test_summary = moog_create_test_summary(create_test)
    stages = [
        {"id": "moog_health", "state": _preflight_stage_state(health_summary.get("state")), "summary": health_summary},
        {"id": "requester_readiness", "state": _preflight_stage_state(readiness_summary.get("state")), "summary": readiness_summary},
        {"id": "asset", "state": _preflight_stage_state(asset_summary.get("state")), "summary": asset_summary},
        {"id": "create_test", "state": _preflight_stage_state(create_test_summary.get("state")), "summary": create_test_summary},
    ]
    state = _preflight_state(stages)
    return {
        "state": state,
        "stages": stages,
        "health": health,
        "readiness": readiness,
        "asset": asset_validation,
        "create_test": create_test,
    }


def moog_preflight_summary(report: dict[str, Any]) -> dict[str, Any]:
    stages = [stage for stage in (report.get("stages") or []) if isinstance(stage, dict)]
    return {
        "state": report.get("state") or "unknown",
        "stage_count": len(stages),
        "ready_count": _count_stage_states(stages, "ready"),
        "warn_count": _count_stage_states(stages, "warn"),
        "blocked_count": _count_stage_states(stages, "blocked"),
        "stages": {stage.get("id"): stage.get("state") for stage in stages},
    }


def query_moog_github_artifacts(
    repo: str | None,
    github_user: str | None,
    github_token: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    if github_user:
        profile_repo = f"{github_user}/{github_user}"
        try:
            artifacts["profile_vkey"] = _github_file_text(profile_repo, "moog.vkey", github_token, timeout).strip()
            artifacts["profile_repo"] = profile_repo
        except Exception as exc:
            artifacts["profile_error"] = _github_error(exc)
    if repo:
        for path in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"):
            try:
                artifacts["codeowners"] = _github_file_text(repo, path, github_token, timeout)
                artifacts["codeowners_path"] = path
                break
            except Exception as exc:
                artifacts["codeowners_error"] = _github_error(exc)
    return artifacts


def parse_moog_health_result(result: CommandResult) -> dict[str, Any]:
    parsed: dict[str, Any] = {
        "state": "error",
        "returncode": result.returncode,
        "rendered_command": result.rendered_command,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "checks": [],
        "wallets": {},
    }
    if result.returncode != 0:
        parsed["error"] = result.stderr.strip() or result.stdout.strip() or f"moog health command exited {result.returncode}"
        return parsed
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        parsed["error"] = f"invalid moog health JSON: {exc}"
        return parsed
    checks = payload.get("checks") if isinstance(payload, dict) else None
    if not isinstance(checks, list):
        parsed["error"] = "moog health JSON did not include checks"
        return parsed
    parsed.update(payload)
    states = {str(check.get("state") or "error") for check in checks if isinstance(check, dict)}
    if "error" in states:
        parsed["state"] = "error"
    elif "warn" in states:
        parsed["state"] = "warn"
    else:
        parsed["state"] = "ok"
    return parsed


def moog_health_summary(health: dict[str, Any]) -> dict[str, Any]:
    checks = [check for check in (health.get("checks") or []) if isinstance(check, dict)]
    wallets = health.get("wallets") if isinstance(health.get("wallets"), dict) else {}
    requester = wallets.get("requester") if isinstance(wallets.get("requester"), dict) else {}
    oracle = wallets.get("oracle") if isinstance(wallets.get("oracle"), dict) else {}
    return {
        "state": health.get("state") or "unknown",
        "check_count": len(checks),
        "ok_count": _count_checks(checks, "ok"),
        "warn_count": _count_checks(checks, "warn"),
        "error_count": _count_checks(checks, "error"),
        "deploy_root": health.get("deploy_root"),
        "mpfs_host": health.get("mpfs_host"),
        "token_id": health.get("token_id"),
        "oracle_service": health.get("oracle_service"),
        "requester_address": requester.get("address"),
        "oracle_address": oracle.get("address"),
    }


def _count_checks(checks: list[dict[str, Any]], state: str) -> int:
    return sum(1 for check in checks if check.get("state") == state)


def _count_actions(actions: list[dict[str, Any]], state: str) -> int:
    return sum(1 for action in actions if action.get("state") == state)


def _count_stage_states(stages: list[dict[str, Any]], state: str) -> int:
    return sum(1 for stage in stages if stage.get("state") == state)


def _positive_int(value: int | str | None) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 1:
        return None
    return parsed


def _find_docker_compose(path: Path) -> Path | None:
    # Check the asset root and the Antithesis-style config/ subdirectory.
    candidates = [
        path / "docker-compose.yaml",
        path / "docker-compose.yml",
        path / "config" / "docker-compose.yaml",
        path / "config" / "docker-compose.yml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _asset_file_count(path: Path) -> int:
    try:
        return sum(1 for item in path.rglob("*") if item.is_file())
    except OSError:
        return 0


def _asset_state(checks: list[dict[str, Any]]) -> str:
    states = {str(check.get("state") or "error") for check in checks}
    if "error" in states:
        return "blocked"
    if "warn" in states:
        return "warn"
    return "ready"


def _preflight_stage_state(state: Any) -> str:
    value = str(state or "unknown").lower()
    if value in {"ok", "ready", "satisfied"}:
        return "ready"
    if value == "warn":
        return "warn"
    return "blocked"


def _preflight_state(stages: list[dict[str, Any]]) -> str:
    states = {str(stage.get("state") or "blocked") for stage in stages}
    if "blocked" in states:
        return "blocked"
    if "warn" in states:
        return "warn"
    return "ready"


def _secret_like_asset_files(path: Path) -> list[str]:
    matches: list[str] = []
    try:
        files = [item for item in path.rglob("*") if item.is_file()]
    except OSError:
        return matches
    for file_path in files:
        rel = file_path.relative_to(path).as_posix()
        lowered = rel.lower()
        if any(pattern in lowered for pattern in SECRET_FILE_PATTERNS):
            matches.append(rel)
    return sorted(matches)


def _requester_wallet_from_health(health: dict[str, Any]) -> dict[str, Any]:
    wallets = health.get("wallets") if isinstance(health.get("wallets"), dict) else {}
    requester = wallets.get("requester") if isinstance(wallets.get("requester"), dict) else {}
    return requester


def _wallet_public_key(wallet: dict[str, Any]) -> str:
    for key in ("publicKey", "public_key", "vkey", "verificationKey", "verification_key"):
        value = wallet.get(key)
        if value:
            return str(value).strip()
    return ""


def _wallet_status_by_id(rows: list[dict[str, Any]], wallet_id: str) -> dict[str, Any] | None:
    for row in rows:
        if isinstance(row, dict) and row.get("id") == wallet_id:
            return row
    return None


def _has_registered_user(rows: list[dict[str, Any]], github_user: str, requester_vkey: str | None) -> bool:
    for row in rows:
        key = row.get("key") if isinstance(row, dict) else None
        if not isinstance(key, dict):
            continue
        if str(key.get("platform") or "").lower() != "github":
            continue
        if str(key.get("user") or "").lower() != github_user.lower():
            continue
        fact_vkey = str(key.get("vkey") or "").strip()
        if requester_vkey and fact_vkey and fact_vkey != requester_vkey:
            continue
        return True
    return False


def _has_registered_role(rows: list[dict[str, Any]], repo: str, github_user: str) -> bool:
    org, project = _split_repo(repo)
    if not org or not project:
        return False
    for row in rows:
        key = row.get("key") if isinstance(row, dict) else None
        if not isinstance(key, dict):
            continue
        repository = key.get("repository") if isinstance(key.get("repository"), dict) else {}
        if str(key.get("platform") or "").lower() != "github":
            continue
        if str(key.get("user") or "").lower() != github_user.lower():
            continue
        if str(repository.get("organization") or "").lower() == org.lower() and str(repository.get("project") or "").lower() == project.lower():
            return True
    return False


def _has_whitelisted_repo(rows: list[dict[str, Any]], repo: str) -> bool:
    org, project = _split_repo(repo)
    if not org or not project:
        return False
    for row in rows:
        key = row.get("key") if isinstance(row, dict) else None
        if not isinstance(key, dict):
            continue
        repository = key.get("repository") if isinstance(key.get("repository"), dict) else {}
        if str(repository.get("organization") or "").lower() == org.lower() and str(repository.get("project") or "").lower() == project.lower():
            return True
    return False


def _split_repo(repo: str) -> tuple[str, str]:
    parts = [part for part in str(repo or "").strip().split("/") if part]
    if len(parts) != 2:
        return "", ""
    return parts[0], parts[1]


def _overall_state(checks: list[dict[str, Any]]) -> str:
    states = {str(check.get("state") or "error") for check in checks}
    if "error" in states:
        return "error"
    if "warn" in states:
        return "warn"
    return "ok"


def _github_file_text(repo: str, path: str, token: str | None, timeout: int) -> str:
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "dwarf-moog-readiness",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(content, str):
        raise ValueError("GitHub contents response did not include file content")
    return base64.b64decode(content.encode("ascii")).decode("utf-8")


def _github_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"GitHub HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"GitHub URL error: {exc.reason}"
    return str(exc)
