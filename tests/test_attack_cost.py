import importlib
import importlib.util

from profile_manager.views.learn_attack_cost import render_learn_attack_cost


def _provider():
    spec = importlib.util.find_spec("profile_manager.data.attack_cost")
    assert spec is not None, "server-side attack-cost provider is missing"
    return importlib.import_module("profile_manager.data.attack_cost")


def test_live_provider_validates_and_caches_upstream_data(monkeypatch):
    provider = _provider()
    provider.reset_cache()
    responses = {
        "totals": [{"epoch_no": "700", "circulation": "36000000000000000"}],
        "epoch_info": [{"active_stake": "21000000000000000"}],
        "simple/price": {"cardano": {"usd": 0.5}},
    }
    calls = []

    def fake_fetch(url, timeout):
        calls.append((url, timeout))
        return next(value for key, value in responses.items() if key in url)

    monkeypatch.setattr(provider, "_fetch_json", fake_fetch)
    first = provider.attack_cost_payload(now=1000.0)
    second = provider.attack_cost_payload(now=1001.0)

    assert first["source"] == "live"
    assert first["epoch"] == 700
    assert first["active_ada"] == 21_000_000_000
    assert first["price_usd"] == 0.5
    assert second == first
    assert len(calls) == 3
    assert all(timeout <= 4 for _, timeout in calls)


def test_provider_falls_back_to_explicit_snapshot(monkeypatch):
    provider = _provider()
    provider.reset_cache()

    def unavailable(url, timeout):
        raise TimeoutError("offline")

    monkeypatch.setattr(provider, "_fetch_json", unavailable)
    payload = provider.attack_cost_payload(now=2000.0)

    assert payload["source"] == "snapshot"
    assert payload["source_detail"]
    assert payload["active_ada"] > 0
    assert payload["price_usd"] > 0


def test_page_uses_server_payload_without_cross_origin_browser_fetch(monkeypatch):
    provider = _provider()
    provider.reset_cache()

    def unavailable(url, timeout):
        raise TimeoutError("offline")

    monkeypatch.setattr(provider, "_fetch_json", unavailable)
    html = render_learn_attack_cost()

    assert "fetch('https://" not in html
    assert "snapshot" in html
    assert "live feed unavailable here" not in html
    assert "term-k-security-parameter" in html
