import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "antithesis" / "cardano_amaru_relay_bootstrap_control"
COMPOSE = BUNDLE / "docker-compose.yaml"
CONSUMER_TOPOLOGY = BUNDLE / "amaru-consumer-topology.json"

AMARU_IMAGE = (
    "ghcr.io/lambdasistemi/amaru-bootstrap-producer@"
    "sha256:aabaf9e1fc1f58045329e14c1127c5424ba4794855d39bce05e3b426b7025c36"
)
FAULT_CLASSES = "network,kill,pause,stop"


def load_compose():
    assert COMPOSE.is_file(), f"new additive package is missing: {COMPOSE}"
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def service(doc, name):
    services = doc.get("services", {})
    assert name in services, f"required service is missing: {name}"
    return services[name]


def volume_targets(spec):
    targets = set()
    for item in spec.get("volumes", []):
        if isinstance(item, str):
            parts = item.split(":")
            if len(parts) >= 2:
                targets.add(parts[1])
        elif isinstance(item, dict) and item.get("target"):
            targets.add(item["target"])
    return targets


def test_new_control_is_additive_and_has_required_files():
    assert BUNDLE.is_dir(), f"new additive package is missing: {BUNDLE}"
    required = {
        "UPSTREAM-PROVENANCE.md",
        "README.md",
        "docker-compose.yaml",
        "testnet.yaml",
        "relay-topology.json",
        "amaru-consumer-topology.json",
        "tracer-config.yaml",
        "amaru-runtime/era-history.json",
        "amaru-runtime/global-parameters.json",
    }
    missing = sorted(name for name in required if not (BUNDLE / name).is_file())
    assert not missing, f"new package is incomplete: {missing}"


def test_configurator_normalizes_all_pool_genesis_start_times():
    doc = load_compose()
    configurator = service(doc, "configurator")
    assert configurator["entrypoint"] == ["/bin/bash", "-lc"]
    command = configurator["command"]
    if isinstance(command, list):
        command = " ".join(command)
    assert "/configurator.sh" in command
    assert "/configs/1/configs/shelley-genesis.json" in command
    assert "/configs/1/configs/byron-genesis.json" in command
    assert ".systemStart = $$start" in command
    assert ".startTime = $$start" in command


def test_relays_use_exact_self_bootstrapping_artifact_and_contract():
    doc = load_compose()
    expected = {
        "amaru-relay-1": ("p1.example:3001", "p1-state", "a1-state"),
        "amaru-relay-2": ("p2.example:3001", "p2-state", "a2-state"),
    }

    for name, (peer, live_volume, state_volume) in expected.items():
        relay = service(doc, name)
        assert relay["image"] == AMARU_IMAGE
        assert relay["entrypoint"] == "amaru-relay-bootstrap"
        assert relay["restart"] == "always"
        assert relay["container_name"] == f"dwarf-control-{name}"
        assert relay["hostname"] == f"{name}.example"
        assert relay["environment"]["RELAY_NAME"] == name
        assert relay["environment"]["AMARU_PEER"] == peer
        assert relay["environment"]["AMARU_NETWORK"] == "testnet_42"
        assert relay["environment"]["AMARU_LOG"] == "debug", (
            "the local control needs per-relay roll-forward slots as evidence"
        )
        assert volume_targets(relay) >= {
            "/live",
            "/cardano/config",
            "/amaru-runtime",
            "/startup",
            "/srv/amaru",
        }
        mounts = "\n".join(str(item) for item in relay["volumes"])
        assert f"{live_volume}:/live:ro" in mounts
        assert f"{state_volume}:/srv/amaru" in mounts


def test_relays_do_not_wait_on_obsolete_one_shot_bootstrap_service():
    doc = load_compose()
    assert "bootstrap-producer" not in doc["services"]
    for name in ("amaru-relay-1", "amaru-relay-2"):
        relay = service(doc, name)
        dependencies = relay.get("depends_on", {})
        assert "bootstrap-producer" not in dependencies
        assert all(
            not (
                isinstance(value, dict)
                and value.get("condition") == "service_completed_successfully"
            )
            for value in dependencies.values()
        )


