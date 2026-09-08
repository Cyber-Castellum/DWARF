import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = (
    ROOT / "dwarf" / "scenarios" / "cardano-amaru-miniprotocol-security-local.yaml"
)
PROBE = ROOT / "tools" / "cardano_amaru_miniprotocol_security_probe.py"


def test_scenario_runs_the_exact_new_package_through_dwarf():
    body = json.loads(SCENARIO.read_text(encoding="utf-8"))
    assert body["id"] == "cardano-amaru-miniprotocol-security-local"
    assert body["runtime"] == "devnet"
    assert body["seed"] == "0x20260907"
    assert len(body["load"]) == 1
    command = body["load"][0]["command"]
    assert "cardano_amaru_miniprotocol_security_probe.py" in command
    assert "antithesis/cardano_amaru_miniprotocol_security" in command
    assert body["assertions"] == [
        {"primitive": "load_events_are_ok", "min_completed": 1, "min_event_count": 1}
    ]


def test_probe_uses_fresh_project_and_local_workload_without_live_submission():
    source = PROBE.read_text(encoding="utf-8")
    assert "dwarf-mixed-sm-" in source
    assert "dwarf-miniprotocol-workload:local" in source
    assert '"fresh_project": True' in source
    assert '"antithesis_submitted": False' in source
    assert "snouty" not in source
    assert "create-test" not in source
    assert "launch" not in source.lower()


def test_probe_retains_seed_digests_revisions_transcripts_and_sample_counts():
    source = PROBE.read_text(encoding="utf-8")
    for token in (
        "compose_sha256",
        "source_revisions",
        "resolved_images",
        "seed",
        "raw-cardano-fuzzer.log",
        "raw-amaru-fuzzer.log",
        "samples.jsonl",
        "sample_counts",
        "container_states",
    ):
        assert token in source


def test_probe_runs_all_bounded_test_commands_and_checks_semantics():
    source = PROBE.read_text(encoding="utf-8")
    commands = (
        "parallel_driver_fuzz_observe.py",
        "anytime_containment.py",
        "eventually_recovery.py",
    )
    positions = [source.index(command) for command in commands]
    assert positions == sorted(positions)
    assert "command_semantics_ok" in source
    assert 'payload.get("classifiable") is True' in source
    assert 'payload.get("safe") is True' in source
    assert 'payload.get("recovered") is True' in source


def test_probe_requires_runtime_security_gates_not_just_startup():
    source = PROBE.read_text(encoding="utf-8")
    for token in (
        "complete_coverage",
        "post_setup_counts",
        "control_progress",
        "unrelated_peers_usable",
        "fatal",
        "recovered",
        "converged",
    ):
        assert token in source
    assert 'compose + ["up", "-d"]' in source
    assert 'compose + ["stop", "-t", "20"]' in source

