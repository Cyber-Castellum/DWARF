"""Scenario -> native Antithesis test generator (cardano-node).

Pure, stdlib-only (string-built YAML/JSON, matching the rest of the package).
Turns a validated DWARF scenario into a native Antithesis bundle and statically
verifies it (Stage-2 gate) before any Moog submission. cardano-node only;
amaru/differential are refused (SP3).

Native conventions honored (see profile_manager.antithesis_conventions):
  - SDK assertions: Sometimes/Reachable only (harness can chaos-kill the
    workload, so Always would false-fail).
  - antithesis_random seeds the adversary --seed at launch (reproducible).
  - composer scripts under /opt/antithesis/test/v1/; parallel_driver_ emits no
    setup_complete.
  - com.antithesis.exclude_from_faults on the rig (not the node under test).
  - public, registry-pinned, hermetic images (no build: contexts).

The "overlap" with the local-devnet backend is honest: both attack the SAME
target decoder under the SAME asserted property from the SAME seed source. The
mutation ENGINES differ by design -- local cbor_fuzz_structured is byte-level
mutate_cbor; the native dwarf-adversary is Term-level structural mutateTerm.
"""
import json
import re
from pathlib import Path

from profile_manager import antithesis_conventions as conv
from profile_manager.backends.base import BackendArtifacts, write_artifacts

SUPPORTED_IMPLEMENTATIONS = {"cardano-node"}
ADVERSARY_IMAGE = "ghcr.io/j-gainsec/dwarf-adversary:0.9.0"

# Version-controlled testnet base assets the generator overlays.
_ASSETS = Path(__file__).resolve().parent / "antithesis_assets"
# The proven hand-built native test, used as the compose base to merge.
ARCHETYPE_COMPOSE = (Path(__file__).resolve().parents[2]
                     / "antithesis" / "cardano_node_dwarf" / "docker-compose.yaml")

NETWORK_MAGIC = 42                       # matches testnet.yaml networkMagic
ADVERSARY_LISTEN_PORT = 3001
ADVERSARY_UPSTREAM = "p1.example:3001"   # in-bundle base-header source
SEED_LAUNCH_PLACEHOLDER = "0x1"          # overwritten by antithesis_random at launch

# Which adversary protocol + CBOR shape a decode target maps to, and whether the
# adversary mode that serves it is built. Only the chain-sync block-header mode
# exists today (Phase 3b). The rest are additive follow-on builds; mapped here so
# the generator errors clearly instead of emitting a non-fuzzing bundle.
ADVERSARY_MODES = {
    "cardano-node-cbor-decode-block-header": {"protocol": "chainsync", "shape": "block-header", "built": True},
    "cardano-node-cbor-decode-block":         {"protocol": "blockfetch", "shape": "block", "built": True},
    "cardano-node-cbor-decode-tx-body":       {"protocol": "txsubmission", "shape": "tx-body", "built": True},
    "cardano-node-cbor-decode-certificate":   {"protocol": "txsubmission", "shape": "certificate", "built": True},
    "cardano-node-cbor-decode-auxiliary-data":{"protocol": "txsubmission", "shape": "auxiliary-data", "built": True},
}

# DWARF assertion primitive -> native SDK catalog entries. Sometimes/Reachable
# only: the harness can chaos-kill the workload, so Always would false-fail.
_ASSERTION_MAP = {
    "parse_succeeds_or_clean_error": [
        {"id": "decoder_reached", "kind": "reachable",
         "message": "node header decoder ran on an adversarial header"},
        {"id": "clean_rejection", "kind": "sometimes",
         "message": "node cleanly rejected a structurally-mutated header"},
    ],
    "roundtrip_equals_original": [
        {"id": "roundtrip_observed", "kind": "sometimes",
         "message": "an unmutated header round-tripped through the node decoder"},
    ],
    "parser_exit_status": [
        {"id": "parser_exit_observed", "kind": "reachable",
         "message": "parser exit status was observed"},
    ],
}


class GeneratorError(Exception):
    """Raised when a scenario cannot be turned into a native Antithesis test."""


def _cbor_load(scenario):
    """Return the single cbor-fuzz load PrimitiveRef, or raise."""
    cbor = [p for p in scenario.load if str(p.primitive).startswith("cbor_fuzz")]
    if len(cbor) != 1:
        raise GeneratorError(
            f"expected exactly one cbor_fuzz load primitive, found {len(cbor)}"
        )
    return cbor[0]


