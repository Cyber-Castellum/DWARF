import json
import re
from pathlib import Path

from profile_manager.data.coverage import scenario_census
from profile_manager.data.learn_api import html_route_groups
from profile_manager.views.learn_overview import render_learn_overview
from profile_manager.views.learn import render_learn_landing
from profile_manager.views.status import render_learn_status
from profile_manager.views.threat_coverage import render_learn_threat_coverage


ROOT = Path(__file__).resolve().parents[1]


def _documented_html_routes():
    return {route for group in html_route_groups() for route in group["routes"]}


def test_api_reference_covers_current_browser_surface():
    required = {
        "/operate/audit",
        "/operate/antithesis",
        "/operate/config/edit",
        "/operate/crashes",
        "/operate/primitives/new",
        "/operate/profiles/new",
        "/operate/schedule",
        "/operate/scenarios/edit/<id>",
        "/operate/targets/new",
        "/learn/attack-cost",
        "/learn/consensus",
        "/learn/developer-onboarding",
        "/learn/operator-runbook",
        "/learn/overview",
        "/learn/plugin-authoring",
        "/learn/status",
        "/learn/threat-coverage",
    }

    assert required <= _documented_html_routes()


def test_status_renders_deployed_revision_and_live_inventory(monkeypatch):
    monkeypatch.setenv("DWARF_SOURCE_REVISION", "0123456789abcdef")
    total = sum(
        cell["count"]
        for (row, column), cell in scenario_census()["cells"].items()
        if column == "__total"
    )
    primitive_total = len(
        json.loads((ROOT / "dwarf/primitives/registry.json").read_text(encoding="utf-8"))["primitives"]
    )

    html = render_learn_status()

    assert "0123456789abcdef" in html
    assert f"{total} scenarios" in html
    assert f"{primitive_total} primitives" in html
    assert "Last 0 feature commits" not in html


def test_threat_coverage_reconciles_to_runtime_scenario_catalog():
    html = render_learn_threat_coverage()
    match = re.search(r"const DATA = (\{.*\});\nconst TYPES", html)
    assert match is not None
    data = json.loads(match.group(1))
    expected = sum(
        cell["count"]
        for (row, column), cell in scenario_census()["cells"].items()
        if column == "__total"
    )

    assert data["meta"]["n_scen"] == expected
    assert len(data["scenarios"]) == expected


def test_overview_describes_proven_mixed_generation_honestly():
    html = render_learn_overview()

    assert "Amaru / differential generation is follow-on work" not in html
    assert "Mixed Cardano/Amaru N2N state-machine" in html
    assert "local runtime proof" in html
    assert "not a valid live Antithesis result" in html
    assert "239 shipped scenarios" not in html
    assert "on-wire adversary mode is follow-on work" not in html


def test_learn_landing_uses_runtime_primitive_count():
    expected = len(
        json.loads((ROOT / "dwarf/primitives/registry.json").read_text(encoding="utf-8"))["primitives"]
    )

    html = render_learn_landing()
    source = (ROOT / "dwarf/dashboard/templates/learn/landing.j2").read_text(encoding="utf-8")

    assert f"the {expected}-primitive catalogue" in html
    assert "the 206-primitive catalogue" not in source


def test_public_runbook_contains_portable_moog_paths():
    source = (ROOT / "dwarf/dashboard/templates/learn/operator_runbook.j2").read_text(
        encoding="utf-8"
    )

    assert "/home/nigel" not in source
    assert "/srv/dwarf" in source
