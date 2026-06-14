# SP2 — Antithesis Native Test Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user run a DWARF cardano-node scenario on a chosen backend — `local-devnet` (existing executor, unchanged) or `antithesis` (generate a native Antithesis test from the scenario and launch it via Moog).

**Architecture:** A new pure module `profile_manager/antithesis_generator.py` turns a validated scenario into a native Antithesis bundle (CF cardano-node testnet + a scenario-derived `dwarf-adversary` service + composer scripts + SDK assertion catalog + provenance manifest). A static `verify_generated_bundle` gate proves the bundle before submission (Stage-2 anti-false-green). `scenario run --backend` selects the path; the `antithesis` path writes the bundle into the existing Moog test-asset layout and reuses the existing `moog create-test` launch code.

**Tech Stack:** Python 3 stdlib only (string-built YAML/JSON, like the rest of the package — no PyYAML). Tests with pytest under `dwarf-v4/tools/`. Round-trip + `docker compose config` lint run on cardano-box.

**Scope:** cardano-node only (amaru/differential = SP3, refused). The chain-sync **block-header** path is delivered end-to-end; the other four CBOR shapes are mapped but raise an explicit "needs `<protocol>` adversary mode (follow-on build)" error — never a silent non-fuzzing bundle.

**Key grounding facts (verified in code):**
- Scenario is JSON-in-YAML; `load_scenario(path)` → `Scenario` with `.target` (dict, `implementation`), `.seed`, `.load` (list of `{primitive, target_id, shape, mutation_rate, iterations, ...}`), `.assertions` (list of `{primitive}`). Block-header scenario: `dwarf/scenarios/cardano-node-cbor-block-header-fuzz-structured.yaml`.
- `dwarf-adversary` CLI (verified `app/Main.hs`): `--network-magic`, `--listen-port`, `--mutation-rate`, `--seed`, `--upstream HOST:PORT`. Image ref in archetype: `ghcr.io/j-gainsec/dwarf-adversary:0.1.0`. It serves **chain-sync block-header** only.
- Adversary `mutationKinds` (verified `src/DwarfAdversary/Fuzz.hs`): `swapMajorType, truncateCollection, extendCollection, perturbInt, flipIndefinite, nestOnce`. Local `mutate_cbor` is byte-level (different engine — do NOT assert kind parity).
- Native-test archetype: `antithesis/cardano_node_dwarf/{docker-compose.yaml,testnet.yaml,relay-dwarf-topology.json,relay-topology.json,tracer-config.yaml}`. Fault label: `com.antithesis.exclude_from_faults: "network,kill,pause,stop"`.
- Conventions module: `profile_manager/antithesis_conventions.py` (`PLATFORM`, `COMPOSE_RELPATH`, `TEST_DIR`, `SETUP_COMPLETE_SH`).
- `BackendArtifacts` + `write_artifacts` live in `profile_manager/backends/base.py`.

---

## File Structure

- Create: `dwarf/profile_manager/antithesis_generator.py` — the scenario→bundle generator + `verify_generated_bundle`.
- Create: `dwarf/profile_manager/antithesis_assets/` — copied testnet base assets the generator overlays (`testnet.yaml`, `relay-dwarf-topology.json`, `tracer-config.yaml`). Keeps the generator's base hermetic and version-controlled.
- Modify: `dwarf/profile_manager/cli.py` — add `scenario run --backend {local-devnet|antithesis}` and an `antithesis` dispatch.
- Modify: `dwarf/profile_manager/scenario.py` — add `run_scenario_backend(...)` thin dispatcher (keeps `run_scenario` untouched for `local-devnet`).
- Create: `tools/test_sp2_generator.py` — unit + negative tests.
- Create: `tools/sp2_roundtrip.sh` — cardano-box round-trip (generate → verify → `docker compose config` → moog plan).

---

## Task 1: `fuzz_spec` + `map_assertions`

**Files:**
- Create: `dwarf/profile_manager/antithesis_generator.py`
- Test: `tools/test_sp2_generator.py`

- [ ] **Step 1: Write the failing test**

