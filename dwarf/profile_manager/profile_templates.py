"""Profile template discovery and rendering."""

from __future__ import annotations

from pathlib import Path


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "profiles" / "templates"


def list_templates() -> list[str]:
    return sorted(path.stem for path in TEMPLATES_DIR.glob("*.yaml"))


def render_template(*, template_name: str, profile_name: str, output_path: Path) -> Path:
    template_path = TEMPLATES_DIR / f"{template_name}.yaml"
    if not template_path.exists():
        raise FileNotFoundError(f"unknown profile template: {template_name}")
    body = template_path.read_text(encoding="utf-8")
    body = body.replace("{{PROFILE_ID}}", profile_name)
    body = body.replace("{{PROFILE_LABEL}}", profile_name)
    body = body.replace("{{REMOTE_RUNTIME_ROOT}}", f"/home/nigel/cardano-profiles/{profile_name}")
    body = body.replace("{{COMPOSE_PROJECT}}", f"dwarf-{profile_name}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(body, encoding="utf-8")
    return output_path