def fuzz_spec(scenario):
    """Shared descriptor consumed by both backends: same target decoder, CBOR
    shape, seed, and asserted properties. NOT a shared mutation engine -- local
    is byte-level mutate_cbor; the adversary is Term-level structural mutateTerm.
    """
    if scenario.target.get("implementation") not in SUPPORTED_IMPLEMENTATIONS:
        raise GeneratorError(
            f"target.implementation {scenario.target.get('implementation')!r} is not "
            "supported by Antithesis (amaru/differential = SP3)"
        )
    load = _cbor_load(scenario)
    return {
        "target_decoder": load.params["target_id"],
        "cbor_shape": load.params.get("shape"),
        "seed": scenario.seed,
        "mutation_rate": float(load.params.get("mutation_rate", 0.05)),
        "asserted_properties": [a.primitive for a in scenario.assertions],
    }


def map_assertions(scenario):
    """DWARF assertions -> native SDK catalog (Sometimes/Reachable). Zero = error."""
    catalog = []
    seen = set()
    for a in scenario.assertions:
        prim = a.primitive
        for entry in _ASSERTION_MAP.get(prim, []):
            if entry["id"] in seen:
                continue
            seen.add(entry["id"])
            catalog.append(dict(entry))
    if not catalog:
        raise GeneratorError(
            "scenario maps to zero SDK assertions -- refusing to generate a "
            "test that asserts nothing (anti-false-green)"
        )
    return catalog


def _fmt_rate(x):
    """Format a mutation rate as a compact decimal string (0.05 -> '0.05')."""
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return s if s else "0"


def derive_adversary(scenario):
    """Map scenario.target + the cbor load primitive -> the dwarf-adversary
    service. Refuses unsupported implementations and unbuilt adversary modes."""
    fs = fuzz_spec(scenario)                       # also validates implementation
    decoder = fs["target_decoder"]
    mode = ADVERSARY_MODES.get(decoder)
    if mode is None:
        raise GeneratorError(f"no adversary mapping for decoder {decoder!r}")
    if not mode["built"]:
        raise GeneratorError(
            f"decoder {decoder!r} needs the {mode['protocol']!r} adversary mode "
            "(additive follow-on build); only the chainsync block-header mode is built"
        )
    return {
        "image": ADVERSARY_IMAGE,
        "protocol": mode["protocol"],
        "shape": mode["shape"],
        "command_args": [
            "--network-magic", str(NETWORK_MAGIC),
            "--listen-port", str(ADVERSARY_LISTEN_PORT),
            "--mutation-rate", _fmt_rate(fs["mutation_rate"]),
            "--upstream", ADVERSARY_UPSTREAM,
            "--seed", SEED_LAUNCH_PLACEHOLDER,
            "--protocol", mode["protocol"],
            "--cbor-shape", mode["shape"],
        ],
    }


def select_testnet_base(scenario):
    """Return the directory of version-controlled testnet base assets to overlay."""
    if scenario.target.get("implementation") not in SUPPORTED_IMPLEMENTATIONS:
        raise GeneratorError("amaru/differential testnet base is SP3")
    return _ASSETS


# Harness/infra services that must be excluded from fault injection.
_FAULT_LABEL = 'com.antithesis.exclude_from_faults: "network,kill,pause,stop"'

# Hand-baked workloads in the archetype that are NOT part of a header-fuzz test.
# The proven node set (configurator, tracer, p1/p2/p3, relay1/relay2,
# tracer-sidecar, log-tailer) is kept for chain liveness; dwarf-adversary is
# dropped here and replaced by the scenario-derived block.
_DROP_SERVICES = {"dwarf-adversary", "tx-generator", "sidecar", "adversary", "asteria-game"}


def _render_adversary_service(adv, eclipse=False):
    """Render the dwarf-adversary compose service block as YAML text lines.

    When @eclipse is set (block-fetch / consensus scenarios), the adversary
    bridges BOTH networks: the default testnet net (to chain-sync its upstream
    p1) and the isolated `eclipse` net (the only net the node under test is on,
    so the adversary is its sole peer)."""
    lines = [
        "  dwarf-adversary:",
        f"    image: {adv['image']}",
        "    container_name: dwarf-adversary",
        "    hostname: dwarf-adversary.example",
        "    labels:",
        f"      {_FAULT_LABEL}",
        "    command:",
    ]
    for tok in adv["command_args"]:
        lines.append(f'      - "{tok}"')
    lines += [
        "    depends_on:",
        "      configurator:",
        "        condition: service_completed_successfully",
        "      p1:",
        "        condition: service_started",
        "    restart: always",
    ]
    if eclipse:
        lines += [
            "    networks:",
            "      default: {}",
            "      eclipse:",
            "        aliases:",
            "          - dwarf-adversary.example",
        ]
    return lines