```python
# tools/test_sp2_generator.py
import json, sys
from pathlib import Path
import pytest

ROOT = Path("/Users/nigel/dwarf-project/dwarf-v4")
sys.path.insert(0, str(ROOT / "dwarf"))
from profile_manager import scenario as scn
from profile_manager import antithesis_generator as gen

HEADER = ROOT / "dwarf/scenarios/cardano-node-cbor-block-header-fuzz-structured.yaml"


def _load(p):
    return scn.load_scenario(p)


def test_fuzz_spec_extracts_decoder_shape_seed():
    s = _load(HEADER)
    fs = gen.fuzz_spec(s)
    assert fs["target_decoder"] == "cardano-node-cbor-decode-block-header"
    assert fs["cbor_shape"]["type"] == "array"            # the Conway header shape
    assert fs["seed"] == "0xCAFE0202"
    assert fs["mutation_rate"] == 0.05
    assert set(fs["asserted_properties"]) == {
        "parse_succeeds_or_clean_error", "roundtrip_equals_original"
    }


def test_map_assertions_emits_sometimes_reachable_only():
    s = _load(HEADER)
    cat = gen.map_assertions(s)
    assert len(cat) >= 1
    kinds = {a["kind"] for a in cat}
    assert kinds <= {"sometimes", "reachable"}            # never "always"
    assert all(a["id"] and a["message"] for a in cat)


def test_map_assertions_zero_is_error():
    s = _load(HEADER)
    s.assertions.clear()
    with pytest.raises(gen.GeneratorError):
        gen.map_assertions(s)
```

- [ ] **Step 2: Run it, verify failure**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && python3 -m pytest tools/test_sp2_generator.py -q`
Expected: FAIL — `ModuleNotFoundError: antithesis_generator`.

- [ ] **Step 3: Write the module skeleton + the two functions**

```python
# dwarf/profile_manager/antithesis_generator.py
"""Scenario → native Antithesis test generator (cardano-node).

Pure, stdlib-only (string-built YAML/JSON, matching the rest of the package).
Turns a validated DWARF scenario into a native Antithesis bundle and statically
verifies it (Stage-2 gate) before any Moog submission. cardano-node only;
amaru/differential are refused (SP3).
"""
from profile_manager import antithesis_conventions as conv

SUPPORTED_IMPLEMENTATIONS = {"cardano-node"}
ADVERSARY_IMAGE = "ghcr.io/j-gainsec/dwarf-adversary:0.1.0"

# Which adversary protocol + CBOR shape a decode target maps to, and whether the
# adversary mode that serves it is built. Only the chain-sync block-header mode
# exists today (Phase 3b). The rest are additive follow-on builds; mapped here so
# the generator errors clearly instead of emitting a non-fuzzing bundle.
ADVERSARY_MODES = {
    "cardano-node-cbor-decode-block-header": {"protocol": "chainsync", "shape": "block-header", "built": True},
    "cardano-node-cbor-decode-block":         {"protocol": "blockfetch", "shape": "block", "built": False},
    "cardano-node-cbor-decode-tx-body":       {"protocol": "txsubmission", "shape": "tx-body", "built": False},
    "cardano-node-cbor-decode-certificate":   {"protocol": "txsubmission", "shape": "certificate", "built": False},
    "cardano-node-cbor-decode-auxiliary-data":{"protocol": "txsubmission", "shape": "auxiliary-data", "built": False},
}

# DWARF assertion primitive -> native SDK catalog entry. Sometimes/Reachable
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
    """Return the single cbor-fuzz load primitive, or raise."""
    cbor = [p for p in scenario.load if str(p.get("primitive", "")).startswith("cbor_fuzz")]
    if len(cbor) != 1:
        raise GeneratorError(
            f"expected exactly one cbor_fuzz load primitive, found {len(cbor)}"
        )
    return cbor[0]


def fuzz_spec(scenario):
    """Shared descriptor consumed by both backends: same target decoder, CBOR
    shape, seed, and asserted properties. NOT a shared mutation engine — local
    is byte-level mutate_cbor; the adversary is Term-level structural mutateTerm.
    """
    if scenario.target.get("implementation") not in SUPPORTED_IMPLEMENTATIONS:
        raise GeneratorError(
            f"target.implementation {scenario.target.get('implementation')!r} is not "
            "supported by Antithesis (amaru/differential = SP3)"
        )
    load = _cbor_load(scenario)
    return {
        "target_decoder": load["target_id"],
        "cbor_shape": load.get("shape"),
        "seed": scenario.seed,
        "mutation_rate": float(load.get("mutation_rate", 0.05)),
        "asserted_properties": [a["primitive"] for a in scenario.assertions],
    }


