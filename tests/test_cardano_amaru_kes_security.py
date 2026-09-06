import json
import re
import stat
import types
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "antithesis" / "cardano_amaru_relay_bootstrap_control"
BUNDLE = ROOT / "antithesis" / "cardano_amaru_kes_security"
COMPOSE = BUNDLE / "docker-compose.yaml"
FAULT_CLASSES = "network,kill,pause,stop"
BASELINE_SERVICES = {
    "configurator",
    "tracer",
    "tracer-sidecar",
    "p1",
    "p2",
    "p3",
    "relay1",
    "relay2",
    "amaru-relay-1",
    "amaru-relay-2",
    "amaru-consumer-seed",
    "amaru-consumer",
    "sidecar",
    "log-tailer",
}
SECURITY_SERVICES = {
    "kes-cardano-seed",
    "kes-cardano-proxy",
    "kes-amaru-proxy",
    "kes-cardano-victim",
    "kes-amaru-victim",
    "kes-workload",
}


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_observer():
    path = BUNDLE / "workload" / "kes_observer.py"
    module = types.ModuleType("kes_observer")
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


def test_security_package_keeps_baseline_assets_byte_identical():
    for name in (
        "testnet.yaml",
        "relay-topology.json",
        "amaru-consumer-topology.json",
        "tracer-config.yaml",
        "amaru-runtime/era-history.json",
        "amaru-runtime/global-parameters.json",
    ):
        assert (BUNDLE / name).read_bytes() == (BASELINE / name).read_bytes(), name


def test_security_compose_preserves_baseline_service_contract():
    baseline = load_yaml(BASELINE / "docker-compose.yaml")
    security = load_yaml(COMPOSE)
    assert set(security["services"]) == BASELINE_SERVICES | SECURITY_SERVICES
    for name in BASELINE_SERVICES - {"sidecar"}:
        assert security["services"][name] == baseline["services"][name], name
    base_sidecar = baseline["services"]["sidecar"]
    security_sidecar = security["services"]["sidecar"]
    for field in ("image", "container_name", "hostname", "labels", "entrypoint", "environment", "restart", "tmpfs"):
        assert security_sidecar[field] == base_sidecar[field], field


def test_setup_complete_is_delayed_until_security_path_is_real():
    sidecar = load_yaml(COMPOSE)["services"]["sidecar"]
    command = "\n".join(sidecar["command"])
    assert "/kes-cardano/node.socket" in command
    assert "/kes-amaru/.bootstrap-complete" in command
    assert command.count('"kind":"honest_header"') == 2
    assert command.count('"kind":"kes_mutation"') == 2
    assert "invalid[ _]?kes[ _]?signature" in command
    assert command.index('"kind":"kes_mutation"') < command.index("exec /bin/sidecar")


