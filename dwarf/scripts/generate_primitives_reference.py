#!/usr/bin/env python3
"""Generate a schema-derived Dwarf primitives reference document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _scenario_documents(scenarios_dir: Path) -> list[tuple[Path, dict]]:
    docs: list[tuple[Path, dict]] = []
    for path in sorted(scenarios_dir.glob("*.yaml")):
        try:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                docs.append((path, parsed))
        except yaml.YAMLError:
            continue
    return docs


def _example_block_for_primitive(name: str, family: str, scenarios: list[tuple[Path, dict]]) -> str:
    for path, document in scenarios:
        if family == "assertion":
            refs = document.get("assertions", [])
        else:
            refs = document.get("load", []) + document.get("setup", []) + document.get("fault", []) + document.get("teardown", [])
        for ref in refs:
            if ref.get("primitive") != name:
                continue
            example = {"scenario": document.get("id", path.stem), "reference": ref}
            return "```json\n" + json.dumps(example, indent=2, sort_keys=True) + "\n```"
    return "_No authored scenario example found._"


def _property_type(schema: dict) -> str:
    type_value = schema.get("type")
    if isinstance(type_value, list):
        return " | ".join(str(item) for item in type_value)
    if type_value is not None:
        return str(type_value)
    if "enum" in schema:
        return "enum"
    return "unspecified"


def _properties_table(properties: dict, required: set[str]) -> list[str]:
    if not properties:
        return ["_No parameters._"]
    lines = [
        "| Param | Type | Required | Description |",
        "| --- | --- | --- | --- |",
    ]
    for name in sorted(properties):
        prop = properties[name]
        description = str(prop.get("description") or "").strip() or "-"
        lines.append(
            f"| `{name}` | `{_property_type(prop)}` | `{'yes' if name in required else 'no'}` | {description} |"
        )
    return lines


def build_reference(repo_root: Path) -> str:
    registry = _load_json(repo_root / "primitives" / "registry.json")
    primitives = registry["primitives"]
    scenarios = _scenario_documents(repo_root / "scenarios")
    lines = [
        "# Dwarf Primitives Reference",
        "",
        "Generated from `dwarf/primitives/registry.json` plus the referenced JSON schemas.",
        "",
    ]
    for name in sorted(primitives):
        entry = primitives[name]
        schema_rel = entry["params_schema"]
        schema = _load_json(repo_root / schema_rel)
        family = entry.get("family", "unknown")
        supports = ", ".join(f"`{item}`" for item in entry.get("supports", [])) or "`-`"
        runtimes = ", ".join(f"`{item}`" for item in entry.get("runtimes", [])) or "`-`"
        required = set(schema.get("required", []))
        lines.extend(
            [
                f"## `{name}`",
                "",
                f"- Family: `{family}`",
                f"- Version: `{entry.get('version', 'unknown')}`",
                f"- Module: `{entry.get('module', '-')}`",
                f"- Class: `{entry.get('class', '-')}`",
                f"- Supports: {supports}",
                f"- Runtimes: {runtimes}",
                f"- Schema: `{schema_rel}`",
                "",
                "### Parameters",
                "",
                *(_properties_table(schema.get("properties", {}), required)),
                "",
                "### Example Invocation",
                "",
                _example_block_for_primitive(name, family, scenarios),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]), help="Path to dwarf/ root.")
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parents[2] / "agent" / "research" / "primitives-reference.md"),
        help="Output markdown path.",
    )
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root)
    markdown = build_reference(repo_root=repo_root)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