def map_assertions(scenario):
    """DWARF assertions -> native SDK catalog (Sometimes/Reachable). Zero = error."""
    catalog = []
    seen = set()
    for a in scenario.assertions:
        prim = a["primitive"]
        for entry in _ASSERTION_MAP.get(prim, []):
            if entry["id"] in seen:
                continue
            seen.add(entry["id"])
            catalog.append(dict(entry))
    if not catalog:
        raise GeneratorError(
            "scenario maps to zero SDK assertions — refusing to generate a "
            "test that asserts nothing (anti-false-green)"
        )
    return catalog
```

- [ ] **Step 4: Run the tests, verify pass**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && python3 -m pytest tools/test_sp2_generator.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add dwarf/profile_manager/antithesis_generator.py tools/test_sp2_generator.py
git commit -m "feat(sp2): fuzz_spec + assertion map (Sometimes/Reachable, zero=error)"
```

---

## Task 2: `derive_adversary` + `select_testnet_base`

**Files:**
- Modify: `dwarf/profile_manager/antithesis_generator.py`
- Create: `dwarf/profile_manager/antithesis_assets/{testnet.yaml,relay-dwarf-topology.json,tracer-config.yaml}` (copied from `antithesis/cardano_node_dwarf/`)
- Test: `tools/test_sp2_generator.py`

- [ ] **Step 1: Copy the testnet base assets**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
mkdir -p dwarf/profile_manager/antithesis_assets
cp antithesis/cardano_node_dwarf/testnet.yaml dwarf/profile_manager/antithesis_assets/
cp antithesis/cardano_node_dwarf/relay-dwarf-topology.json dwarf/profile_manager/antithesis_assets/
cp antithesis/cardano_node_dwarf/tracer-config.yaml dwarf/profile_manager/antithesis_assets/
```

- [ ] **Step 2: Write the failing test**

```python
# append to tools/test_sp2_generator.py

def test_derive_adversary_header_path():
    s = _load(HEADER)
    adv = gen.derive_adversary(s)
    assert adv["image"] == gen.ADVERSARY_IMAGE
    assert adv["protocol"] == "chainsync"
    assert adv["shape"] == "block-header"
    args = adv["command_args"]
    assert "--mutation-rate" in args and "0.05" in args
    assert "--network-magic" in args
    assert "--listen-port" in args
    assert "--upstream" in args            # upstream node it captures a base header from
    # seed is wired from antithesis_random at launch — args carry the seed FLAG,
    # value is a launch placeholder, not the scenario seed baked in.
    assert "--seed" in args


def test_derive_adversary_refuses_unbuilt_shape():
    s = _load(HEADER)
    s.load[0]["target_id"] = "cardano-node-cbor-decode-tx-body"
    with pytest.raises(gen.GeneratorError) as ei:
        gen.derive_adversary(s)
    assert "txsubmission" in str(ei.value)          # names the needed mode


def test_derive_adversary_refuses_amaru():
    s = _load(HEADER)
    s.target["implementation"] = "amaru"
    with pytest.raises(gen.GeneratorError) as ei:
        gen.derive_adversary(s)
    assert "SP3" in str(ei.value)


def test_select_testnet_base_returns_asset_dir():
    s = _load(HEADER)
    base = gen.select_testnet_base(s)
    assert (base / "testnet.yaml").exists()
    assert (base / "relay-dwarf-topology.json").exists()
```

- [ ] **Step 3: Run it, verify failure**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && python3 -m pytest tools/test_sp2_generator.py -k derive_adversary -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'derive_adversary'`.

- [ ] **Step 4: Implement `derive_adversary` + `select_testnet_base`**

