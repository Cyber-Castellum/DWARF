#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema.validators import validator_for


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA_GLOBS = [
    "dwarf/primitives/**/*.schema.json",
]


def discover_schema_paths(repo_root: Path) -> list[Path]:
    paths: list[Path] = []
    for pattern in DEFAULT_SCHEMA_GLOBS:
        paths.extend(sorted(repo_root.glob(pattern)))
    return paths


def validate_schema_paths(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in paths:
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{path}: invalid JSON: {exc}")
            continue
        try:
            validator_cls = validator_for(schema)
            validator_cls.check_schema(schema)
        except Exception as exc:
            errors.append(f"{path}: invalid JSON Schema: {exc}")
    return errors


def main(argv: list[str]) -> int:
    paths = [Path(arg).resolve() for arg in argv] if argv else discover_schema_paths(REPO_ROOT)
    errors = validate_schema_paths(paths)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"validated {len(paths)} JSON schema files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
