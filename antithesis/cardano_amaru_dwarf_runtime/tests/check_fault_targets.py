#!/usr/bin/env python3
"""Validate the resolved MOOG fault scope and service/runtime identity contract."""
import json
import sys


EXCLUSION_LABEL = "com.antithesis.exclude_from_faults"
EXPECTED_TARGETS = {"amaru-relay-1", "amaru-relay-2"}


def labels_as_dict(labels):
    if isinstance(labels, dict):
        return labels
    result = {}
    for item in labels or []:
        key, _, value = item.partition("=")
        result[key] = value
    return result


def validate(compose):
    services = compose.get("services", {})
    eligible = {
        name
        for name, service in services.items()
        if EXCLUSION_LABEL not in labels_as_dict(service.get("labels"))
    }
    errors = []
    if eligible != EXPECTED_TARGETS:
        errors.append(
            f"fault-eligible services are {sorted(eligible)}, expected {sorted(EXPECTED_TARGETS)}"
        )
    for name, service in services.items():
        if name in eligible:
            continue
        runtime_name = service.get("container_name")
        if runtime_name != name:
            errors.append(
                f"excluded service {name!r} has runtime name {runtime_name!r}; expected {name!r}"
            )
    return errors


def main():
    try:
        compose = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as error:
        print(f"invalid Compose JSON: {error}", file=sys.stderr)
        return 2
    errors = validate(compose)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("fault targets: amaru-relay-1, amaru-relay-2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
