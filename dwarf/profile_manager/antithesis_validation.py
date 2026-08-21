from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


MOOG_FAULT_CLASSES = frozenset({"network", "kill", "pause", "stop"})
MOOG_FAULT_EXCLUSION_LABEL = "com.antithesis.exclude_from_faults"


def moog_fault_exclusion_errors(compose_path: Path) -> list[str]:
    """Return MOOG-compatible fault-exclusion validation errors."""
    try:
        document = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return [f"cannot parse {compose_path.name}: {exc}"]

    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict):
        return []

    errors: list[str] = []
    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        value: Any = None
        labels = service.get("labels")
        if isinstance(labels, dict):
            value = labels.get(MOOG_FAULT_EXCLUSION_LABEL)
        elif isinstance(labels, list):
            prefix = f"{MOOG_FAULT_EXCLUSION_LABEL}="
            value = next(
                (
                    item[len(prefix):]
                    for item in labels
                    if isinstance(item, str) and item.startswith(prefix)
                ),
                None,
            )

        if value is None:
            continue
        if not isinstance(value, str):
            errors.append(
                f"{service_name}: fault exclusion must be a comma-separated string"
            )
            continue
        for token in (part.strip() for part in value.split(",")):
            if token and token not in MOOG_FAULT_CLASSES:
                errors.append(f'{service_name}: unknown fault class "{token}"')
    return errors
