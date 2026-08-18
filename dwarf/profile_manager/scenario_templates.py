"""Scenario template discovery and rendering.

A "template" is any existing scenario in ``dwarf/scenarios/``. ``scenario new``
clones one as the starting point for a new scenario, rewriting its ``id`` and
``title`` to the new name. This means the whole catalog (and any scenarios added
later) is available as a template — there is no separate templates directory to
keep in sync.

For backward compatibility, if a source file still uses the legacy
``{{SCENARIO_ID}}`` / ``{{SCENARIO_TITLE}}`` placeholders, those are substituted
textually instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml


SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"


def list_templates() -> list[str]:
    """Every existing scenario id is usable as a template."""
    return sorted(path.stem for path in SCENARIOS_DIR.glob("*.yaml"))


def render_template(*, template_name: str, scenario_name: str, output_path: Path) -> Path:
    source_path = SCENARIOS_DIR / f"{template_name}.yaml"
    if not source_path.exists():
        available = ", ".join(list_templates()[:8])
        raise FileNotFoundError(
            f"unknown scenario template: {template_name!r}. "
            f"Use any existing scenario id as a template (e.g. {available}, ...). "
            f"They are the files in dwarf/scenarios/ (also listed at /operate/scenarios)."
        )

    body = source_path.read_text(encoding="utf-8")

    # Legacy placeholder templates: substitute textually and keep the raw shape.
    if "{{SCENARIO_ID}}" in body or "{{SCENARIO_TITLE}}" in body:
        body = body.replace("{{SCENARIO_ID}}", scenario_name)
        body = body.replace("{{SCENARIO_TITLE}}", scenario_name)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(body, encoding="utf-8")
        return output_path

    # Clone an existing scenario: parse, rename its identity to the new name,
    # write back in the catalog's JSON house style.
    data = yaml.safe_load(body)
    if isinstance(data, dict):
        data["id"] = scenario_name
        if "title" in data:
            data["title"] = scenario_name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return output_path
