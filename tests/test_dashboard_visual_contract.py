from pathlib import Path

from profile_manager.data import schedule_store
from profile_manager.views import operate_schedule


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "tools" / "dashboard_visual_audit.js"
CSS = ROOT / "dwarf" / "dashboard" / "static" / "css" / "base.css"
SCHEDULE_TEMPLATE = ROOT / "dwarf" / "dashboard" / "templates" / "operate" / "schedule.j2"
RUNS_TEMPLATE = ROOT / "dwarf" / "dashboard" / "templates" / "operate" / "runs.j2"
OVERVIEW = ROOT / "dwarf" / "dashboard" / "static" / "overview.html"
CONSENSUS = ROOT / "dwarf" / "dashboard" / "static" / "consensus-differential.html"
THREAT_COVERAGE = ROOT / "dwarf" / "profile_manager" / "data" / "threat_risk_coverage.html"


def test_visual_audit_is_read_only_and_checks_both_viewports():
    source = AUDIT.read_text(encoding="utf-8")

    assert "1440" in source
    assert "390" in source
    assert "scrollWidth" in source
    assert "rgb(255, 255, 255)" in source
    assert "naturalWidth" in source
    assert "pageerror" in source
    assert "waitForLoadState('load'" in source
    assert "reducedMotion: 'reduce'" in source
    assert ".click(" not in source
    assert "method: 'POST'" not in source


def test_shared_css_themes_native_controls_and_contains_wide_content():
    source = CSS.read_text(encoding="utf-8")

    for selector in (
        '.shell-main input:not([type="checkbox"]):not([type="radio"])',
        ".shell-main select",
        ".shell-main textarea",
        ".shell-main button",
        ".responsive-table",
    ):
        assert selector in source
    assert "overflow-wrap: anywhere" in source
    assert "overflow-x: auto" in source
    assert "overflow-x: clip" in source
    assert "@media (max-width: 640px)" in source


def test_schedule_ui_uses_product_facing_resolved_store_path():
    source = SCHEDULE_TEMPLATE.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert "$ADA2_DWARF_STATE_DIR" not in source
    assert "DWARF state store" in source
    assert "{{ store_path }}" in source
    assert ".schedule-form" in css
    assert ".schedule-btn" in css


def test_schedule_view_passes_the_resolved_store_path(monkeypatch, tmp_path):
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path))
    captured = {}

    monkeypatch.setattr(schedule_store, "list_entries", lambda: [])
    monkeypatch.setattr(operate_schedule, "_scenario_options", lambda: [])
    monkeypatch.setattr(
        operate_schedule,
        "render",
        lambda template, **context: captured.update(template=template, **context) or "rendered",
    )

    assert schedule_store.store_path() == tmp_path / "schedule.json"
    assert operate_schedule.render_operate_schedule() == "rendered"
    assert captured["store_path"] == str(tmp_path / "schedule.json")


def test_legacy_static_pages_obey_the_visual_contract():
    overview = OVERVIEW.read_text(encoding="utf-8")
    consensus = CONSENSUS.read_text(encoding="utf-8")
    threat_coverage = THREAT_COVERAGE.read_text(encoding="utf-8")

    assert "api.koios.rest" not in overview
    assert "snapshot · live figures: /learn/attack-cost" in overview
    assert "--obsidian-0" in consensus
    assert "background:var(--obsidian-0)" in consensus
    assert "table{display:block;overflow-x:auto" in consensus
    assert "table{display:block;overflow-x:auto" in threat_coverage


def test_dense_mobile_tables_scroll_instead_of_collapsing_columns():
    css = CSS.read_text(encoding="utf-8")
    runs = RUNS_TEMPLATE.read_text(encoding="utf-8")
    consensus = CONSENSUS.read_text(encoding="utf-8")
    threat_coverage = THREAT_COVERAGE.read_text(encoding="utf-8")

    assert '<div class="responsive-table runs-table-wrap">' in runs
    assert ".runs-table-wrap .runs-table" in css
    assert consensus.count('<div class="table-scroll">') == 2
    assert threat_coverage.count('<div class="table-scroll">') == 2
