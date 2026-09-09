import json
import re

from profile_manager.data.operate_bundles import _enrich_bundle_row
from profile_manager.data.operate_runs import _enrich_run_row
from profile_manager.views.learn import render_learn_landing
from profile_manager.views.learn_attack_cost import render_learn_attack_cost
from profile_manager.views.learn_docs import render_learn_glossary
from profile_manager.views.learn_examples import render_learn_examples
from profile_manager.views.operate_bundles import render_operate_bundles
from profile_manager.views.scenarios import render_operate_scenarios
from profile_manager.views.threat_coverage import render_learn_threat_coverage


def test_scenario_and_example_deep_links_have_real_destinations():
    scenarios = render_operate_scenarios()
    examples = render_learn_examples()
    ids = re.findall(r'href="/operate/scenarios#([^"]+)"', examples)

    assert ids
    for scenario_id in ids:
        assert f'id="{scenario_id}"' in scenarios
        assert f'id="{scenario_id}"' in examples


def test_consensus_glossary_anchor_matches_attack_cost_link(monkeypatch):
    from profile_manager.data import attack_cost

    attack_cost.reset_cache()
    monkeypatch.setattr(attack_cost, "_fetch_json", lambda url, timeout: (_ for _ in ()).throw(TimeoutError()))
    attack = render_learn_attack_cost()
    glossary = render_learn_glossary()
    target = re.search(r'href="/learn/glossary#([^"]+)"', attack)

    assert target is not None
    assert f'id="{target.group(1)}"' in glossary
    assert target.group(1) == "term-k-security-parameter"


def test_missing_historical_profile_is_not_linked(tmp_path):
    run_id = "historic-run"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    manifest = {
        "profile": {"id": "profile-that-no-longer-exists"},
        "target": {"implementation": "cardano-node"},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    run = {"run_id": run_id, "source": "local"}

    row = _enrich_run_row(run, tmp_path)

    assert row["profile_id"] == "profile-that-no-longer-exists"
    assert row["profile_url"] is None


def test_missing_bundle_profile_is_not_linked(tmp_path):
    run_id = "historic-bundle"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"profile": {"id": "profile-that-no-longer-exists"}}),
        encoding="utf-8",
    )
    bundle = tmp_path / f"{run_id}.tar.gz"
    bundle.write_bytes(b"archive")

    row = _enrich_bundle_row(bundle, tmp_path)

    assert row["profile_id"] == "profile-that-no-longer-exists"
    assert row["profile_url"] is None


def test_operate_and_learn_controls_have_accessible_names():
    landing = render_learn_landing()
    bundles = render_operate_bundles()
    threat = render_learn_threat_coverage()

    assert len(re.findall(r"<h1(?:\s|>)", landing)) == 1
    assert re.search(r'<label[^>]+for="bundle-import"', bundles)
    assert 'id="bundle-import"' in bundles
    assert re.search(r'<label[^>]+for="q"', threat)