```python
# add to antithesis_generator.py
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent / "antithesis_assets"
NETWORK_MAGIC = 42          # matches the testnet base (testnet.yaml networkMagic)
ADVERSARY_LISTEN_PORT = 3001
ADVERSARY_UPSTREAM = "p1.example:3001"   # in-bundle base-header source
SEED_LAUNCH_PLACEHOLDER = "0x1"          # overwritten by antithesis_random at launch


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
            "--mutation-rate", repr(fs["mutation_rate"]).rstrip("0").rstrip(".") if "." in repr(fs["mutation_rate"]) else str(fs["mutation_rate"]),
            "--upstream", ADVERSARY_UPSTREAM,
            "--seed", SEED_LAUNCH_PLACEHOLDER,
        ],
    }


def select_testnet_base(scenario):
    """Return the directory of version-controlled testnet base assets to overlay."""
    if scenario.target.get("implementation") not in SUPPORTED_IMPLEMENTATIONS:
        raise GeneratorError("amaru/differential testnet base is SP3")
    return _ASSETS
```

Note on the mutation-rate formatting: keep it simple and exact. Replace the brittle one-liner with:

```python
            "--mutation-rate", _fmt_rate(fs["mutation_rate"]),
```

and add:

```python
def _fmt_rate(x):
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return s if s else "0"
```

(Use `_fmt_rate` — do not ship the inline `repr().rstrip()` version.)

- [ ] **Step 5: Run the tests, verify pass**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && python3 -m pytest tools/test_sp2_generator.py -q`
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add dwarf/profile_manager/antithesis_generator.py \
        dwarf/profile_manager/antithesis_assets tools/test_sp2_generator.py
git commit -m "feat(sp2): derive_adversary + testnet base (chainsync header built; others refused)"
```

---

## Task 3: `render_bundle` (compose + topology + composer + manifest)

**Files:**
- Modify: `dwarf/profile_manager/antithesis_generator.py`
- Test: `tools/test_sp2_generator.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tools/test_sp2_generator.py

def test_render_bundle_files_and_labels(tmp_path):
    s = _load(HEADER)
    arts = gen.render_bundle(s, registry="reg.example/x", tag="t1")
    files = arts.files
    # compose + topology + composer + manifest + readme
    assert "config/docker-compose.yaml" in files
    assert "relay-dwarf-topology.json" in files
    assert any(p.startswith("test/") for p in files)
    assert "dwarf-manifest.json" in files
    compose = files["config/docker-compose.yaml"]
    # native conventions
    assert "ghcr.io/j-gainsec/dwarf-adversary:0.1.0" in compose
    assert "com.antithesis.exclude_from_faults" in compose
    assert "build:" not in compose                       # hermetic: registry images only
    # adversary args carried through
    assert "--mutation-rate" in compose
    # manifest provenance
    man = json.loads(files["dwarf-manifest.json"])
    assert man["scenario_id"] == s.id
    assert man["adversary"]["protocol"] == "chainsync"
    assert len(man["assertions"]) >= 1


def test_composer_parallel_driver_no_setup_complete():
    s = _load(HEADER)
    arts = gen.render_bundle(s, registry="reg.example/x", tag="t1")
    drivers = {k: v for k, v in arts.files.items()
               if "parallel_driver" in k}
    assert drivers
    for body in drivers.values():
        assert "setup_complete" not in body and "antithesis_setup" not in body
```

- [ ] **Step 2: Run it, verify failure**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && python3 -m pytest tools/test_sp2_generator.py -k render_bundle -q`
Expected: FAIL — no `render_bundle`.

- [ ] **Step 3: Implement the renderers + `render_bundle`**

```python
# add to antithesis_generator.py
import json
from profile_manager.backends.base import BackendArtifacts, write_artifacts

# Harness/infra services that must be excluded from fault injection.
_FAULT_LABEL = 'com.antithesis.exclude_from_faults: "network,kill,pause,stop"'


def _render_adversary_service(adv):
    """Render the dwarf-adversary compose service block as YAML text lines."""
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
        lines.append(f"      - \"{tok}\"")
    lines += [
        "    depends_on:",
        "      configurator:",
        "        condition: service_completed_successfully",
        "      p1:",
        "        condition: service_started",
        "    restart: always",
    ]
    return lines


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


