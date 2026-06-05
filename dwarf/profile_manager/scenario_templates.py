"""Scenario template discovery and rendering."""

from __future__ import annotations

from pathlib import Path


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "scenarios" / "templates"


def list_templates() -> list[str]:
    return sorted(path.stem for path in TEMPLATES_DIR.glob("*.yaml"))


def render_template(*, template_name: str, scenario_name: str, output_path: Path) -> Path:
    template_path = TEMPLATES_DIR / f"{template_name}.yaml"
    if not template_path.exists():
        raise FileNotFoundError(f"unknown scenario template: {template_name}")
    body = template_path.read_text(encoding="utf-8")
    body = body.replace("{{SCENARIO_ID}}", scenario_name)
    body = body.replace("{{SCENARIO_TITLE}}", scenario_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(body, encoding="utf-8")
    return output_path
