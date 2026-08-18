# DWARF

DWARF is a fuzzing and adversarial-testing framework for Cardano node implementations
(Haskell `cardano-node` and Rust `amaru`). It exercises a node's serialization /
deserialization, **ledger-rule**, mini-protocol, runtime, resource, and consensus
surfaces with structurally-malformed and adversarial inputs, captures structured
evidence, and **bridges its fuzzing into the [Antithesis](https://antithesis.com)
deterministic-simulation platform** so a real node processes mutated payloads across
thousands of explored timelines.

It runs in two places from one set of definitions:

1. **Local framework** — a scenario-driven fuzz/test runner (Python `profile_manager`
   + `cardano-profile` CLI + web dashboard) that spins up containerized Cardano devnets
   (Haskell `cardano-node`, Rust `amaru`, or **mixed**), runs a catalog of scenarios
   across CBOR, ledger-rule, mini-protocol, runtime, resource, and consensus families,
   and captures structured, replayable evidence. It also drives **native
   coverage-guided fuzzing** — AFL++ steered by real edge coverage over a
   SanitizerCoverage-instrumented `cardano-node` (decode + the full Conway ledger rules,
   incl. `applyBlock`).
2. **Antithesis bridge** — a generator that turns a fuzz scenario into a self-contained
   Antithesis test bundle, plus a Haskell **`dwarf-adversary`** that joins a live testnet
   as a node-to-node (N2N) peer and serves structurally-mutated CBOR to the node under
   test, and an in-process **`dwarf-decoder-fuzz`** workload (the same `applyBlock`
   surface, run under Antithesis). Profiles parameterize implementation, version,
   network, topology, and peer-sharing.

---

## Quickstart

Deploy the framework + dashboard on any Docker-capable Linux host (Docker with
Compose v2, ~20 GB free disk). No credentials or wallet are required for local use.

```bash
# 1. Clone
git clone https://github.com/Cyber-Castellum/DWARF.git
cd DWARF

# 2. Prepare runtime dirs (creates ./var/{runs,state,bundles} + ssh placeholders)
delivery/scripts/install.sh

# 3. Build the framework image (dwarf/framework:current)
delivery/scripts/build-image.sh

# 4. Deploy the dashboard (container dwarf-fw on 0.0.0.0:8787)
delivery/scripts/deploy.sh
```

**Confirm it's working:**

```bash
# a. Delivery status — image, container, port, inventory
delivery/scripts/status.sh

# b. Dashboard responds (expect HTTP 200)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/operate

# c. Validate the full scenario catalog (expect: RESULT: PASS, 0 failures)
docker exec dwarf-fw python3 dwarf/scripts/validate_scenarios.py
```

Then open **http://127.0.0.1:8787/operate** (or `http://<host-lan-ip>:8787/operate`)
and browse `/operate/scenarios`, `/operate/targets`, and `/learn`.

Deploy on a different port with `DWARF_DASHBOARD_PORT=8877 delivery/scripts/deploy.sh`.
Copy `.env.example` to `.env` to override any default. Full prerequisites and options
are in [`INSTALL.md`](INSTALL.md); day-2 operations in [`OPERATIONS.md`](OPERATIONS.md).
To tear down: `delivery/scripts/uninstall.sh` (add `--purge` to also remove runtime data).

### Running scenarios

The steps above deploy the dashboard and validate the whole catalog — no extra
dependencies. **Executing** a scenario needs its runtime layer, and is run from a
Docker-capable host (the CLI, not the hardened UI container):

- **Fuzz scenarios** (CBOR / ledger-rule / mini-protocol) decode into a target
  binary. Build the targets first (Rust for `amaru`, GHC/cabal for `cardano-node`):

  ```bash
  make -C dwarf/targets amaru          # or: make -C dwarf/targets all
  ./dwarf/cardano-profile scenario run dwarf/scenarios/amaru-cbor-tx-body-fuzz.yaml
  ```

- **Substrate scenarios** (`runtime-substrate-*`, consensus differentials) compose a
  containerized devnet, so they need Docker and the `devnet` runtime:

  ```bash
  ./dwarf/cardano-profile scenario run --runtime devnet \
    dwarf/scenarios/runtime-substrate-honest-baseline-example-smoke.yaml
  ```

- **Native coverage-guided fuzz** and **Antithesis campaigns** use the published
  GHCR images — see [Container images](#container-images) below.

---

## What it does

### Local fuzz testing

The local catalog (`dwarf/scenarios/`, 239 YAML scenarios) spans these families:

| Family | What it exercises |
|---|---|
| **CBOR structural fuzz** | Decoder robustness on structurally-mutated CBOR — block-header, block, tx-body, certificate, auxiliary-data — against `cardano-node` and `amaru` (`*-cbor-*`, `edge-cases-cbor-*`). |
| **Native coverage-guided fuzz** | Edge-guided AFL++ over a **SanitizerCoverage-instrumented `cardano-node`** dependency tree (GHC `-fllvm` + LLVM SanCov), in a cross-platform Docker image. One harness, surface selected by `DWARF_DECODER`: `tx / block / header / txbody / ledger / applytx / applyblock` decode + ledger surfaces and `handshake / txsub / keepalive` mini-protocol codecs. See [`COVERAGE-HARNESS.md`](antithesis/components/dwarf-adversary/COVERAGE-HARNESS.md). |
| **Ledger-rule fuzz** | Decode + **run the real Conway ledger rules**: `applytx` (mempool `applyTx` STS) and `applyblock` (full `BBODY → LEDGERS → per-tx LEDGER` over a genesis-initialised `NewEpochState`). The deepest surfaces — they reach `ConwayUtxow/Utxo/Certs` validation, not just decoding. |
| **Mini-protocol fuzz** | N2N protocol grammar / sequencing / state-machine — dedicated `cardano-node-mini-protocol-*-fuzz` scenarios for handshake, chain-sync, block-fetch, tx-submission, keep-alive, peer-sharing, plus wrong-version / malformed-handshake gating. |
| **Adversarial topology / consensus** | Eclipse, sybil, byzantine block-fetch, fork-switch, era / hard-fork boundaries. |
| **Runtime / network faults** | Partition–rejoin, restart / tip recovery, freeze / recover, keep-alive failure cascade, slow-loris, time-skew. |
| **Resource pressure** | Host cpu / disk / ram / bandwidth exhaustion and disk-fill-during-sync (`resource-*`). |
| **Mempool / tx pressure** | Batch / window pressure, mempool-relay pressure, local-tx-monitor faults. |
| **Snapshot / recovery** | Snapshot corruption / recovery, multi-day pause–resume, deterministic checkpointing. |
| **Differential** | `amaru` ↔ `cardano-node` validation-path agreement on the same input (`replay-and-diff`). |
| **Forensics / evidence** | pcap / syscall / gc capture, bundle attestation, chain-verify, SARIF export, credential checks. |
| **Runtime substrate / phased** | The bulk of `runtime-substrate-*` and `phase*` — runtime profiles, capability demos, and the generated multi-node baselines that the above families build on. |

CBOR fuzzing uses two engines, selectable per scenario via `load` primitives:
`cbor_fuzz` / `cbor_fuzz_target` (semantic, structure-aware mutation) and
`cbor_fuzz_structured` (byte-level structural mutation), plus `cbor_edge_cases`
for curated corner cases. Each run writes a manifest, assertion summary, NDJSON
log, and probe outputs under the runtime state directory (dashboard-inspectable).

**Seed corpus via cuddle.** The fuzzers start from **spec-valid CBOR generated by
[`cuddle`](https://github.com/input-output-hk/cuddle)** from the official Conway CDDL
— cuddle produces structurally-valid inputs, DWARF corrupts them adversarially and
asserts oracle / differential properties (the two are complementary halves). The
generator is `dwarf/scripts/gen_cuddle_seeds.py`, its output lives under
`dwarf/corpora/cuddle-generated/`, and the relationship, scope boundary, and phased
integration plan are written up in
[`dwarf/docs/cuddle-relationship.md`](dwarf/docs/cuddle-relationship.md).

### Native coverage-guided fuzzing

Beyond generational CBOR fuzzing, DWARF runs **edge-coverage-guided AFL++** against a
natively-instrumented `cardano-node`. The whole dependency tree is compiled with GHC
`-fllvm` + an LLVM **SanitizerCoverage** pass, so AFL steers mutation by real edge
coverage — packaged as a cross-platform Docker image (`dwarf-haskell-cov`). A single
harness (`dwarf-decode-any`) selects the surface via `DWARF_DECODER`, from pure decode
(`tx`, `block`, `header`) through the Conway ledger rules (`applytx`, `applyblock`).
The same surfaces are wired as DWARF scenarios (`dwarf scenario run
cardano-node-cov-<surface>-aflpp-smoke`, asserted by `aflpp_smoke_exit_clean`) and as a
two-backend definition: the same `applyblock` surface also runs **in-process under
Antithesis** via `dwarf-decoder-fuzz --target applyblock`.

The `applyblock` surface builds an initial Conway `NewEpochState` from genesis once per
process and applies a decoded tx through the full block-application STS — reaching the
real per-tx ledger validation (`ValueNotConservedUTxO`, `BadInputsUTxO`,
`StakeKeyNotRegisteredDELEG`, …), the deepest fuzz surface in the framework. Campaign
evidence (SARIF + per-surface metrics + reports) lives under `reports/`; raw fuzzer
logs under `raw/logs/`.

### Profiles, devnets, and targets

DWARF doesn't assume a fixed network — it **spins up the devnet it needs** from a
*profile*. A profile parameterizes:

- **Implementation** — Haskell `cardano-node`, Rust `amaru`, or a **mixed** devnet
  running both side-by-side (`node_type: haskell | amaru | mixed`, with independent
  `haskell_count` / `amaru_count`).
- **Network / version** — a fully local devnet (network-magic 42) or attach to a
  public network — **preview, preview2, preprod** — via an upstream peer address, so
  the same scenarios run against real-network block shapes and era boundaries.
- **Topology & consensus knobs** — `topology_pattern` (e.g. `local-mesh`),
  `shared_genesis`, `peer_sharing` on/off.

The framework ships **12 ready profiles** plus a **template system** for generating
more:

| Profile | Shape |
|---|---|
| a / b | Haskell, peer-sharing disabled / enabled |
| c | Mixed: 1 Haskell + 1 Amaru (minimal) |
| h | Generated mixed: 2 Haskell + 1 Amaru (local-mesh, shared genesis) |
| i | Generated Haskell (3 nodes) |
| d / f / e / g | Amaru / Haskell preview & preview2 proofs |
| j / k | Haskell / Amaru preprod proofs |
| l | Amaru closed devnet |

The **local devnet backend** renders a profile into a `docker-compose.yml` and brings
the devnet up on any Docker host; a separate deploy path runs it on a remote runtime
root. Because both implementations and mixed devnets are first-class, DWARF also
supports **differential testing** — feed the same adversarial input to `amaru` and
`cardano-node` and assert their validation paths agree (`replay-and-diff`).

### CLI & dashboard

Everything runs through the `cardano-profile` CLI — a broad surface including
`profile` / `list-profiles`, `scenario` / `run` (with `--backend local-devnet` or
`antithesis`), `fuzz` / `campaign`, `replay` / `replay-and-diff` / `reproduce` /
`minimize`, `coverage` / `compare` / `stats`, `snapshot` / `evidence` / `export`
(SARIF), `deploy` / `status` / `doctor`, and the `antithesis` / `moog` bridge
commands. The same code path backs a web **dashboard** (the "Operate" views: runs,
profiles, scenarios, coverage trends, crash triage, run compare/field-diff, timeline).
See `INSTALL.md` and `OPERATIONS.md`.

### Antithesis integration

[Antithesis](https://antithesis.com) is a deterministic-simulation platform: it runs the
whole system inside a hypervisor that controls scheduling and entropy, explores **many
timelines** branching from a single test, and can snapshot-and-replay any timeline to
reproduce a failure exactly. DWARF's bridge turns a fuzz scenario into an Antithesis test
so a **real node** processes structurally-mutated CBOR across thousands of explored
timelines, with the platform's autonomous search steering toward the interesting ones —
far past what a single linear fuzz run reaches.

**End-to-end flow** — from a local scenario to a launched campaign:

```bash
# 1. Render a CBOR-decode scenario into a hermetic Antithesis bundle
dwarf/cardano-profile antithesis build <profile_id> \
  --scenario dwarf/scenarios/cardano-node-cbor-tx-body-fuzz.yaml \
  --registry ghcr.io/<owner> --tag <tag> --out antithesis/cardano_node_dwarf

# 2. Validate the bundle is well-formed + self-contained
dwarf/cardano-profile moog asset validate --asset-dir antithesis/cardano_node_dwarf --json

# 3. Launch on Antithesis through the Moog requester flow (Cardano Preprod)
#    — see "Launching live runs (Moog)" below
```

The generator (`profile_manager/antithesis_generator.py`) maps each decode target to an
adversary protocol + CBOR shape. It is hard-gated to cardano-node
(`SUPPORTED_IMPLEMENTATIONS = {"cardano-node"}`, exactly one `cbor_fuzz` load primitive,
raises otherwise):

| Decode target | N2N protocol | CBOR shape | Built |
|---|---|---|---|
| block-header | chain-sync (#2) | `block-header` | ✅ |
| block | block-fetch (#3) | `block` | ✅ |
| tx-body | tx-submission2 (#4) | `tx-body` | ✅ |
| certificate | tx-submission2 (#4) | `certificate` | ✅ |
| auxiliary-data | tx-submission2 (#4) | `auxiliary-data` | ✅ |

`render_bundle()` emits a **hermetic** bundle (no external network at run time): the
Antithesis test harness (setup `sidecar` + composer `adversary` driver), the testnet
substrate (producers, relays, tracer, tx-generator), and the `dwarf-adversary` wired for
the target protocol/shape. For block-fetch it also applies a **topology eclipse**
(`_apply_eclipse`) so the node under test fetches blocks only from the adversary.

**What each timeline asserts.** The workload emits Antithesis SDK assertions the platform
checks continuously across every explored timeline:

- **no-crash** — the node never panics, aborts, or exits on mutated input;
- **liveness** — it keeps making progress (adopts blocks / drains the mempool), no hang or deadlock;
- **clean-reject** — malformed input is rejected with a structured error, never silently mis-accepted.

A violation in any timeline is a finding, reported with the exact seed + schedule needed
to reproduce it deterministically. Because the adversary defaults to `--seed random` and
Antithesis treats entropy as a per-timeline choice point, **the mutation seed-space is
explored across timelines rather than fixed** (see the adversary section below).

> **Scope — `cardano-node` only, today.** Antithesis CBOR fuzzing runs against
> `cardano-node`. Amaru remains a **local-only** target (local/mixed devnets +
> differential `replay-and-diff`). The `antithesis/amaru-single/` and
> `antithesis/mixed-haskell-amaru/` directories are early, **unvalidated scaffolding**
> (a separate `dwarf-antithesis-workload` image, never confirmed live) — not a supported
> path yet.

### The `dwarf-adversary`

A Haskell N2N peer (`antithesis/components/dwarf-adversary/`, image
`ghcr.io/j-gainsec/dwarf-adversary:0.19.0`) that speaks the real Ouroboros N2N
mini-protocols — chain-sync (#2), block-fetch (#3), tx-submission2 (#4), keep-alive
(#8) — and serves **structurally-mutated CBOR** to the node under test. It bootstraps
a valid chain (proxying an upstream producer or serving a baked corpus), reaches GSM
`CaughtUp`, then fuzzes the targeted decoder via a mutating codec.

**Per-timeline seeding (exhaustive fuzzing).** The mutation generator is
`mkStdGen(seed XOR fnv1a64(payloadBytes))`, so distinct payloads mutate differently. The
base seed comes from `--seed`, which defaults to **`random`**: the adversary draws a
fresh `Word64` from `/dev/urandom` at launch — and since Antithesis intercepts entropy
as a per-timeline choice point, **every explored timeline fuzzes from a different seed**,
so the mutation seed-space is explored rather than fixed. The drawn value is logged
(`reproduce with --seed 0x…`), and an explicit `--seed 0x<hex>` pins the RNG for
deterministic recreation.

Key flags: `--protocol {chainsync|blockfetch|txsubmission}`, `--cbor-shape {block-header|block|tx-body|certificate|auxiliary-data}`,
`--mutation-rate`, `--upstream HOST:PORT`, `--seed {random|0x<hex>|<dec>}`, `--network-magic`,
`--listen-port`, `--baked-chain FILE` (serve an embedded chain, no upstream),
`--capture-to FILE` (serialize a captured chain), `--selftest`.

### Container images

DWARF publishes **five public container images** under `ghcr.io/j-gainsec/*`
(GitHub Container Registry). They are the runtime images the Antithesis bundles pull;
each is reproducible from a Dockerfile + build script in this repo.

| Image (`ghcr.io/j-gainsec/…`) | Purpose | Built / pushed by |
|---|---|---|
| `dwarf-adversary:<tag>` | Haskell Ouroboros **N2N peer** that joins a testnet and serves structurally-mutated CBOR to the node under test. The in-process `dwarf-decoder-fuzz` binary is also baked into this image. Current `:0.19.0`; pulled by the `cardano_node_dwarf` bundle. | `antithesis/components/dwarf-adversary/build-image.sh` |
| `dwarf-decoder-fuzz:<tag>` | Standalone in-process libFuzzer-style workload over the same `applyBlock` / decode + mini-protocol codec surfaces (`--target tx\|block\|header\|…\|applyblock`). | `antithesis/components/dwarf-adversary/build-fuzz-image.sh` (`Dockerfile.fuzz`) |
| `dwarf-haskell-cov:<tag>` | Native **SanitizerCoverage-instrumented `cardano-node`** AFL++ coverage-guided harness (`dwarf-cov-run <surface> <seconds>`); cross-platform, reproducible from `coverage-docker/`. | `antithesis/components/dwarf-adversary/coverage-docker/build.sh` |
| `amaru-baked:<tag>` | Amaru node shipping a **pre-baked ledger store** (`baked-store.tgz`) so it boots at a known tip without a from-genesis rebuild — the submit-API fuzz + Haskell/Amaru differential substrate. Current `:0.2.0`. | `antithesis/amaru_baked_dwarf/Dockerfile.amaru-baked` |
| `dwarf-submit-workload:<tag>` | The **submit-API workload driver** (`driver.py`) that submits transactions to each target's `submit-api` and gates on acceptance — drives the baked differential bundle. Current `:0.2.1`. | `antithesis/amaru_baked_dwarf/` |

The Antithesis **testnet substrate** (producers, relays, tracer, tx-generator) is pulled
from public upstream registries pinned by digest in the compose files:
`ghcr.io/cardano-foundation/cardano-node-antithesis/*` and `ghcr.io/pragma-org/amaru/loader`.

The framework's own devnet node/amaru images are built locally from
`infrastructure/docker/` (see `delivery/scripts/build-image.sh`). Pulling/pushing to
`ghcr.io/j-gainsec/*` requires a GHCR credential with the appropriate scope.

### Launching live runs (Moog)

Live Antithesis campaigns are launched through **Moog**, the on-chain requester flow
(`profile_manager/moog.py`, surfaced as `cardano-profile moog …`). It handles requester
registration, the on-chain token / MPFS interaction, the Cardano Foundation oracle
hand-off, and `create-test-plan` (free dry-run) / `create-test --approve` (billed
launch) against a GitHub repo + commit + bundle directory. Results are read back from
the Antithesis tenant (triage reports / SDK assertions). Secrets (PAT, wallet, tenant
credentials) live outside the repo and are never committed.

---

## Confirmed status

**Confirmed live on Antithesis** (tenant `amaru-cardano`, `--no-faults`, 1h runs):

| CBOR shape | Path | Live assertion | Status |
|---|---|---|---|
| block-header | chain-sync | `dwarf_served_mutated_header` (decode-on-receipt) | ✅ live |
| **tx-body** | tx-submission | `dwarf_served_mutated_tx` | ✅ **PASSED** (run `0e1c9877…`, Completed 1h 12m) |
| **block** | block-fetch | `dwarf_served_mutated_block` | ✅ **PASSED** (run `ea5ad7d0…`, Completed 1h 13m) |
| certificate | tx-submission | `dwarf_served_mutated_tx` (served inside the tx) | built; not yet run live |
| auxiliary-data | tx-submission | `dwarf_served_mutated_tx` (served inside the tx) | built; not yet run live |

Both hard serve-path shapes (tx-body, block) are now proven on Antithesis: a real
`cardano-node` connects to the `dwarf-adversary`, pulls structurally-mutated CBOR, and
runs its decoder on it — with the adversary stable (no crash) and the run completing.

**Local gates** (run on the local devnet, all green):

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

**Native coverage-guided fuzzing** (`dwarf-haskell-cov`, native GHC SanCov):

- All surfaces run clean in-container with edge coverage > 0, 100% stability, 0 crashes.
- The `applyblock` surface is proven to reach the real per-tx Conway ledger rules
  (`ConwayUtxow/Utxo/Certs`), and is green through the DWARF framework (`dwarf scenario
  run cardano-node-cov-applyblock` → pass, ~20.7k edges, 0 crashes) and in-process under
  Antithesis (`dwarf-decoder-fuzz --target applyblock`).
- An **8-hour exhaustive campaign** across all 9 surfaces ran **~20.5M executions with 0
  crashes** (`applyblock` led coverage at ~28k edges). Results, SARIF, and per-surface
  metrics are under [`reports/`](reports/).

---

## Layout

```text
DWARF/
├── antithesis/
│   ├── components/dwarf-adversary/      # Haskell N2N adversary (cabal)
│   ├── cardano_node_dwarf/              # full-harness CBOR bundle (live-proven)
│   ├── cardano_node_dwarf_eclipse/      # custom-network eclipse (local-only)
│   ├── cardano_node_dwarf_baked/        # baked-corpus eclipse (local-only)
│   ├── amaru-single/                    # early scaffolding (amaru on Antithesis NOT supported)
│   └── mixed-haskell-amaru/             # early scaffolding (unvalidated, never run live)
├── dwarf/
│   ├── cardano-profile                  # CLI entrypoint
│   ├── profile_manager/                 # framework + antithesis.py + antithesis_generator.py + moog.py
│   ├── scenarios/                       # 239 scenario YAMLs (~8 families)
│   ├── primitives/                      # primitive registry + schemas
│   ├── profiles/                        # 12 profiles + templates/
│   ├── bundles/                         # run artifacts
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

**Native coverage-guided harness** (`dwarf-haskell-cov`, GHC 9.6.x + LLVM-15):

```bash
cd antithesis/components/dwarf-adversary/coverage-docker
./build.sh ghcr.io/<owner>/dwarf-haskell-cov:<tag>
# run one surface (edge-guided AFL++) for N seconds:
docker run --rm -v "$PWD/out:/out" ghcr.io/<owner>/dwarf-haskell-cov:<tag> applyblock 60
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