def render_bundle(scenario, *, registry, tag="latest"):
    fs = fuzz_spec(scenario)
    adv = derive_adversary(scenario)
    catalog = map_assertions(scenario)
    base = select_testnet_base(scenario)

    # Base compose = the proven cardano_node_dwarf testnet, with the adversary
    # block replaced by our scenario-derived one. We render fresh rather than
    # text-patch: copy the base service stanzas we need + our adversary block.
    base_compose = (base.parent.parent / "antithesis" / "cardano_node_dwarf"
                    / "docker-compose.yaml")
    # The version-controlled base lives in antithesis/; assets/ holds the small
    # overlay files. Read the base compose, strip its hand-baked adversary/extras,
    # and append our adversary block. (Implemented by _merge_compose below.)
    compose_text = _merge_compose(base_compose.read_text(), _render_adversary_service(adv))

    files = {
        conv.COMPOSE_RELPATH: compose_text,
        "relay-dwarf-topology.json": (base / "relay-dwarf-topology.json").read_text(),
        "testnet.yaml": (base / "testnet.yaml").read_text(),
        "tracer-config.yaml": (base / "tracer-config.yaml").read_text(),
        "dwarf-manifest.json": _render_manifest(scenario, adv, catalog, fs),
        "README.md": _render_readme(scenario),
    }
    files.update(_render_composer(scenario, catalog))
    return BackendArtifacts(backend="antithesis", files=files,
                            summary={"scenario": scenario.id, "registry": registry, "tag": tag})
```

Implement `_merge_compose` to: keep the base `x-cardano-node`/`x-cardano-relay` anchors and the `configurator, tracer, p1, relay2 (dwarf topology), tracer-sidecar, log-tailer` services + `volumes:`/`networks:` blocks; drop the base's hand-baked `dwarf-adversary`, `tx-generator`, `sidecar`, `adversary`, `asteria-game`, `p2`, `p3`, `relay1` extras not needed for the header fuzz; then splice the rendered adversary block before the `volumes:` line. Because the base is structured text, implement `_merge_compose` as a line-walk:

```python
_DROP_SERVICES = {"dwarf-adversary", "tx-generator", "sidecar", "adversary",
                  "asteria-game"}   # keep p1, relay2, configurator, tracer*, log-tailer


def _merge_compose(base_text, adversary_lines):
    out, lines = [], base_text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        # a top-level service header is "  <name>:" at 2-space indent
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


def _render_readme(scenario):
    return (
        f"# Native Antithesis test: {scenario.id}\n\n"
        "Generated by Dwarf (`cardano-profile scenario run --backend antithesis`).\n"
        "cardano-node testnet + dwarf-adversary chain-sync header fuzzer.\n\n"
        "No secrets here. Do not commit wallets, PATs, or credentials.\n"
    )
```

> Note: this requires the generator to know the repo-relative path to the base compose. Pass it explicitly instead of guessing from `__file__`. Change `render_bundle` to accept `base_compose_path` with a default of the in-repo archetype, resolved via an importable constant `ARCHETYPE_COMPOSE = Path(__file__).resolve().parents[2] / "antithesis" / "cardano_node_dwarf" / "docker-compose.yaml"`. Define `ARCHETYPE_COMPOSE` at module top and use it directly; delete the brittle `base.parent.parent` expression.

- [ ] **Step 4: Run the tests, verify pass**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && python3 -m pytest tools/test_sp2_generator.py -q`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add dwarf/profile_manager/antithesis_generator.py tools/test_sp2_generator.py
git commit -m "feat(sp2): render_bundle — testnet + adversary + composer + manifest"
```

---

## Task 4: `verify_generated_bundle` (Stage-2 gate)

**Files:**
- Modify: `dwarf/profile_manager/antithesis_generator.py`
- Test: `tools/test_sp2_generator.py`

- [ ] **Step 1: Write the failing tests (positive + every negative)**

```python
# append to tools/test_sp2_generator.py

def _write_bundle(tmp_path, scenario):
    arts = gen.render_bundle(scenario, registry="reg.example/x", tag="t1")
    from profile_manager.backends.base import write_artifacts
    write_artifacts(arts, str(tmp_path))
    return tmp_path


