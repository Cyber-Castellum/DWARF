import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "dwarf" / "scenarios" / "cardano-amaru-kes-security-local.yaml"
PROBE = ROOT / "tools" / "cardano_amaru_kes_security_probe.py"


def test_scenario_runs_the_new_package_through_dwarf():
    body = json.loads(SCENARIO.read_text(encoding="utf-8"))
    assert body["id"] == "cardano-amaru-kes-security-local"
    assert body["runtime"] == "devnet"
    assert len(body["load"]) == 1
    command = body["load"][0]["command"]
    assert "cardano_amaru_kes_security_probe.py" in command
    assert "antithesis/cardano_amaru_kes_security" in command
    assert body["assertions"] == [
        {"primitive": "load_events_are_ok", "min_completed": 1, "min_event_count": 1}
    ]


def test_probe_uses_unique_project_and_local_images_without_launching_antithesis():
    source = PROBE.read_text(encoding="utf-8")
    assert "dwarf-kes-security-" in source
    assert "dwarf-kes-proxy:local" in source
    assert "dwarf-kes-workload:local" in source
    assert "docker\", \"compose" in source
    assert "snouty" not in source
    assert "moog" not in source.lower()
    assert "launch" not in source.lower()


def test_probe_executes_every_test_command_in_documented_order():
    source = PROBE.read_text(encoding="utf-8")
    commands = (
        "parallel_driver_observe_kes.py",
        "anytime_kes_non_adoption.py",
        "eventually_kes_recovery.py",
    )
    positions = [source.index(command) for command in commands]
    assert positions == sorted(positions)


def test_probe_requires_true_semantic_verdicts_not_only_zero_exit_codes():
    source = PROBE.read_text(encoding="utf-8")
    assert "command_semantics_ok" in source
    assert 'evaluation.get("classifiable") is True' in source
    assert 'evaluation.get("safe") is True' in source
    assert 'payload.get("recovered") is True' in source


def test_probe_allows_the_proven_mixed_bootstrap_more_than_five_minutes():
    source = PROBE.read_text(encoding="utf-8")
    assert "bootstrap_timeout = min(args.timeout_seconds, 1800)" in source
    assert 'compose + ["up", "-d"], timeout=bootstrap_timeout' in source
    assert "except subprocess.TimeoutExpired" in source


def test_probe_retains_raw_security_evidence_in_the_dwarf_bundle():
    source = PROBE.read_text(encoding="utf-8")
    assert '"raw-cardano-proxy-events.jsonl"' in source
    assert '"raw-amaru-proxy-events.jsonl"' in source
    assert '"raw-amaru-victim.log"' in source
    assert '"/amaru-evidence/amaru.log"' in source


def test_recovery_command_prints_its_boolean_verdict():
    command = (
        ROOT
        / "antithesis"
        / "cardano_amaru_kes_security"
        / "workload"
        / "test"
        / "v1"
        / "mixed-kes-security"
        / "eventually_kes_recovery.py"
    ).read_text(encoding="utf-8")
    assert '"recovered": recovered' in command