def test_security_delta_has_two_dedicated_proxy_victim_paths():
    doc = load_yaml(COMPOSE)
    services = doc["services"]
    topology = load_yaml(BUNDLE / "kes-cardano-topology.json")
    assert topology["localRoots"][0]["accessPoints"] == [
        {"address": "kes-cardano-proxy.example", "port": 3001}
    ]
    amaru = services["kes-amaru-victim"]
    assert amaru["environment"]["AMARU_PEER"] == "kes-amaru-proxy.example:3001"
    assert "p1-state:/live:ro" in amaru["volumes"]
    assert "kes-amaru-state:/srv/amaru" in amaru["volumes"]
    assert amaru["depends_on"]["kes-cardano-seed"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["kes-cardano-victim"]["depends_on"]["kes-cardano-seed"]["condition"] == (
        "service_completed_successfully"
    )
    cardano_victim = services["kes-cardano-victim"]
    assert "./kes-cardano-topology.json:/configs/configs/topology.json:ro" in (
        cardano_victim["volumes"]
    )
    assert "/configs/configs/kes-topology.json" not in cardano_victim["command"]
    for name in ("kes-cardano-proxy", "kes-amaru-proxy"):
        proxy = services[name]
        command = " ".join(proxy.get("command", [])) if isinstance(proxy.get("command"), list) else proxy.get("command", "")
        assert "live-proxy" in command
        assert "--upstream" in command and "p1.example:3001" in command
        assert "--seed" in command
        assert "--min-slot" in command and "1800" in command
        assert "com.antithesis.exclude_from_faults" not in proxy.get("labels", {})


def test_cardano_seed_uses_only_tools_present_in_the_pinned_node_image():
    service = load_yaml(COMPOSE)["services"]["kes-cardano-seed"]
    command = "\n".join(service["command"])
    assert "find " not in command
    assert "for entry in /seed/*" in command
    assert 'rm -rf "$${entry}"' in command


def test_only_oracle_in_security_delta_is_fault_excluded():
    services = load_yaml(COMPOSE)["services"]
    for name in SECURITY_SERVICES:
        labels = services[name].get("labels", {})
        value = labels.get("com.antithesis.exclude_from_faults")
        if name == "kes-workload":
            assert value == FAULT_CLASSES
        else:
            assert value is None, f"{name} must remain faultable"


def test_antithesis_compose_uses_only_digest_images_and_no_build_contexts():
    services = load_yaml(COMPOSE)["services"]
    expected = {
        "kes-cardano-proxy": "ghcr.io/j-gainsec/dwarf-kes-proxy@sha256:d5a27a13c871cffcb5cc0b5ede02e18bc69cd49b96b617609162bcef76724f68",
        "kes-amaru-proxy": "ghcr.io/j-gainsec/dwarf-kes-proxy@sha256:d5a27a13c871cffcb5cc0b5ede02e18bc69cd49b96b617609162bcef76724f68",
        "kes-workload": "ghcr.io/j-gainsec/dwarf-kes-workload@sha256:03b1c345e2728f39a28a6ffae0c41a66a6f1bfe69dca05c5cea91fa22a7acf88",
    }
    for name, service in services.items():
        assert "build" not in service, name
        if name in expected:
            assert service["image"] == expected[name]
        assert re.search(r"@sha256:[0-9a-f]{64}$", service["image"]), (
            name,
            service["image"],
        )
        assert ":latest" not in service["image"]


def test_test_template_commands_are_discoverable_and_executable():
    command_dir = BUNDLE / "workload" / "test" / "v1" / "mixed-kes-security"
    names = {
        "parallel_driver_observe_kes.py",
        "anytime_kes_non_adoption.py",
        "eventually_kes_recovery.py",
    }
    assert {path.name for path in command_dir.iterdir() if path.is_file()} == names
    for name in names:
        path = command_dir / name
        assert path.read_bytes().startswith(b"#!/usr/bin/env python3\n")
        assert path.stat().st_mode & stat.S_IXUSR


def test_package_contains_no_sensitive_or_generated_junk():
    forbidden_names = {".env", ".DS_Store", "genesis.1.skey", "utxo-keys"}
    offenders = []
    for path in BUNDLE.rglob("*"):
        if not path.is_file():
            continue
        if (
            path.name in forbidden_names
            or path.name.startswith("._")
            or path.suffix.lower() in {".skey", ".pem", ".key", ".pyc"}
            or "__pycache__" in path.parts
        ):
            offenders.append(str(path.relative_to(BUNDLE)))
    assert not offenders, offenders


def test_observer_pairs_only_identical_concrete_mutations():
    observer = load_observer()
    event = {
        "kind": "kes_mutation",
        "source_slot": 100,
        "source_hash": "aa" * 32,
        "mutated_hash": "bb" * 32,
        "signature_offset": 447,
        "bit": 7,
        "seed": 9,
    }
    left = observer.parse_proxy_events(json.dumps(event) + "\n")
    right = observer.parse_proxy_events(json.dumps(event) + "\n")
    assert observer.latest_paired_mutation(left, right) == event
    right[0]["bit"] = 6
    assert observer.latest_paired_mutation(left, right) is None


def test_observer_does_not_treat_unavailability_as_rejection():
    observer = load_observer()
    result = observer.evaluate_observation(
        mutation={"mutated_hash": "bb" * 32, "source_slot": 100},
        cardano_tip={"status": "unavailable"},
        amaru={"status": "unavailable", "invalid_kes_hashes": set()},
    )
    assert result["classifiable"] is False
    assert result["safe"] is None


def test_observer_requires_explicit_amaru_kes_classification():
    observer = load_observer()
    mutation = {"mutated_hash": "bb" * 32, "source_slot": 100}
    cardano_tip = {"status": "ok", "slot": 99, "hash": "aa" * 32}
    ambiguous = observer.evaluate_observation(
        mutation=mutation,
        cardano_tip=cardano_tip,
        amaru={"status": "ok", "adopted_hashes": set(), "invalid_kes_hashes": set()},
    )
    assert ambiguous["classifiable"] is False
    explicit = observer.evaluate_observation(
        mutation=mutation,
        cardano_tip=cardano_tip,
        amaru={"status": "ok", "adopted_hashes": set(), "invalid_kes_hashes": {"bb" * 32}},
    )
    assert explicit["classifiable"] is True
    assert explicit["safe"] is True