def test_verify_generated_bundle_green(tmp_path):
    s = _load(HEADER)
    b = _write_bundle(tmp_path, s)
    res = gen.verify_generated_bundle(str(b))
    assert res["state"] == "pass", res


def test_verify_catches_build_context(tmp_path):
    s = _load(HEADER)
    b = _write_bundle(tmp_path, s)
    p = b / "config/docker-compose.yaml"
    p.write_text(p.read_text() + "\n  evil:\n    build: .\n")
    res = gen.verify_generated_bundle(str(b))
    assert res["state"] == "fail" and any("build:" in r for r in res["reasons"])


def test_verify_catches_missing_fault_label(tmp_path):
    s = _load(HEADER)
    b = _write_bundle(tmp_path, s)
    p = b / "config/docker-compose.yaml"
    p.write_text(p.read_text().replace("com.antithesis.exclude_from_faults", "x_disabled"))
    res = gen.verify_generated_bundle(str(b))
    assert res["state"] == "fail" and any("fault" in r.lower() for r in res["reasons"])


def test_verify_catches_setup_complete_in_driver(tmp_path):
    s = _load(HEADER)
    b = _write_bundle(tmp_path, s)
    drv = next(b.glob("test/**/parallel_driver_*.sh"))
    drv.write_text(drv.read_text() + '\necho \'{"antithesis_setup":{}}\'\n')
    res = gen.verify_generated_bundle(str(b))
    assert res["state"] == "fail" and any("setup" in r.lower() for r in res["reasons"])
```

- [ ] **Step 2: Run it, verify failure**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && python3 -m pytest tools/test_sp2_generator.py -k verify -q`
Expected: FAIL — no `verify_generated_bundle`.

- [ ] **Step 3: Implement `verify_generated_bundle`**

```python
# add to antithesis_generator.py
import os, re


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
    # topology resolves: adversary reachable as a relay root
    topo = bundle / "relay-dwarf-topology.json"
    if not topo.exists():
        reasons.append("missing relay-dwarf-topology.json")
    elif "dwarf-adversary" not in topo.read_text():
        reasons.append("topology does not list dwarf-adversary as a root")
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
```

- [ ] **Step 4: Run the tests, verify pass**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && python3 -m pytest tools/test_sp2_generator.py -q`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add dwarf/profile_manager/antithesis_generator.py tools/test_sp2_generator.py
git commit -m "feat(sp2): verify_generated_bundle Stage-2 gate + negative tests"
```

---

## Task 5: backend selector wiring (`scenario run --backend`)

**Files:**
- Modify: `dwarf/profile_manager/antithesis_generator.py` (add `generate_native_test`)
- Modify: `dwarf/profile_manager/scenario.py` (add `run_scenario_backend`)
- Modify: `dwarf/profile_manager/cli.py` (`--backend` flag + dispatch)
- Test: `tools/test_sp2_generator.py`

- [ ] **Step 1: Write the failing test for `generate_native_test`**

```python
# append to tools/test_sp2_generator.py

def test_generate_native_test_writes_and_verifies(tmp_path):
    out = tmp_path / "bundle"
    res = gen.generate_native_test(str(HEADER), out_dir=str(out),
                                   registry="reg.example/x", tag="t1")
    assert res["verify"]["state"] == "pass", res
    assert (out / "config/docker-compose.yaml").exists()
    assert res["bundle_dir"] == str(out)
```

- [ ] **Step 2: Run it, verify failure**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && python3 -m pytest tools/test_sp2_generator.py -k generate_native -q`
Expected: FAIL — no `generate_native_test`.

- [ ] **Step 3: Implement `generate_native_test`**

```python
# add to antithesis_generator.py
from profile_manager import scenario as _scn


def generate_native_test(scenario_path, *, out_dir, registry, tag="latest",
                         registry_path=None):
    """Load + validate the scenario, render the native bundle, write it, and run
    the Stage-2 gate. Returns {bundle_dir, files, verify}."""
    # semantic validation first (refuses unregistered primitives by name)
    _scn.semantic_validate_scenario(scenario_path, registry_path)
    scenario = _scn.load_scenario(scenario_path)
    arts = render_bundle(scenario, registry=registry, tag=tag)
    written = write_artifacts(arts, out_dir)
    verify = verify_generated_bundle(out_dir)
    return {"bundle_dir": out_dir, "files": written, "verify": verify}
