# DWARF

DWARF is a fuzzing and adversarial-testing framework for Cardano node implementations
(Haskell `cardano-node` and Rust `amaru`). It exercises a node's serialization /
deserialization, mini-protocol, runtime, resource, and consensus surfaces with
structurally-malformed and adversarial inputs, captures structured evidence, and
**bridges its CBOR-decoder fuzzing into the [Antithesis](https://antithesis.com)
deterministic-simulation platform** so a real node decodes mutated payloads across
thousands of explored timelines.

It has two complementary halves:

1. **Local framework** — a scenario-driven fuzz/test runner (Python `profile_manager`
   + `cardano-profile` CLI + dashboard) with a catalog of ~223 scenarios across ~8
   capability families, run against containerized targets on any Docker host.
2. **Antithesis bridge** — a generator that turns a CBOR-decode scenario into a
   self-contained Antithesis test bundle, plus a Haskell **`dwarf-adversary`** that
   joins a live testnet as a node-to-node (N2N) peer and serves structurally-mutated
   CBOR to the node under test.

---

## What it does

### Local fuzz testing

The local catalog (`dwarf/scenarios/`, 223 YAML scenarios) spans these families:

| Family | What it exercises |
|---|---|
| **CBOR structural fuzz** | Decoder robustness on structurally-mutated CBOR — block-header, block, tx-body, certificate, auxiliary-data — against `cardano-node` and `amaru` (`*-cbor-*`, `edge-cases-cbor-*`). |
| **Mini-protocol fuzz** | N2N protocol grammar / sequencing (handshake, keep-alive, chain-sync, block-fetch, tx-submission) with malformed frames. |
| **Runtime / substrate** | The bulk of the catalog (`runtime-*`, `phase*`) — runtime profiles, bundle attestation, chain-verify, differential divergence, SARIF export, forensics, credentials. |
| **Resource pressure** | Host cpu/disk/ram/bandwidth exhaustion and disk-fill-during-sync (`resource-*`). |
| **Differential** | `amaru` ↔ `cardano-node` validation-path agreement on the same input. |

CBOR fuzzing uses two engines, selectable per scenario via `load` primitives:
`cbor_fuzz` / `cbor_fuzz_target` (semantic, structure-aware mutation) and
`cbor_fuzz_structured` (byte-level structural mutation), plus `cbor_edge_cases`
for curated corner cases. Each run writes a manifest, assertion summary, NDJSON
log, and probe outputs under `dwarf/runs/` (dashboard-inspectable) and
`dwarf/evidence/`.

Run the catalog through the `cardano-profile` CLI (the dashboard container wraps the
same code path). See `INSTALL.md` and `OPERATIONS.md`.

### Antithesis integration

The **`dwarf/profile_manager/antithesis_generator.py`** generator converts a single
CBOR-decode scenario into a deployable Antithesis test bundle. By design it is
**CBOR-only and `cardano-node`-only** (`SUPPORTED_IMPLEMENTATIONS = {"cardano-node"}`;
it requires exactly one `cbor_fuzz` load primitive). It maps each decode target to an
adversary protocol + CBOR shape:

| Decode target | N2N protocol | CBOR shape | Built |
|---|---|---|---|
| block-header | chain-sync (#2) | `block-header` | ✅ |
| block | block-fetch (#3) | `block` | ✅ |
| tx-body | tx-submission2 (#4) | `tx-body` | ✅ |
| certificate | tx-submission2 (#4) | `certificate` | ✅ |
| auxiliary-data | tx-submission2 (#4) | `auxiliary-data` | ✅ |

`render_bundle()` emits a self-contained bundle: the full Antithesis test harness
(setup `sidecar` + composer `adversary` driver), the testnet (producers, relays,
tracer, tx-generator), and the `dwarf-adversary` wired for the target protocol/shape.
For block-fetch it also applies a **topology eclipse** (`_apply_eclipse`) so the node
under test fetches blocks only from the adversary.

### The `dwarf-adversary`

A Haskell N2N peer (`antithesis/components/dwarf-adversary/`, image
`ghcr.io/j-gainsec/dwarf-adversary:0.10.0`) that speaks the real Ouroboros N2N
mini-protocols — chain-sync (#2), block-fetch (#3), tx-submission2 (#4), keep-alive
(#8) — and serves **structurally-mutated CBOR** to the node under test. It bootstraps
a valid chain (proxying an upstream producer or serving a baked corpus), reaches GSM
`CaughtUp`, then fuzzes the targeted decoder via a mutating codec. Mutation is driven
by a per-run `--seed` for deterministic reproduction.

Key flags: `--protocol {chainsync|blockfetch|txsubmission}`, `--cbor-shape {block-header|block|tx-body|certificate|auxiliary-data}`,
`--mutation-rate`, `--upstream HOST:PORT`, `--seed`, `--network-magic`, `--listen-port`,
`--baked-chain FILE` (serve an embedded chain, no upstream), `--capture-to FILE`
(serialize a captured chain), `--selftest`.

---

## Confirmed status

**Confirmed live on Antithesis** (tenant `amaru-cardano`, `--no-faults`, 1h runs):

| CBOR shape | Path | Live assertion | Status |
|---|---|---|---|
| block-header | chain-sync | `dwarf_served_mutated_header` (decode-on-receipt) | ✅ live (SP2) |
| **tx-body** | tx-submission | `dwarf_served_mutated_tx` | ✅ **PASSED** 2026-06-13 (run `0e1c9877…`, Completed 1h 12m) |
| **block** | block-fetch | `dwarf_served_mutated_block` | ✅ **PASSED** 2026-06-13 (run `ea5ad7d0…`, Completed 1h 13m) |
| certificate | tx-submission | `dwarf_served_mutated_tx` (served inside the tx) | built; not yet run live |
| auxiliary-data | tx-submission | `dwarf_served_mutated_tx` (served inside the tx) | built; not yet run live |

Both hard serve-path shapes (tx-body, block) are now proven on Antithesis: a real
`cardano-node` connects to the `dwarf-adversary`, pulls structurally-mutated CBOR, and
runs its decoder on it — with the adversary stable (no crash) and the run completing.

**Local gates** (run on the cardano-box testnet, all green):

- `tools/sp3a_topology_eclipse_repro.sh` — block-fetch under single-network topology
  eclipse: `dwarf_served_mutated_block=69`, VRFKeyBadProof 0, RestartCount 0.
- `tools/sp3a_eclipse_repro.sh` / `tools/sp3a_baked_repro.sh` — block-fetch under
  custom-network / baked-corpus eclipse (local-only capabilities).
- `tools/sp3_caughtup_repro.sh` — the advancing CaughtUp peer foundation.

The live eclipse for block-fetch uses **topology alone on the single default network**
(no custom docker network) inside the full-harness bundle — the dwarf-adversary serves
no peer-sharing gossip, so the node under test reaches only the adversary. The
custom-network and producer-less baked bundles are retained as local capabilities
(they lack the Antithesis test harness and must not be run live).

---

## Layout

```text
DWARF/
├── README.md  INSTALL.md  OPERATIONS.md  RELEASE-NOTES.md  TEST-OUTPUTS.md
├── antithesis/
│   ├── components/dwarf-adversary/      # Haskell N2N adversary (cabal)
│   ├── cardano_node_dwarf/              # full-harness Antithesis bundle (live-proven)
│   ├── cardano_node_dwarf_eclipse/      # custom-network eclipse (local-only)
│   └── cardano_node_dwarf_baked/        # baked-corpus eclipse (local-only)
├── dwarf/
│   ├── cardano-profile                  # CLI entrypoint
│   ├── profile_manager/                 # framework + antithesis_generator.py
│   ├── scenarios/                       # 223 scenario YAMLs (~8 families)
│   ├── primitives/                      # primitive registry + schemas
│   ├── profiles/                        # profile/template catalog
│   ├── runs/  bundles/  evidence/       # run artifacts + evidence
│   ├── spec/                            # SARIF + spec schemas
│   └── docs/
├── delivery/                            # Docker delivery wrapper (framework image)
├── infrastructure/docker/
├── tools/                               # local repro/validation gates
├── tests/                               # framework + integration tests
└── docs/                                # design specs + implementation plans
```

## Build & run

**Local framework / dashboard** (any Docker host with Compose v2):

```bash
delivery/scripts/install.sh
delivery/scripts/build-image.sh
delivery/scripts/deploy.sh
delivery/scripts/status.sh
```

**`dwarf-adversary`** (built on a GHC 9.6.x host):

```bash
cd antithesis/components/dwarf-adversary
cabal build -w ghc-9.6.7 exe:dwarf-adversary
./build-image.sh ghcr.io/<owner>/dwarf-adversary:<tag>
```

**Generate an Antithesis bundle** from a CBOR-decode scenario, via the `cardano-profile` CLI:

```bash
dwarf/cardano-profile antithesis build <profile_id> \
  --scenario dwarf/scenarios/cardano-node-cbor-tx-body-fuzz.yaml \
  --registry ghcr.io/<owner> --tag 0.10.0 --out antithesis/cardano_node_dwarf
```

(The generator lives in `dwarf/profile_manager/antithesis_generator.py`; live campaigns
are launched from the bundle through the Moog requester flow.)

Verify the package layout with `delivery/tests/test_delivery_contract.sh`.

---

## Roadmap

The CBOR family is the one fully bridged to Antithesis (header + tx-body + block
proven live; certificate + auxiliary-data built and pending a live run). The
highest-leverage next bridges are **mini-protocol fuzz** (the adversary already speaks
every N2N protocol), **differential** `amaru` ↔ `cardano-node` agreement, and
**mempool / tx pressure**. Runtime/network-fault and snapshot scenarios are largely
redundant with Antithesis's native fault injector and are better expressed as fault
config than rebuilt as adversaries; resource-pressure and forensics scenarios stay
local. See `docs/` for design specs and the capability-surface map.
