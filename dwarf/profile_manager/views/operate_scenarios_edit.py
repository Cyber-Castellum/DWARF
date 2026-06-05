"""View for /operate/scenarios/edit/<id> + /operate/scenarios/edit.

Item #15 — in-browser scenario YAML authoring with validate-on-blur
and save. Existing /operate/scenarios/new keeps its template-picker
role; this view is the editor for an existing scenario *or* a fresh
authoring buffer when no id is supplied.

The textarea hosts the canonical bytes; the JS layer talks to
/api/scenario/validate + /api/scenario/save with the dashboard token.
We deliberately don't ship a heavy editor dep — vanilla textarea +
client-side line-counter line markers is sufficient for the dense
scenario shape.
"""
from __future__ import annotations

import os
from pathlib import Path

from profile_manager.templating import render


_BLANK_SKELETON = (
    '{\n'
    '  "spec_version": "v1",\n'
    '  "id": "",\n'
    '  "title": "",\n'
    '  "authors": ["dwarf"],\n'
    '  "tags": [],\n'
    '  "target": {"implementation": "amaru", "version": "any"},\n'
    '  "runtime": "library"\n'
    '}\n'
)


def _scenarios_dir() -> Path:
    env = os.environ.get("ADA2_DWARF_SCENARIOS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "scenarios"


def _safe_id(scenario_id: str) -> bool:
    if not scenario_id:
        return False
    if "/" in scenario_id or ".." in scenario_id:
        return False
    return all(ch.isalnum() or ch in "-_" for ch in scenario_id)


def render_operate_scenarios_edit(scenario_id: str = "") -> str:
    scenarios_dir = _scenarios_dir()
    body = ""
    found = False
    sid = scenario_id.strip()
    if sid and _safe_id(sid):
        path = scenarios_dir / f"{sid}.yaml"
        if path.is_file():
            try:
                body = path.read_text(encoding="utf-8")
                found = True
            except OSError:
                body = ""
    if not body:
        body = _BLANK_SKELETON
    return render(
        "operate/scenarios_edit.j2",
        page_title=f"Edit · {sid}" if sid and found else "New scenario (editor)",
        density="reading",
        active="operate",
        active_sub="scenarios",
        scenario_id=sid if found else "",
        scenario_body=body,
        is_new=not found,
        scenarios_dir=str(scenarios_dir),
    )