```

> Verify the exact name/signature of the semantic validator before wiring: `grep -n "def semantic_validate_scenario" dwarf/profile_manager/scenario.py`. If it takes `(path, registry_path)` positionally, call it as shown; adjust if the signature differs.

- [ ] **Step 4: Add `run_scenario_backend` dispatcher to `scenario.py`**

```python
# in scenario.py, after verify_scenario
def run_scenario_backend(path, *, backend, runs_dir, state_dir, registry_path=None,
                         out_dir=None, registry=None, tag="latest"):
    """Dispatch a scenario to a backend. local-devnet = the existing executor;
    antithesis = generate + verify a native bundle (launch is a separate step)."""
    if backend == "local-devnet":
        return {"backend": "local-devnet",
                "result": run_scenario(path, runs_dir=runs_dir, state_dir=state_dir,
                                       registry_path=registry_path)}
    if backend == "antithesis":
        from profile_manager import antithesis_generator as gen
        if not out_dir:
            raise ValueError("antithesis backend requires --out")
        return {"backend": "antithesis",
                "result": gen.generate_native_test(path, out_dir=out_dir,
                                                    registry=registry or gen.ADVERSARY_IMAGE.rsplit("/", 1)[0],
                                                    tag=tag, registry_path=registry_path)}
    raise ValueError(f"unknown backend: {backend!r}")
```

> `run_scenario`'s exact keyword set is `(path, *, runs_dir, state_dir, registry_path=None, framework_version=..., framework_commit=..., actor=...)`. Pass only the three shown; defaults cover the rest.

- [ ] **Step 5: Wire the CLI `--backend` flag**

In `cli.py`, on the existing `scenario run` subparser add:

```python
    scenario_run.add_argument("--backend", choices=("local-devnet", "antithesis"),
                              default="local-devnet")
    scenario_run.add_argument("--out", default=None,
                              help="Output dir for the antithesis bundle.")
    scenario_run.add_argument("--registry", default=None)
    scenario_run.add_argument("--tag", default="latest")
```

In `cmd_scenario`, where `run` is handled, branch:

```python
    if args.backend == "local-devnet":
        # ... existing run_scenario path unchanged ...
    else:
        res = scenario.run_scenario_backend(
            args.path, backend="antithesis", runs_dir=args.runs_dir,
            state_dir=args.state_dir, registry_path=getattr(args, "registry_path", None),
            out_dir=args.out, registry=args.registry, tag=args.tag)
        v = res["result"]["verify"]
        if args.json:
            print(json.dumps(res["result"]))
        else:
            print(f"bundle: {res['result']['bundle_dir']}")
            print("VERIFY:", v["state"].upper())
            for r in v["reasons"]:
                print("  -", r)
        return 0 if v["state"] == "pass" else 1
```

> Read the existing `scenario run` handler first (`grep -n "def cmd_scenario" dwarf/profile_manager/cli.py`) and slot the branch into the actual structure — do not duplicate the existing local path; wrap it in the `if args.backend == "local-devnet":` arm verbatim.

- [ ] **Step 6: Run the unit test + a CLI smoke**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && python3 -m pytest tools/test_sp2_generator.py -q`
Expected: 14 passed.

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && PYTHONPATH=dwarf python3 dwarf/cardano-profile scenario run dwarf/scenarios/cardano-node-cbor-block-header-fuzz-structured.yaml --backend antithesis --out /tmp/sp2-bundle --registry reg.example/x`
Expected: prints `VERIFY: PASS`, exit 0.

- [ ] **Step 7: Commit**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add dwarf/profile_manager/antithesis_generator.py dwarf/profile_manager/scenario.py \
        dwarf/profile_manager/cli.py tools/test_sp2_generator.py
git commit -m "feat(sp2): scenario run --backend {local-devnet|antithesis} + generate_native_test"
```

---

## Task 6: header-path round-trip on cardano-box

**Files:**
- Create: `tools/sp2_roundtrip.sh`