def _merge_compose(base_text, adversary_lines):
    """Drop the archetype's hand-baked workloads and splice in our adversary
    block before the top-level `volumes:`."""
    out, lines = [], base_text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        # a top-level service header is "  <name>:" at exactly 2-space indent
        if (line.startswith("  ") and not line.startswith("   ")
                and line.rstrip().endswith(":")):
            name = line.strip().rstrip(":")
            if name in _DROP_SERVICES:
                i += 1
                while i < n and (lines[i].startswith("    ") or lines[i].strip() == ""):
                    i += 1
                continue
        if line.rstrip() == "volumes:":          # splice adversary before volumes
            out.extend(adversary_lines)
        out.append(line)
        i += 1
    return "\n".join(out) + "\n"


def _apply_eclipse(compose_text):
    """Rewrite the merged compose so the node under test (relay2) is ECLIPSED:
    it lives solely on an isolated `eclipse` network and so can reach ONLY the
    dwarf-adversary (topology alone can't eclipse — ledger peers + the producer
    mesh's inbound dials leak the real producers). Used for block-fetch /
    consensus scenarios where the node must fetch from the adversary. relay2's
    topology is swapped to the adversary-only `relay-eclipse-topology.json`."""
    t = compose_text
    # relay2: attach ONLY to the eclipse net (service-level networks stanza).
    marker = "    hostname: relay2.example\n"
    if marker not in t:
        raise ValueError("eclipse: relay2 hostname anchor not found in compose")
    t = t.replace(marker, marker + "    networks:\n      - eclipse\n", 1)
    # relay2: use the adversary-only / no-ledger-peers topology.
    swap = "./relay-dwarf-topology.json:/configs/configs/topology.json:ro"
    if swap not in t:
        raise ValueError("eclipse: relay2 topology mount not found in compose")
    t = t.replace(swap, "./relay-eclipse-topology.json:/configs/configs/topology.json:ro", 1)
    # define `eclipse` as a sibling of the default network.
    netmarker = "    internal: ${INTERNAL_NETWORK}\n"
    if netmarker not in t:
        raise ValueError("eclipse: top-level default network anchor not found in compose")
    t = t.replace(netmarker, netmarker + "  eclipse:\n    driver: bridge\n", 1)
    return t


def _render_composer(scenario, catalog):
    """Render /opt/antithesis/test/v1 driver scripts. parallel_driver_ must NOT
    emit setup_complete (that is the testnet setup step's job)."""
    asserts = "\n".join(
        f'echo \'{{"antithesis_assert":{{"id":"{a["id"]}","condition":true,'
        f'"message":"{a["message"]}"}}}}\' >> "$OUT"'
        for a in catalog
    )
    driver = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'OUT="${ANTITHESIS_OUTPUT_DIR:-/tmp}/sdk.jsonl"\n'
        "# The adversary is already serving mutated headers to the node; each\n"
        "# tick we record that the asserted properties remain reachable.\n"
        f"{asserts}\n"
    )
    return {f"{conv.TEST_DIR}/v1/parallel_driver_fuzz.sh": driver}


def _render_manifest(scenario, adv, catalog, fs):
    return json.dumps({
        "scenario_id": scenario.id,
        "target": dict(scenario.target),
        "seed_policy": "antithesis_random -> --seed (reproducible)",
        "fuzz_spec": fs,
        "adversary": {"image": adv["image"], "protocol": adv["protocol"], "shape": adv["shape"]},
        "assertions": catalog,
    }, indent=2) + "\n"


def _render_readme(scenario):
    return (
        f"# Native Antithesis test: {scenario.id}\n\n"
        "Generated by Dwarf (`cardano-profile scenario run --backend antithesis`).\n"
        "cardano-node testnet + dwarf-adversary chain-sync header fuzzer.\n\n"
        "No secrets here. Do not commit wallets, PATs, or credentials.\n"
    )


