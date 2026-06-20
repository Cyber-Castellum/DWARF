"""Read-only config view (slice 4 of dispatch 7).

Walks ``CONFIG_FIELDS`` (the canonical schema) and projects each
known key as a row with name / current value / default / source /
description. Source is one of:

- ``env``        — overridden by an environment variable
- ``config-file``— set in state/config.yaml (differs from default)
- ``default``    — fallback to the schema default

The dashboard never mutates config; editing happens via
``cardano-profile config set <key> <value>`` from a terminal.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from profile_manager.moog import DEFAULT_MOOG_CONFIG, normalize_moog_config


# Best-effort env-var overrides matching ada3's CLI naming convention.
_ENV_PREFIX = "DWARF_"

MOOG_SETUP_FIELDS: list[dict[str, Any]] = [
    {"key": "enabled", "label": "Enable Moog", "kind": "checkbox", "group": "Moog runtime", "env": ["DWARF_MOOG_ENABLED", "MOOG_ENABLED"], "help": "Controls whether Dwarf treats Moog as an active integration."},
    {"key": "deploy_root", "label": "Deploy root", "group": "Moog runtime", "env": ["DWARF_MOOG_DEPLOY_ROOT", "MOOG_DEPLOY_ROOT"], "help": "Remote Moog deploy directory on build-host."},
    {"key": "moog_binary", "label": "Moog binary", "group": "Moog runtime", "env": ["DWARF_MOOG_BINARY", "MOOG_BINARY"], "help": "Remote path to the release moog CLI binary."},
    {"key": "secrets_root", "label": "Secrets root", "group": "Moog runtime", "env": ["DWARF_MOOG_SECRETS_ROOT", "MOOG_SECRETS_ROOT"], "help": "Remote root for Moog wallet and service secrets."},
    {"key": "mpfs_host", "label": "MPFS host", "group": "Moog runtime", "env": ["DWARF_MOOG_MPFS_HOST", "MOOG_MPFS_HOST"], "help": "MPFS endpoint used by Moog."},
    {"key": "token_id", "label": "Moog token id", "group": "Moog runtime", "env": ["DWARF_MOOG_TOKEN_ID", "MOOG_TOKEN_ID"], "help": "Active Preprod Moog token asset id."},
    {"key": "requester_wallet_id", "label": "Requester wallet id", "group": "Moog runtime", "env": ["DWARF_MOOG_REQUESTER_WALLET_ID", "MOOG_REQUESTER_WALLET_ID"], "help": "Dwarf wallet id used for requester telemetry."},
    {"key": "requester_wallet_file", "label": "Requester wallet file", "group": "Moog runtime", "env": ["DWARF_MOOG_REQUESTER_WALLET_FILE", "MOOG_REQUESTER_WALLET_FILE"], "help": "Remote encrypted requester wallet file path."},
    {"key": "oracle_service", "label": "Oracle service", "group": "Moog runtime", "env": ["DWARF_MOOG_ORACLE_SERVICE", "MOOG_ORACLE_SERVICE"], "help": "User systemd unit name for the Moog oracle."},
    {"key": "github_user", "label": "GitHub username", "group": "GitHub target", "env": ["MOOG_GITHUB_USER", "GITHUB_USER"], "help": "GitHub requester identity to register with Moog."},
    {"key": "github_repo", "label": "GitHub repo", "group": "GitHub target", "env": ["MOOG_GITHUB_REPO", "GITHUB_REPOSITORY"], "help": "Target repository in org/repo form."},
    {"key": "github_pat", "label": "GitHub PAT", "group": "GitHub target", "kind": "password", "secret": True, "env": ["MOOG_GITHUB_PAT", "GITHUB_TOKEN"], "help": "Personal access token visible to the target repositories."},
    {"key": "target_directory", "label": "Test asset directory", "group": "GitHub target", "env": ["MOOG_TARGET_DIRECTORY", "MOOG_TEST_DIRECTORY"], "help": "Repository-relative directory containing the Moog/Antithesis asset."},
    {"key": "target_commit", "label": "Target commit/ref", "group": "GitHub target", "env": ["MOOG_TARGET_COMMIT", "GITHUB_SHA"], "help": "Commit SHA or ref submitted to Moog."},
    {"key": "asset_dir", "label": "Local asset dir", "group": "GitHub target", "env": ["MOOG_ASSET_DIR"], "help": "Local directory Dwarf validates before submission."},
    {"key": "duration_hours", "label": "Duration hours", "group": "GitHub target", "env": ["MOOG_DURATION_HOURS"], "help": "Requested Antithesis run duration."},
    {"key": "antithesis_launch_url", "label": "Antithesis launch URL", "group": "Antithesis", "env": ["MOOG_ANTITHESIS_LAUNCH_URL"], "help": "Tenant launch API URL."},
    {"key": "antithesis_user", "label": "Antithesis user", "group": "Antithesis", "env": ["MOOG_ANTITHESIS_USER"], "help": "Tenant launch username."},
    {"key": "antithesis_password", "label": "Antithesis password", "group": "Antithesis", "kind": "password", "secret": True, "env": ["MOOG_ANTITHESIS_PASSWORD", "ANTITHESIS_PASSWORD"], "help": "Tenant launch password."},
    {"key": "antithesis_registry", "label": "Container registry", "group": "Antithesis", "env": ["MOOG_REGISTRY"], "help": "Registry used by the Moog agent for Antithesis assets."},
    {"key": "antithesis_api_key", "label": "Antithesis API key", "group": "Antithesis", "kind": "password", "secret": True, "env": ["MOOG_ANTITHESIS_API_KEY", "ANTITHESIS_API_KEY"], "help": "Read/proxy API key if that path is enabled later."},
    {"key": "docker_config_path", "label": "Docker config path", "group": "Antithesis", "env": ["MOOG_DOCKER_CONFIG_PATH"], "help": "Remote path to Docker registry auth config."},
    {"key": "agent_email_user", "label": "Agent email user", "group": "Agent service", "env": ["MOOG_AGENT_EMAIL_USER"], "help": "Email account used by moog-agent result collection."},
    {"key": "agent_email_password", "label": "Agent email password", "group": "Agent service", "kind": "password", "secret": True, "env": ["MOOG_AGENT_EMAIL_PASSWORD"], "help": "Email password used by moog-agent result collection."},
]


def _read_config_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    # The state/config.yaml is JSON-formatted in practice. Try JSON first;
    # fall back to a flat key:value parse.
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    out: dict[str, Any] = {}
    for line in text.splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def operate_config_payload() -> dict[str, Any]:
    try:
        from profile_manager.config import CONFIG_FIELDS, config_path
        cfg_path = config_path()
    except ImportError:
        # Older deployments don't ship CONFIG_FIELDS — fall back to a
        # state-dir lookup and derive the schema from the file contents.
        CONFIG_FIELDS = {}
        cfg_path = (Path(os.environ.get("ADA2_DWARF_STATE_DIR")
                          or Path(__file__).resolve().parents[2] / "state")
                    / "config.yaml")

    file_data = _read_config_file(cfg_path)
    rows: list[dict[str, Any]] = []
    for key, meta in CONFIG_FIELDS.items():
        env_key = _ENV_PREFIX + key.upper()
        env_value = os.environ.get(env_key)
        default = meta.get("default")
        if env_value is not None:
            value = env_value
            source = "env"
        elif key in file_data:
            value = file_data[key]
            source = "config-file"
        else:
            value = default
            source = "default"
        rows.append({
            "key": key,
            "value": _stringify(value),
            "default": _stringify(default),
            "source": source,
            "type": meta.get("type") or "string",
            "description": meta.get("description") or "",
            "env_key": env_key,
        })
    # Surface unknown keys present in config.yaml so operators see them
    # even when CONFIG_FIELDS doesn't enumerate them yet.
    for key, value in file_data.items():
        if key in CONFIG_FIELDS:
            continue
        rows.append({
            "key": key,
            "value": _stringify(value),
            "default": "",
            "source": "config-file (unknown to schema)",
            "type": "unknown",
            "description": "",
            "env_key": _ENV_PREFIX + key.upper(),
        })
    return {
        "config_path": str(cfg_path),
        "config_present": cfg_path.is_file(),
        "rows": rows,
        "moog_setup": moog_setup_payload(_config_from_file_data(file_data), environ=os.environ),
    }


def moog_setup_payload(config, environ: dict[str, str] | None = None) -> dict[str, Any]:
    environ = environ if environ is not None else os.environ
    moog_config = normalize_moog_config(getattr(config, "moog", None))
    fields = []
    for meta in MOOG_SETUP_FIELDS:
        key = meta["key"]
        env_key = _first_env_key(meta.get("env") or [], environ)
        raw_value = environ.get(env_key) if env_key else moog_config.get(key, DEFAULT_MOOG_CONFIG.get(key, ""))
        source = f"env:{env_key}" if env_key else ("config" if key in getattr(config, "moog", {}) else "default")
        is_secret = bool(meta.get("secret"))
        configured = bool(raw_value)
        if meta.get("kind") == "checkbox":
            value = _truthy(raw_value)
        elif is_secret:
            value = ""
        else:
            value = str(raw_value or "")
        fields.append({
            **meta,
            "kind": meta.get("kind") or "text",
            "value": value,
            "source": source,
            "configured": configured,
            "secret": is_secret,
            "placeholder": "configured; leave blank to keep" if is_secret and configured else "",
        })
    groups = []
    for group in dict.fromkeys(field["group"] for field in fields):
        groups.append({"name": group, "fields": [field for field in fields if field["group"] == group]})
    commands = {
        "readiness": _moog_command_from_values(moog_config, "readiness"),
        "preflight": _moog_command_from_values(moog_config, "preflight"),
    }
    return {"fields": fields, "groups": groups, "commands": commands}


def apply_moog_setup_form(config, form: dict[str, list[str]]):
    current = normalize_moog_config(getattr(config, "moog", None))
    updated = dict(current)
    for meta in MOOG_SETUP_FIELDS:
        key = meta["key"]
        if meta.get("kind") == "checkbox":
            updated[key] = _truthy(_first_form_value(form, key))
            continue
        if key not in form:
            continue
        value = _first_form_value(form, key)
        if meta.get("secret") and value == "":
            continue
        updated[key] = value
    from profile_manager.moog import set_moog_config
    return set_moog_config(config, updated)


def _config_from_file_data(file_data: dict[str, Any]):
    try:
        from profile_manager.config import DeploymentConfig
        return DeploymentConfig.from_dict(file_data)
    except Exception:
        class _Fallback:
            moog = file_data.get("moog") if isinstance(file_data.get("moog"), dict) else {}
        return _Fallback()


def _first_env_key(keys: list[str], environ: dict[str, str]) -> str | None:
    for key in keys:
        if environ.get(key):
            return key
    return None


def _first_form_value(form: dict[str, list[str]], key: str) -> str:
    values = form.get(key) or [""]
    return str(values[0]).strip()


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _moog_command_from_values(values: dict[str, Any], command: str) -> str:
    repo = values.get("github_repo") or "<org/repo>"
    user = values.get("github_user") or "<github-user>"
    if command == "readiness":
        return f"cardano-profile moog readiness --repo {repo} --github-user {user} --json"
    directory = values.get("target_directory") or "<repo-relative-test-dir>"
    commit = values.get("target_commit") or "<sha>"
    asset_dir = values.get("asset_dir") or "<local-test-asset-dir>"
    return (
        "cardano-profile moog preflight "
        f"--asset-dir {asset_dir} --repo {repo} --github-user {user} "
        f"--directory {directory} --commit {commit} --json"
    )


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)
