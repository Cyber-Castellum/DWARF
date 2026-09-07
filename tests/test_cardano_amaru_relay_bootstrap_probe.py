import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "cardano_amaru_relay_bootstrap_probe.py"
SCENARIO = (
    ROOT / "dwarf" / "scenarios" / "cardano-amaru-relay-bootstrap-control-local.yaml"
)


def load_probe():
    assert TOOL.is_file(), f"runtime probe is missing: {TOOL}"
    spec = importlib.util.spec_from_file_location("relay_bootstrap_probe", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_new_dwarf_scenario_exists_and_uses_only_the_new_package():
    assert SCENARIO.is_file(), f"new DWARF scenario is missing: {SCENARIO}"
    body = json.loads(SCENARIO.read_text(encoding="utf-8"))
    assert body["id"] == "cardano-amaru-relay-bootstrap-control-local"
    assert body["runtime"] == "devnet"
    assert body["evidence_intent"] == "regression"
    assert len(body["load"]) == 1
    command = body["load"][0]["command"]
    assert "cardano_amaru_relay_bootstrap_probe.py" in command
    assert "cardano_amaru_relay_bootstrap_control" in command
    assert "cardano_amaru_security" not in command
    assert "cardano_amaru_adversarial" not in command
    assert body["assertions"] == [
        {
            "primitive": "load_events_are_ok",
            "min_completed": 1,
            "min_event_count": 1,
        }
    ]


def test_scenario_passes_the_real_dwarf_loader():
    assert SCENARIO.is_file(), f"new DWARF scenario is missing: {SCENARIO}"
    sys.path.insert(0, str(ROOT / "dwarf"))
    try:
        from profile_manager.scenario import load_scenario

        loaded = load_scenario(SCENARIO)
    finally:
        sys.path.pop(0)
    assert loaded.id == "cardano-amaru-relay-bootstrap-control-local"
    assert loaded.load[0].primitive == "load_shell_command"


def test_relay_parser_separates_bootstrap_targets_from_runtime_progress():
    probe = load_probe()
    log = """
[amaru-relay-1 bootstrap-producer] + era-readiness predicate satisfied - tip_slot=1601 tip_epoch=4 snapshot_slots=399 799 1199
[amaru-relay-1] bootstrap attempt #9: committed bundle to /srv/amaru
[amaru-relay-1] bundle ready at /srv/amaru, exec'ing amaru run
2026-09-05T00:00:01Z INFO chainsync.intersect_found current=Point { slot: Slot(1210) } highest=Point { slot: Slot(1620) }
2026-09-05T00:00:02Z DEBUG chainsync.roll_forward_done current=Point { slot: Slot(1621) } outcome="stored"
"""
    parsed = probe.parse_relay_log(log)
    assert parsed["target_slots"] == [399, 799, 1199]
    assert parsed["committed"] is True
    assert parsed["exec_started"] is True
    assert parsed["runtime_slots"] == [1210, 1620, 1621]
    assert parsed["max_runtime_slot"] == 1621
    assert parsed["fatal_signatures"] == []


def test_relay_epoch_gate_requires_a_post_bootstrap_transition():
    probe = load_probe()
    base = {
        "target_slots": [399, 799, 1199],
        "committed": True,
        "exec_started": True,
        "runtime_slots": [1201, 1599],
        "max_runtime_slot": 1599,
        "fatal_signatures": [],
    }
    assert probe.relay_crossed_post_bootstrap_epoch(base, epoch_length=400) is False
    base["runtime_slots"].append(1601)
    base["max_runtime_slot"] = 1601
    assert probe.relay_crossed_post_bootstrap_epoch(base, epoch_length=400) is True


def test_relay_parser_detects_known_fatal_classes():
    probe = load_probe()
    log = """
[amaru-relay-2] bundle ready at /srv/amaru, exec'ing amaru run
thread 'tokio-rt-worker' panicked at epoch_transition.rs
discrepancy between expected total rewards and actual total rewards
Consensus died
"""
    parsed = probe.parse_relay_log(log)
    assert set(parsed["fatal_signatures"]) >= {
        "panic",
        "reward-discrepancy",
        "consensus-died",
    }


def test_genesis_start_parser_reads_both_clock_anchors(monkeypatch):
    probe = load_probe()

    class Completed:
        returncode = 0
        stdout = (
            json.dumps({"systemStart": "2026-09-05T05:00:00Z"})
            + "\n---DWARF-GENESIS---\n"
            + json.dumps({"startTime": 1788584400})
        )
        stderr = ""

    monkeypatch.setattr(probe, "run", lambda *args, **kwargs: Completed())
    assert probe.query_genesis_start("p1") == {
        "shelley_system_start": "2026-09-05T05:00:00Z",
        "byron_start_time": 1788584400,
    }