def test_consumer_has_only_amaru_upstreams_on_an_isolated_network():
    doc = load_compose()
    consumer = service(doc, "amaru-consumer")
    assert consumer["container_name"] == "dwarf-control-amaru-consumer"
    assert set(consumer["networks"]) == {"amaru-consumer-net"}

    assert CONSUMER_TOPOLOGY.is_file()
    topology = json.loads(CONSUMER_TOPOLOGY.read_text(encoding="utf-8"))
    serialized = json.dumps(topology, sort_keys=True)
    assert "amaru-relay-1.example" in serialized
    assert "amaru-relay-2.example" in serialized
    access_points = topology["localRoots"][0]["accessPoints"]
    assert {peer["port"] for peer in access_points} == {3000}, (
        "current Amaru relays listen on 3000; using the Cardano-node port 3001 "
        "makes the isolated consumer control vacuous"
    )
    for forbidden in (
        "p1.example",
        "p2.example",
        "p3.example",
        "relay1.example",
        "relay2.example",
    ):
        assert forbidden not in serialized

    for name in ("amaru-relay-1", "amaru-relay-2"):
        assert "amaru-consumer-net" in service(doc, name)["networks"]


def test_consumer_is_seeded_from_cardano_db_only_after_amaru_bootstrap():
    doc = load_compose()
    seed = service(doc, "amaru-consumer-seed")
    consumer = service(doc, "amaru-consumer")

    assert seed["image"] == AMARU_IMAGE
    assert seed["entrypoint"] == ["/bin/bash", "-euo", "pipefail", "-c"]
    assert seed["restart"] == "on-failure"
    assert seed["container_name"] == "dwarf-control-amaru-consumer-seed"
    assert not seed.get("networks"), "seed service must not provide a bypass peer path"

    mounts = "\n".join(str(item) for item in seed["volumes"])
    assert "p1-state:/live:ro" in mounts
    assert "a1-state:/amaru-state:ro" in mounts
    assert "amaru-consumer-state:/seed" in mounts

    command = "\n".join(seed["command"] if isinstance(seed["command"], list) else [seed["command"]])
    assert "/amaru-state/.bootstrap-complete" in command
    assert "/live/immutable" in command
    assert "/live/ledger" in command
    assert "/live/volatile" in command
    assert "/seed/.seed-complete" in command

    assert consumer["depends_on"]["amaru-consumer-seed"]["condition"] == (
        "service_completed_successfully"
    )


def test_fault_exclusion_labels_use_moog_token_strings_not_booleans():
    doc = load_compose()
    found = 0
    for name, spec in doc["services"].items():
        labels = spec.get("labels", {})
        if isinstance(labels, list):
            labels = dict(item.split("=", 1) for item in labels)
        if "com.antithesis.exclude_from_faults" not in labels:
            continue
        found += 1
        value = labels["com.antithesis.exclude_from_faults"]
        assert isinstance(value, str), f"{name} has non-string fault exclusion"
        assert value == FAULT_CLASSES, f"{name} has invalid fault classes: {value}"
    assert found, "no infrastructure service has an explicit fault exclusion"


def test_every_runtime_image_is_immutable_and_no_known_bad_image_is_present():
    doc = load_compose()
    for name, spec in doc["services"].items():
        image = spec.get("image")
        assert image, f"{name} has no prebuilt image"
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), (
            f"{name} image is not digest-only immutable: {image}"
        )
        assert ":latest" not in image
        assert "cf657b91" not in image
        assert not re.search(r":[^/@]+@sha256:", image), (
            f"{name} uses tag+digest syntax rejected by Antithesis: {image}"
        )


def test_services_have_explicit_unique_identity_and_no_sensitive_files():
    doc = load_compose()
    names = []
    for name, spec in doc["services"].items():
        assert spec.get("container_name"), f"{name} has no container_name"
        assert spec.get("hostname"), f"{name} has no hostname"
        names.append(spec["container_name"])
    assert len(names) == len(set(names)), "container names are not unique"

    offenders = [
        str(path.relative_to(BUNDLE))
        for path in BUNDLE.rglob("*")
        if path.is_file()
        and (
            path.name.startswith("._")
            or path.name == ".DS_Store"
            or path.suffix.lower() in {".skey", ".pem", ".key"}
        )
    ]
    assert not offenders, offenders


def test_runtime_parameters_match_short_epoch_testnet_contract():
    assert (BUNDLE / "testnet.yaml").is_file()
    assert (BUNDLE / "amaru-runtime" / "era-history.json").is_file()
    assert (BUNDLE / "amaru-runtime" / "global-parameters.json").is_file()

    testnet = {}
    for document in yaml.safe_load_all(
        (BUNDLE / "testnet.yaml").read_text(encoding="utf-8")
    ):
        if document:
            testnet.update(document)
    globals_doc = json.loads(
        (BUNDLE / "amaru-runtime" / "global-parameters.json").read_text(
            encoding="utf-8"
        )
    )
    assert testnet["networkMagic"] == 42
    assert testnet["epochLength"] == 400
    assert globals_doc["consensus_security_param"] == 20
    assert globals_doc["active_slot_coeff_inverse"] == 5
    assert globals_doc["epoch_length_scale_factor"] == 4
