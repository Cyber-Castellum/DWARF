import json
import re
import stat
import types
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "antithesis" / "cardano_amaru_relay_bootstrap_control"
BUNDLE = ROOT / "antithesis" / "cardano_amaru_miniprotocol_security"
COMPOSE = BUNDLE / "docker-compose.yaml"
SP4_IMAGE = (
    "ghcr.io/j-gainsec/dwarf-adversary@"
    "sha256:c5e35065b9a58c337cd770ef0317bf11c1057a5869d654f7873275d3c8fe1aa1"
)
FAULT_CLASSES = "network,kill,pause,stop"
PROTOCOLS = ("chainsync", "blockfetch", "txsubmission", "keepalive")
CLASSES = (
    "WrongAgency",
    "OutOfState",
    "PrematureTerminal",
    "PostTerminal",
    "Flood",
    "Duplicate",
)
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
    "sm-cardano-seed",
    "sm-cardano-victim",
    "sm-amaru-victim",
    "sm-cardano-fuzzer",
    "sm-amaru-fuzzer",
    "sm-workload",
}


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_observer():
    path = BUNDLE / "workload" / "miniprotocol_observer.py"
    module = types.ModuleType("miniprotocol_observer")
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


def command_text(service: dict) -> str:
    command = service.get("command", "")
    return " ".join(command) if isinstance(command, list) else command


def test_new_package_has_the_complete_additive_file_set():
    required = {
        "README.md",
        "UPSTREAM-PROVENANCE.md",
        "docker-compose.yaml",
        "docker-compose.local.yaml",
        "testnet.yaml",
        "relay-topology.json",
        "amaru-consumer-topology.json",
        "sm-cardano-topology.json",
        "tracer-config.yaml",
        "amaru-runtime/era-history.json",
        "amaru-runtime/global-parameters.json",
        "workload/Dockerfile",
        "workload/miniprotocol_observer.py",
        "scratchbook/property-catalog.md",
        "scratchbook/properties/mixed-miniprotocol-containment.md",
    }
    missing = sorted(name for name in required if not (BUNDLE / name).is_file())
    assert not missing, missing


def test_package_keeps_proven_control_assets_byte_identical():
    for name in (
        "testnet.yaml",
        "relay-topology.json",
        "amaru-consumer-topology.json",
        "tracer-config.yaml",
        "amaru-runtime/era-history.json",
        "amaru-runtime/global-parameters.json",
    ):
        assert (BUNDLE / name).read_bytes() == (BASELINE / name).read_bytes(), name


def test_compose_preserves_the_baseline_and_only_adds_named_security_services():
    baseline = load_yaml(BASELINE / "docker-compose.yaml")
    security = load_yaml(COMPOSE)
    assert set(security["services"]) == BASELINE_SERVICES | SECURITY_SERVICES
    for name in BASELINE_SERVICES - {"sidecar"}:
        assert security["services"][name] == baseline["services"][name], name
    base_sidecar = baseline["services"]["sidecar"]
    sidecar = security["services"]["sidecar"]
    for field in (
        "image",
        "container_name",
        "hostname",
        "labels",
        "entrypoint",
        "environment",
        "restart",
        "tmpfs",
    ):
        assert sidecar[field] == base_sidecar[field], field


def test_pair_uses_the_same_pinned_engine_seed_and_single_worker():
    services = load_yaml(COMPOSE)["services"]
    commands = []
    for name, target in (
        ("sm-cardano-fuzzer", "sm-cardano-victim.example:3001"),
        ("sm-amaru-fuzzer", "sm-amaru-victim.example:3000"),
    ):
        service = services[name]
        assert service["image"] == SP4_IMAGE
        text = command_text(service)
        assert "--state-machine-fuzz" in text
        assert "--sm-connections 1" in text
        assert "--seed 0x20260907" in text
        assert f"--upstream {target}" in text
        assert "tee" in text and "/evidence/fuzzer.log" in text
        assert service["labels"]["com.antithesis.exclude_from_faults"] == FAULT_CLASSES
        victim = name.replace("-fuzzer", "-victim")
        assert service["depends_on"][victim]["condition"] == "service_healthy"
        commands.append(text)
    assert commands[0].replace("sm-cardano-victim.example:3001", "TARGET").replace(
        "/cardano-evidence/", "/evidence/"
    ) == commands[1].replace("sm-amaru-victim.example:3000", "TARGET").replace(
        "/amaru-evidence/", "/evidence/"
    )