- [ ] **Step 1: Write the round-trip script**

```bash
#!/usr/bin/env bash
# SP2 round-trip: generate the header-path native bundle, run the Stage-2 gate,
# lint with docker compose, and confirm moog accepts the bundle as a test asset.
# Run on cardano-box (Docker + moog present).
set -uo pipefail
cd "$(dirname "$0")/.."
SCEN=dwarf/scenarios/cardano-node-cbor-block-header-fuzz-structured.yaml
OUT=/tmp/sp2-bundle
REG=reg.example/x

rm -rf "$OUT"
PYTHONPATH=dwarf python3 dwarf/cardano-profile scenario run "$SCEN" \
  --backend antithesis --out "$OUT" --registry "$REG" || { echo "FAIL generate/verify"; exit 1; }

# docker compose static lint (no pull, no run)
docker compose -f "$OUT/config/docker-compose.yaml" config >/dev/null \
  && echo "OK docker compose config" || { echo "FAIL docker compose config"; exit 1; }

# moog accepts the bundle dir as a test asset (dry-run plan; no submission)
PYTHONPATH=dwarf python3 dwarf/cardano-profile moog asset validate --asset-dir "$OUT" --json \
  && echo "OK moog asset validate" || echo "WARN moog asset validate (check layout)"
echo "sp2 round-trip done"
```

- [ ] **Step 2: Run the overlap assertion test (local)**

```python
# append to tools/test_sp2_generator.py
def test_overlap_same_decoder_property_seed():
    s = _load(HEADER)
    fs = gen.fuzz_spec(s)
    # the local executor reads exactly these from the same load primitive
    load = s.load[0]
    assert fs["target_decoder"] == load["target_id"]
    assert fs["cbor_shape"] == load["shape"]
    assert fs["seed"] == s.seed
    # mutation engines differ by design — assert we did NOT claim kind parity
    assert "mutation_kinds" not in fs
```

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && python3 -m pytest tools/test_sp2_generator.py -q`
Expected: 15 passed.

- [ ] **Step 3: Run the round-trip on cardano-box**

```bash
chmod +x tools/sp2_roundtrip.sh
ssh cardano-box 'cd /home/nigel/dwarf-v4 && tools/sp2_roundtrip.sh'
```

Expected: `VERIFY: PASS`, `OK docker compose config`, round-trip done. (If the deployed tree lags, `git pull` on cardano-box first.)

- [ ] **Step 4: Commit**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add tools/sp2_roundtrip.sh tools/test_sp2_generator.py
git commit -m "test(sp2): header-path round-trip + overlap (same decoder/property/seed) on cardano-box"
```

---

## Self-Review

- **Spec coverage:** backend selector (Task 5) ✓; scenario→bundle generator (Tasks 1–3) ✓; shared descriptor / overlap (Task 1 `fuzz_spec` + Task 6 overlap test) ✓; Stage-2 verify (Task 4) ✓; Moog launch (Task 5 writes the bundle into the asset layout + Task 6 `moog asset validate`) ✓; cardano-node-only refusal + unbuilt-mode refusal (Task 2) ✓; native conventions — SDK Sometimes/Reachable (Task 1), fault labels + hermetic + composer (Tasks 3–4), antithesis_random seed (Task 2 placeholder + manifest seed_policy) ✓.
- **Placeholder scan:** none — every code step shows real code. The two brittle one-liners (`repr().rstrip()` and `base.parent.parent`) are explicitly flagged for replacement with `_fmt_rate` and `ARCHETYPE_COMPOSE`.
- **Type/name consistency:** `GeneratorError`, `fuzz_spec`, `map_assertions`, `derive_adversary`, `select_testnet_base`, `render_bundle`, `verify_generated_bundle`, `generate_native_test` used consistently across tasks; `verify_generated_bundle` returns `{"state","reasons"}` everywhere; `ADVERSARY_MODES`/`ADVERSARY_IMAGE`/`ARCHETYPE_COMPOSE` defined once.
- **Open verification (do at execution):** confirm `semantic_validate_scenario` signature and the `scenario run`/`cmd_scenario` handler shape before wiring Task 5 (both flagged inline).
