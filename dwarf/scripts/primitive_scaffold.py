#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path


FAMILY_CLASS = {
    "setup": "LoadPrimitive",
    "load": "LoadPrimitive",
    "probe": "ProbePrimitive",
    "assertion": "AssertionPrimitive",
    "fault": "LoadPrimitive",
    "teardown": "LoadPrimitive",
}


def _helper_body(name: str, family: str) -> str:
    return f'''#!/usr/bin/env python3

"""Scaffold helper for `{name}` ({family})."""

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "result.json"
    payload = {{
        "schema_version": "v1",
        "primitive": "{name}",
        "status": "scaffold-placeholder"
    }}
    result_path.write_text(json.dumps(payload, indent=2) + "\\n", encoding="utf-8")
    print(f"primitive={name} result_relpath={{result_path}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''


def _schema_body(name: str, family: str) -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "description": f"Scaffold schema for {name} ({family}).",
        "properties": {
            "output_dir": {
                "type": "string",
                "description": "Directory where the helper should write its result artifacts.",
            }
        },
        "required": ["output_dir"],
    }


def _test_body(name: str) -> str:
    return f'''import unittest


class Runtime{name.title().replace("_", "")}ScaffoldTests(unittest.TestCase):
    def test_scaffold_placeholder(self):
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
'''


def _registry_entry(name: str, family: str) -> dict:
    return {
        "module": "profile_manager.primitives",
        "class": FAMILY_CLASS[family],
        "version": "0.1.0",
        "family": family,
        "supports": ["amaru", "cardano-node"],
        "runtimes": ["library"],
        "params_schema": f"primitives/{family}/{name}.schema.json",
        "scaffold_note": "Replace the default class binding in profile_manager.primitives before using this primitive in a runnable scenario.",
    }


def scaffold_primitive(*, repo_root: Path, family: str, name: str) -> dict[str, Path]:
    helper_path = repo_root / "scripts" / f"runtime_{name}.py"
    schema_path = repo_root / "primitives" / family / f"{name}.schema.json"
    test_path = repo_root / "tests" / f"test_runtime_{name}.py"
    registry_path = repo_root / "primitives" / "registry.json"

    helper_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.parent.mkdir(parents=True, exist_ok=True)

    helper_path.write_text(_helper_body(name, family), encoding="utf-8")
    schema_path.write_text(json.dumps(_schema_body(name, family), indent=2) + "\n", encoding="utf-8")
    test_path.write_text(_test_body(name), encoding="utf-8")

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    primitives = registry.setdefault("primitives", {})
    if name in primitives:
        raise ValueError(f"primitive {name!r} already exists")
    primitives[name] = _registry_entry(name, family)
    registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    return {
        "helper_path": helper_path,
        "schema_path": schema_path,
        "test_path": test_path,
        "registry_path": registry_path,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a primitive scaffold")
    parser.add_argument("--family", required=True, choices=sorted(FAMILY_CLASS))
    parser.add_argument("--name", required=True)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)

    written = scaffold_primitive(repo_root=Path(args.repo_root), family=args.family, name=args.name)
    print(json.dumps({k: str(v) for k, v in written.items()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