def test_victims_keep_honest_upstreams_while_accepting_adversarial_sessions():
    services = load_yaml(COMPOSE)["services"]
    topology = load_yaml(BUNDLE / "sm-cardano-topology.json")
    serialized = json.dumps(topology, sort_keys=True)
    assert "p1.example" in serialized
    assert "sm-cardano-fuzzer" not in serialized
    cardano = services["sm-cardano-victim"]
    assert "--port 3001" in command_text(cardano)
    assert "sm-cardano-state:/state" in cardano["volumes"]
    assert cardano["entrypoint"] == ["/bin/bash", "-euo", "pipefail", "-c"]
    assert "/evidence/cardano.log" in command_text(cardano)
    assert "sm-cardano-runtime-evidence:/evidence" in cardano["volumes"]
    assert "/state/node.socket" in " ".join(cardano["healthcheck"]["test"])
    assert "/dev/tcp/127.0.0.1/3001" in " ".join(cardano["healthcheck"]["test"])
    amaru = services["sm-amaru-victim"]
    assert amaru["environment"]["AMARU_PEER"] == "p1.example:3001"
    assert "p1-state:/live:ro" in amaru["volumes"]
    assert "sm-amaru-state:/srv/amaru" in amaru["volumes"]
    assert "/srv/amaru/.bootstrap-complete" in " ".join(amaru["healthcheck"]["test"])
    assert "/dev/tcp/127.0.0.1/3000" in " ".join(amaru["healthcheck"]["test"])


def test_setup_complete_is_blocked_on_real_paired_24_cell_coverage():
    sidecar = load_yaml(COMPOSE)["services"]["sidecar"]
    text = command_text(sidecar)
    mounts = "\n".join(sidecar["volumes"])
    assert "sm-cardano-evidence:/sm-cardano-evidence:ro" in mounts
    assert "sm-amaru-evidence:/sm-amaru-evidence:ro" in mounts
    assert "prefault-ready" in text
    assert "/sm-cardano/node.socket" in text
    assert "/sm-amaru/.bootstrap-complete" in text
    assert text.index("prefault-ready") < text.index("exec /bin/sidecar")


def test_only_oracles_are_fault_excluded_in_the_security_delta():
    services = load_yaml(COMPOSE)["services"]
    expected = {"sm-cardano-fuzzer", "sm-amaru-fuzzer", "sm-workload"}
    for name in SECURITY_SERVICES:
        value = services[name].get("labels", {}).get(
            "com.antithesis.exclude_from_faults"
        )
        assert (value == FAULT_CLASSES) == (name in expected), name


def test_all_runtime_images_are_digest_pinned_and_have_no_build_context():
    for name, service in load_yaml(COMPOSE)["services"].items():
        assert "build" not in service, name
        assert re.search(r"@sha256:[0-9a-f]{64}$", service["image"]), (
            name,
            service["image"],
        )
        assert ":latest" not in service["image"]


def test_test_template_has_bounded_executable_driver_and_recovery_commands():
    command_dir = (
        BUNDLE / "workload" / "test" / "v1" / "mixed-miniprotocol-security"
    )
    names = {
        "parallel_driver_fuzz_observe.py",
        "anytime_containment.py",
        "eventually_recovery.py",
    }
    assert {path.name for path in command_dir.iterdir() if path.is_file()} == names
    for name in names:
        path = command_dir / name
        assert path.read_bytes().startswith(b"#!/usr/bin/env python3\n")
        assert path.stat().st_mode & stat.S_IXUSR