def render_bundle(scenario, *, registry, tag="latest"):
    """Assemble the native Antithesis bundle artifacts for a scenario."""
    fs = fuzz_spec(scenario)
    adv = derive_adversary(scenario)
    catalog = map_assertions(scenario)
    base = select_testnet_base(scenario)

    # Block-fetch (and, later, consensus) scenarios need the node to fetch from
    # the adversary, which only happens under ECLIPSE (sole peer). tx-submission
    # / chain-sync decode-on-receipt scenarios use the dual-peer topology (node
    # stays CaughtUp via the real producers while still talking to the adversary).
    eclipse = adv["protocol"] == "blockfetch"
    compose_text = _merge_compose(
        ARCHETYPE_COMPOSE.read_text(), _render_adversary_service(adv, eclipse=eclipse)
    )
    if eclipse:
        compose_text = _apply_eclipse(compose_text)
    files = {
        conv.COMPOSE_RELPATH: compose_text,
        "testnet.yaml": (base / "testnet.yaml").read_text(),
        "tracer-config.yaml": (base / "tracer-config.yaml").read_text(),
        "dwarf-manifest.json": _render_manifest(scenario, adv, catalog, fs),
        "README.md": _render_readme(scenario),
    }
    if eclipse:
        files["relay-eclipse-topology.json"] = (base / "relay-eclipse-topology.json").read_text()
    else:
        files["relay-dwarf-topology.json"] = (base / "relay-dwarf-topology.json").read_text()
    files.update(_render_composer(scenario, catalog))
    return BackendArtifacts(
        backend="antithesis", files=files,
        summary={"scenario": scenario.id, "registry": registry, "tag": tag},
    )


def verify_generated_bundle(bundle_dir):
    """Static Stage-2 gate. Returns {"state": "pass"|"fail", "reasons": [...]}.
    Mirrors Stage-1: refuse anything that would look green but not actually fuzz."""
    bundle = Path(bundle_dir)
    reasons = []
    compose_p = bundle / conv.COMPOSE_RELPATH
    if not compose_p.exists():
        return {"state": "fail", "reasons": ["missing config/docker-compose.yaml"]}
    compose = compose_p.read_text()

    # hermetic: registry images only, no build contexts
    if re.search(r"^\s*build:", compose, re.MULTILINE):
        reasons.append("compose has a build: context (must use registry images)")
    # adversary present + fault-excluded
    if ADVERSARY_IMAGE not in compose:
        reasons.append("adversary image not referenced in compose")
    if "com.antithesis.exclude_from_faults" not in compose:
        reasons.append("no exclude_from_faults label on harness services")
    # topology resolves: adversary reachable as a relay root. Eclipse bundles
    # (block-fetch) ship relay-eclipse-topology.json (node's sole peer = the
    # adversary, on an isolated network); others ship relay-dwarf-topology.json
    # (dual-peer). Exactly one must be present and must list the adversary.
    eclipse_topo = bundle / "relay-eclipse-topology.json"
    dual_topo = bundle / "relay-dwarf-topology.json"
    topo = eclipse_topo if eclipse_topo.exists() else dual_topo
    if not topo.exists():
        reasons.append("missing relay topology (relay-dwarf-topology.json or relay-eclipse-topology.json)")
    elif "dwarf-adversary" not in topo.read_text():
        reasons.append("topology does not list dwarf-adversary as a root")
    # eclipse bundle must define the isolated network + put relay2 on it only
    if eclipse_topo.exists():
        if "eclipse:" not in compose:
            reasons.append("eclipse topology present but compose has no eclipse network")
        if "relay-eclipse-topology.json" not in compose:
            reasons.append("eclipse topology present but relay2 does not mount it")
    # manifest + at least one assertion
    man_p = bundle / "dwarf-manifest.json"
    if not man_p.exists():
        reasons.append("missing dwarf-manifest.json")
    else:
        man = json.loads(man_p.read_text())
        if not man.get("assertions"):
            reasons.append("manifest declares zero assertions")
    # composer: drivers exist, non-empty; parallel_driver emits no setup_complete
    drivers = list(bundle.glob("test/**/parallel_driver_*.sh"))
    if not drivers:
        reasons.append("no parallel_driver_ composer script")
    for d in drivers:
        body = d.read_text()
        if not body.strip():
            reasons.append(f"empty composer script {d.name}")
        if "antithesis_setup" in body or "setup_complete" in body:
            reasons.append(f"parallel_driver {d.name} emits setup_complete (forbidden)")
    return {"state": "fail" if reasons else "pass", "reasons": reasons}


def generate_native_test(scenario_path, *, out_dir, registry, tag="latest",
                         registry_path=None):
    """Load + validate the scenario, render the native bundle, write it, and run
    the Stage-2 gate. Returns {bundle_dir, files, verify}."""
    from profile_manager import scenario as _scn
    # semantic validation first (refuses unregistered primitives by name)
    _scn.semantic_validate_scenario(scenario_path, registry_path=registry_path)
    scenario = _scn.load_scenario(scenario_path)
    arts = render_bundle(scenario, registry=registry, tag=tag)
    written = write_artifacts(arts, out_dir)
    verify = verify_generated_bundle(out_dir)
    return {"bundle_dir": out_dir, "files": written, "verify": verify}
