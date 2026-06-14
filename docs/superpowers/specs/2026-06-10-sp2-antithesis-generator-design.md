# SP2 — Antithesis Native Test Generator (cardano-node)

> Design spec. Status: approved (brainstorming gate). Date: 2026-06-10.
> Predecessors: SP1 (cardano-node scenario reincorporation), Stage 1 (local foundation
> — shims built, behavioral `scenario verify` gate). Successor: SP3 (amaru + differential).

## Goal

A user says "I want to run this DWARF scenario" and picks a backend:

- **`local-devnet`** — DWARF's existing executor (run bundle, assertions, SARIF, evidence). Unchanged.
- **`antithesis`** — DWARF *generates everything needed* to produce a **native Antithesis test**
  from the scenario (even a freshly pasted YAML/profile/primitive), then uses **Moog
  (`create-test`)** to run it on Antithesis.

The two backends **overlap** on the same scenario / target / properties, but Antithesis runs as
a **native** test: idiomatic SDK assertions, `antithesis_random` seeding, the composer
test-template layout, fault-exclusion labels, and public hermetic images. The native workload
**reuses DWARF's fuzz logic** (shared mutation-kind set + seed policy) so its coverage overlaps
the local executor's corpus on the same scenario.

## Scope

- **cardano-node only.** Antithesis supports the Haskell cardano-node, not Amaru. Amaru and
  differential scenarios are **SP3** (the generator refuses them with a clear error, never a
  silent placeholder).
- **First-generation scenarios:** the 10 green `cardano-node-cbor-*-fuzz[-structured]` scenarios
  (Stage 1), which already exercise the built decode shims locally.
- **Archetype being generalized:** the hand-built `antithesis/cardano_node_dwarf` testnet +
  `antithesis/components/dwarf-adversary` (a chain-sync upstream server that serves seeded,
  structurally-mutated header CBOR to a live node, emitting SDK assertions). SP2 turns this
  one hand-built test into a *generated* one driven by a scenario.

## Why the current generator can't do this

`profile_manager/antithesis.py` is **profile-driven**: `render_compose(profile, ...)` emits node
services plus a generic `workload` service whose command is a placeholder
(`/antithesis/setup-complete.sh`). There is **no scenario→bundle path** and no native workload —
the produced bundle does not fuzz anything. SP2 adds the scenario→native-test path.

## The native-test shape (what "generate everything needed" produces)

A bundle directory laid out as the Cardano Foundation testnet + Dwarf adversary + composer:

```
<bundle>/
  docker-compose.yaml        # CF cardano-node testnet (configurator, p1..p3, relays,
                             #   tracer, sidecars) + the dwarf-adversary service +
                             #   exclude_from_faults labels on harness/infra services
  testnet.yaml               # genesis/topology params (from the testnet base)
  relay-dwarf-topology.json  # wires a relay to chain-sync FROM the adversary
  test/v1/                   # composer scripts: first_*, parallel_driver_*, finally_*
  dwarf-manifest.json        # provenance: scenario id, seed policy, assertion map, fault labels
  README.md                  # no secrets; how it was generated
```

Native conventions honored (pinned in `antithesis_conventions.py`):

- **SDK:** Fallback-SDK NDJSON `antithesis_assert` to `sdk.jsonl`. **`Sometimes` / `Reachable`
  only — never `Always`** (the harness can chaos-kill the workload, so `Always` would
  false-fail). `setup_complete` is emitted once by the testnet's setup step.
- **Determinism:** `antithesis_random` supplies the adversary `--seed` at launch → any finding is
  reproducible from the seed alone. No clock / `/dev/urandom` in the fuzz path.
- **Composer:** test commands live under `/opt/antithesis/test/v1/` (`first_`, `parallel_driver_`,
  `finally_`); `parallel_driver_` must not emit `setup_complete`.
- **Faults:** `com.antithesis.exclude_from_faults: "network,kill,pause,stop"` on the adversary and
  the tracer/sidecar/log infra — the harness should perturb the *node*, not the test rig.
- **Images:** public, registry-pinned refs; hermetic (pulled only at launch).

## Components / file structure

1. **Backend selector** — `cli.py` + `scenario.py`.
   `cardano-profile scenario run <scenario> --backend {local-devnet|antithesis}`
   (default `local-devnet`). `local-devnet` → current `run_scenario` (unchanged). `antithesis` →
   `generate_native_test` → `verify_generated_bundle` (gate) → optional `moog create-test`.

2. **Scenario→bundle generator** — new module `profile_manager/antithesis_generator.py`.
   `antithesis.py` keeps its profile-driven primitives; the generator orchestrates scenario →
   native test. Pure, dependency-free (string-built YAML, like the rest of the package).
   - `select_testnet_base(scenario)` — picks the cardano-node testnet skeleton (the
     `cardano_node_dwarf` layout) by `target.implementation`.
   - `derive_adversary(scenario)` — maps `scenario.load[]` (cbor-fuzz primitive + params:
     target CBOR shape, mutation rate) + `scenario.seed` → the adversary service: image,
     CLI args (`--protocol`, `--cbor-shape`, `--network-magic`, `--listen-port`,
     `--mutation-rate`, `--upstream`, `--seed`), and topology wiring (the adversary listed as a
     trustable local root so the node syncs from it). `--seed` is wired from `antithesis_random`
     at launch, not baked.
   - `map_assertions(scenario.assertions)` — DWARF assertions → native SDK assertion IDs
     (`Sometimes`/`Reachable`). Produces the catalog the workload fires; zero assertions = error.
   - `render_composer_scripts(scenario)` — `first_`/`parallel_driver_`/`finally_` scripts.
   - `render_bundle(...)` — assembles compose + adversary + topology + composer + `README.md` +
     `dwarf-manifest.json`.

