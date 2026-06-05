import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_ENV = "ADA2_PROFILE_MANAGER_CONFIG"
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "state" / "config.yaml"

CONFIG_FIELDS = {
    "deployment_name": {"type": "string", "default": "cardano-box", "description": "Human-readable deployment label."},
    "host": {"type": "string", "default": "192.168.30.16", "description": "Remote SSH host or IP."},
    "ssh_user": {"type": "string", "default": "nigel", "description": "Remote SSH username."},
    "ssh_key_path": {"type": "string", "default": "/Users/nigel/.ssh/cardano-box", "description": "SSH private-key path used for remote commands."},
    "remote_base_path": {"type": "string", "default": "/home/nigel/cardano-profiles", "description": "Remote base directory for deployment artifacts."},
    "allow_prereq_install": {"type": "boolean", "default": False, "description": "Allow prerequisite installation via CLI."},
    "allow_sudo": {"type": "boolean", "default": False, "description": "Allow sudo-backed remote commands."},
    "log_level": {"type": "string", "default": "info", "description": "Operator-facing CLI log level."},
    "output_format": {"type": "string", "default": "text", "description": "Default human output format for future export-capable commands."},
    "docker_registry": {"type": "string", "default": "", "description": "Default Docker registry prefix for framework images."},
    "runs_retention_days": {"type": "integer", "default": 30, "description": "Suggested local run retention window."},
    "bundles_retention_days": {"type": "integer", "default": 90, "description": "Suggested retained export/archive window."},
    "sarif_rules": {"type": "array[string]", "default": [], "description": "Optional SARIF rule filters or preferred rule identifiers."},
}


@dataclass(frozen=True)
class DeploymentConfig:
    deployment_name: str
    host: str
    ssh_user: str
    ssh_key_path: str
    remote_base_path: str
    allow_prereq_install: bool = False
    allow_sudo: bool = False
    log_level: str = "info"
    output_format: str = "text"
    docker_registry: str = ""
    runs_retention_days: int = 30
    bundles_retention_days: int = 90
    sarif_rules: list[str] = None

    @classmethod
    def from_dict(cls, data):
        normalized = {}
        for key, meta in CONFIG_FIELDS.items():
            normalized[key] = data.get(key, meta["default"])
        return cls(
            deployment_name=normalized["deployment_name"],
            host=normalized["host"],
            ssh_user=normalized["ssh_user"],
            ssh_key_path=normalized["ssh_key_path"],
            remote_base_path=normalized["remote_base_path"],
            allow_prereq_install=bool(normalized["allow_prereq_install"]),
            allow_sudo=bool(normalized["allow_sudo"]),
            log_level=str(normalized["log_level"]),
            output_format=str(normalized["output_format"]),
            docker_registry=str(normalized["docker_registry"]),
            runs_retention_days=int(normalized["runs_retention_days"]),
            bundles_retention_days=int(normalized["bundles_retention_days"]),
            sarif_rules=list(normalized["sarif_rules"] or []),
        )

    def to_dict(self):
        return {
            "deployment_name": self.deployment_name,
            "host": self.host,
            "ssh_user": self.ssh_user,
            "ssh_key_path": self.ssh_key_path,
            "remote_base_path": self.remote_base_path,
            "allow_prereq_install": self.allow_prereq_install,
            "allow_sudo": self.allow_sudo,
            "log_level": self.log_level,
            "output_format": self.output_format,
            "docker_registry": self.docker_registry,
            "runs_retention_days": self.runs_retention_days,
            "bundles_retention_days": self.bundles_retention_days,
            "sarif_rules": list(self.sarif_rules or []),
        }


def config_path():
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_CONFIG


def config_exists():
    return config_path().exists()


def load_config():
    with config_path().open("r", encoding="utf-8") as handle:
        return DeploymentConfig.from_dict(json.load(handle))


def save_config(config):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def list_config_values(config: DeploymentConfig) -> dict[str, Any]:
    return config.to_dict()


def parse_config_value(key: str, raw: str) -> Any:
    if key not in CONFIG_FIELDS:
        raise KeyError(key)
    type_name = CONFIG_FIELDS[key]["type"]
    if type_name == "string":
        return raw
    if type_name == "boolean":
        value = raw.strip().lower()
        if value in {"true", "1", "yes", "y", "on"}:
            return True
        if value in {"false", "0", "no", "n", "off"}:
            return False
        raise ValueError(f"invalid boolean for {key}: {raw}")
    if type_name == "integer":
        return int(raw)
    if type_name == "array[string]":
        value = raw.strip()
        if not value:
            return []
        if value.startswith("["):
            parsed = json.loads(value)
            if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
                raise ValueError(f"invalid array[string] for {key}")
            return parsed
        return [item.strip() for item in value.split(",") if item.strip()]
    raise ValueError(f"unsupported config type for {key}: {type_name}")


def set_config_value(config: DeploymentConfig, key: str, raw: str) -> DeploymentConfig:
    values = config.to_dict()
    values[key] = parse_config_value(key, raw)
    return DeploymentConfig.from_dict(values)