def test_package_contains_no_secrets_or_generated_junk():
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


def test_observer_parses_only_explicit_injection_records():
    observer = load_observer()
    text = """
seed: using explicit seed 0x20260907
dwarf-adversary: state-machine-init[chainsync]: dialing; injecting departure=WrongAgency legalPrefix=2 frames=3
configuration mentions state-machine-init[keepalive] but is not an injection
dwarf-adversary: state-machine-init[blockfetch]: dialing; injecting departure=Flood legalPrefix=1 frames=9
"""
    parsed = observer.parse_fuzzer_log(text)
    assert parsed["seed"] == "0x20260907"
    assert parsed["injections"] == [
        {
            "protocol": "chainsync",
            "class": "WrongAgency",
            "legal_prefix": 2,
            "frames": 3,
        },
        {
            "protocol": "blockfetch",
            "class": "Flood",
            "legal_prefix": 1,
            "frames": 9,
        },
    ]


def test_prefault_pairing_fails_closed_on_any_common_prefix_mismatch():
    observer = load_observer()
    left = [
        {"protocol": "chainsync", "class": "WrongAgency", "legal_prefix": 1, "frames": 2},
        {"protocol": "blockfetch", "class": "Flood", "legal_prefix": 0, "frames": 8},
    ]
    right = [dict(item) for item in left]
    right[1]["class"] = "Duplicate"
    result = observer.compare_prefault_streams(left, right)
    assert result["matched"] is False
    assert result["first_mismatch_index"] == 1
    assert result["complete_coverage"] is False


def test_prefault_pairing_requires_all_24_protocol_class_cells():
    observer = load_observer()
    stream = [
        {"protocol": protocol, "class": departure, "legal_prefix": 0, "frames": 1}
        for protocol in PROTOCOLS
        for departure in CLASSES
    ]
    incomplete = observer.compare_prefault_streams(stream[:-1], stream[:-1])
    assert incomplete["matched"] is True
    assert incomplete["complete_coverage"] is False
    assert incomplete["missing_cells"] == [["keepalive", "Duplicate"]]
    complete = observer.compare_prefault_streams(stream, list(stream))
    assert complete["matched"] is True
    assert complete["complete_coverage"] is True
    assert complete["common_length"] == 24


def test_known_amaru_listener_failure_is_background_not_a_new_finding():
    observer = load_observer()
    signals = observer.classify_fatal_signals(
        "panicked at listener bind: Address already in use (os error 98) EADDRINUSE"
    )
    assert signals["fatal"] is True
    assert signals["known_background"] == ["amaru_listener_eaddrinuse"]
    assert signals["novel"] == []


def test_cardano_panic_is_a_fatal_novel_signal():
    observer = load_observer()
    signals = observer.classify_fatal_signals("cardano-node: panicked at unexpected invariant")
    assert signals["fatal"] is True
    assert signals["known_background"] == []
    assert signals["novel"] == ["panic"]


def test_runtime_evaluation_is_non_vacuous_and_requires_recovery():
    observer = load_observer()
    cells = [[protocol, departure] for protocol in PROTOCOLS for departure in CLASSES]
    result = observer.evaluate_runtime(
        prefault={"matched": True, "complete_coverage": True, "common_length": 24},
        cardano_cells=cells,
        amaru_cells=cells,
        post_setup_counts={"cardano": 48, "amaru": 44},
        targets_reachable={"cardano": True, "amaru": True},
        control_progress=True,
        unrelated_peers_usable=True,
        fatal={"cardano": False, "amaru": False},
        recovered={"cardano": True, "amaru": True, "consumer": True},
        converged=True,
    )
    assert result["classifiable"] is True
    assert result["safe"] is True
    result["inputs"]["post_setup_counts"]["amaru"] = 0
    assert observer.evaluate_runtime(**result["inputs"])["classifiable"] is False