3. **Shared scenario descriptor (the "overlap")** — `fuzz_spec(scenario)` returns a small
   descriptor that **both** backends consume: `{target_decoder, cbor_shape, seed, mutation_rate,
   asserted_properties}`. The overlap the two backends guarantee is **same target decoder + same
   asserted property + same seed discipline** — NOT an identical mutation engine. This is a
   deliberate, honest distinction:
   - Local (`cbor_fuzz_structured`): `generate_cbor(shape)` + **byte-level** `mutate_cbor`
     (random byte flips), fed to the built shim decoder.
   - Native adversary (`dwarf-adversary`): **Term-level structural** `mutateTerm`
     (`swapMajorType`, `truncateCollection`, `extendCollection`, `perturbInt`, `flipIndefinite`,
     `nestOnce`) applied to a captured real header, served over chain-sync to a live node.

   The two mutation engines differ on purpose (the structural engine is the higher-value native
   test). What `fuzz_spec` pins is that both attack the **same decoder** under the **same property**
   from the **same seed source**, so a finding on one is meaningful to the other. No parity-of-kinds
   test is asserted, because the kinds genuinely differ.

4. **Stage-2 verification** — `verify_generated_bundle(bundle_dir)`: compose parses; every image
   is a registry ref (**no `build:` contexts**); fault-exclusion labels present on the harness
   services; topology resolves (adversary listed as a relay root); composer scripts exist, are
   executable, non-empty, and `parallel_driver_` emits no `setup_complete`; **every scenario
   assertion maps to an emitted SDK id**; at least one assertion exists. A `docker compose config`
   lint runs where Docker is available (cardano-box). Gate: green before any submission —
   mirrors the Stage-1 anti-false-green discipline.

5. **Moog launch** — reuse the existing `moog create-test-plan` / `preflight` / `create-test`
   path. The generator writes the bundle into the Moog test-asset directory layout so those
   commands consume it unchanged. No new launch code; SP2 only feeds the existing path a real,
   verified bundle.

## Data flow

```
scenario.yaml
  → semantic_validate_scenario (registry-checked; refuses unregistered primitives by name)
  → [backend=local-devnet] run_scenario                       (unchanged)
  → [backend=antithesis]
       generate_native_test(scenario)        → bundle dir
       verify_generated_bundle(bundle dir)    → Stage-2 gate (must be green)
       image build/push (image_push_commands, extended for adversary + testnet)
       moog create-test                       → Antithesis run → results
```

## Generalization of the adversary across CBOR shapes

The proven archetype covers **block-header over chain-sync**. The other Stage-1 shapes
(block, tx-body, certificate, auxiliary-data) each need the adversary to serve the matching
mini-protocol with the matching mutated CBOR shape. The design makes this **additive**: the
adversary gains `--protocol`/`--cbor-shape`, and the generator maps `scenario.target` +
`scenario.load[]` → `(protocol, shape)`. **SP2 delivers the generator framework + the
header/chain-sync path end-to-end** (generation → Stage-2 verify → launch), with each
additional protocol/shape an additive adversary build task — not a redesign. This cap is
surfaced explicitly (no silent truncation): the generator emits a clear "shape `X` requires the
`<protocol>` adversary mode (follow-on build)" error for shapes whose adversary mode isn't built
yet, rather than generating a non-fuzzing bundle.

## Error handling

- Unsupported `target.implementation` (amaru/differential) → refuse with an SP3 message.
- Unregistered primitive in a pasted scenario → `semantic_validate` names the registry gap.
- Zero / unmapped assertions → Stage-2 gate fails (anti-false-green).
- Unbuilt adversary protocol mode → clear follow-on-build error, never a silent placeholder.

## Testing

- **Unit:** generator maps each of the 10 cbor-fuzz scenarios → bundle with the expected services,
  fault labels, adversary args, and assertion catalog.
- **Negative:** `verify_generated_bundle` catches a missing fault label, a `build:` context, an
  unmapped assertion, zero assertions, a `parallel_driver_` that emits `setup_complete`.
- **Overlap:** `fuzz_spec(scenario)` for a cbor-fuzz scenario yields the same `target_decoder`,
  `cbor_shape`, `seed`, and `asserted_properties` that the local executor consumes — proving both
  backends attack the same decoder/property/seed (mutation engines differ by design).
- **Round-trip (cardano-box):** generate the header-path scenario → `verify_generated_bundle`
  green → `docker compose config` parses → `moog create-test-plan`/`preflight` accept the bundle.

## Out of scope (SP2)

- Amaru + differential scenarios and their adversary modes (SP3).
- Adversary protocol modes beyond chain-sync/header (additive follow-on builds).
- Result-retrieval / triage automation from Antithesis (separate Stage-3 verification track).
