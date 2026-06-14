# SP1 — cardano-node Artifact Reincorporation Design Spec

**Date:** 2026-06-10
**Status:** Drafted from brainstorming (decisions locked below)
**Part of:** the post-Phase-3b arc — "develop the local devnet alongside Antithesis" via *one definition, two backends*.

## Background

The M2 (June) delivery slimmed Dwarf's artifact set from the pre-M2 (May) package — **464 → 28 scenarios**, **109 → 10 load primitives**, **98 → 29 target manifests**, **~55 → ~3 target dirs** — while *adding* the Antithesis generator and profiles j/k/l. dwarf-v4's surviving artifacts are a **strict, byte-identical subset** of the May package (`dwarf-deploypackage-may`). The goal of this arc is to bring the removed artifacts back and finish the Antithesis generator so each definition runs on both backends (local devnet + Antithesis).

**Antithesis ingests only the Haskell cardano-node** (Amaru is not an Antithesis target). That constraint sets the sequence:

1. **SP1 (this spec)** — restore the **cardano-node** artifacts (feeds the generator; runs locally too).
2. **SP2** — finish the Antithesis generator so a *scenario* (not just a profile) emits into an Antithesis bundle.
3. **SP3** — restore the **amaru** + **differential** artifacts (local-only; the bulk; independent of Antithesis).

Each sub-project gets its own spec → plan → build cycle. This spec covers **SP1 only**.

## Goal

Additively restore the **cardano-node-implementation** scenarios removed for M2, with their full dependency closure (primitives, target manifests, target sources, registry entries), from `dwarf-deploypackage-may` into `dwarf-v4`, **validated** against the current registry/schema — without touching dwarf-v4's existing artifacts.

## Source of truth

- **From:** `/Users/nigel/dwarf-project/dwarf-deploypackage-may/dwarf/` (the richest pre-M2 set: 464 scenarios, 109 load primitives, 98 manifests, ~55 target dirs).
- **Into:** `/Users/nigel/dwarf-project/dwarf-v4/dwarf/` (origin `git.gainpalfam.com/DWARF/V4`; GitHub mirror `Cyber-Castellum/DWARF` for Antithesis).
- v4 ⊂ may for artifacts: the 28 v4 scenarios are byte-identical to may's; the 10 kept load primitives are byte-identical. So the restore is **purely additive** — no clobber risk.

## Membership (what SP1 restores)

The **~197 may-delta scenarios** whose `target.implementation == "cardano-node"` (or are unambiguously cardano-node by name), spanning these families:

- `cardano-node-cbor-*` (CBOR serdes fuzz: block, header, tx-body, certificate, auxiliary-data — structured + unstructured)
- `cardano-node-mini-protocol-*-fuzz` (chainsync, blockfetch, handshake, keep-alive, peersharing, txsubmission)
- `runtime-substrate-*` (the large family: serdes shape-rejection, snapshot/checkpoint, stateful local protocols, txsubmission pressure, large 10/20/30/50/100-node meshes, plutus phase-2, resource/network impairment, eclipse/sybil/byzantine, era-transition, compound faults)
- `m3-runtime-*`, `phase1-runtime-*`, `phase3-runtime-*` (haskell baselines, capability demos, profile-a runtime, node partition/recovery)
- `runtime-bundle-*` (attestation, chain-verify, diff, export-sarif, summary, timeline), `cardano-lsq-extract-*`

**Deferred to SP3 (not in SP1):** the **70 differential** scenarios (they need amaru targets) and the **169 amaru** scenarios. **Reclassification rule:** if a scenario classified "cardano-node" turns out to reference an amaru target/primitive in its dependency closure, move it to SP3.

**Dependency closure (also restored):** for the restored scenarios only — the referenced `load`/`assertion`/`probe`/`teardown` primitive schemas, the referenced target **manifests** and target **source dirs**, and the matching **`registry.json`** entries.

## Architecture / files

| Path | Action |
|------|--------|
| `dwarf/scenarios/*.yaml` | **add** the ~197 cardano-node scenarios (additive; existing 28 untouched) |
| `dwarf/primitives/{load,assertion,probe,teardown}/*.schema.json` | **add** the primitives referenced by the restored scenarios (delta only) |
| `dwarf/primitives/registry.json` | **merge** (additive union; on id conflict, keep v4's entry and flag) |
| `dwarf/targets/<target-name>/` | **add** the target source dirs the restored scenarios reference |
| `dwarf/targets/manifests/*` | **add** the manifests the restored scenarios reference |

No changes to: `dwarf/profiles/` (v4 superset already), `dwarf/profile_manager/` code (the validator/executor already exist), the Antithesis generator (SP2).

## Approach — dependency-layered, additive (each layer one commit)

1. **Compute membership + closure.** Parse the may-delta scenarios; select `target.implementation == "cardano-node"`; for each, collect referenced primitive ids (`load[].primitive`, `assert[].*`, etc.) and target ids/manifests. Produce three lists: scenarios, primitives, targets+manifests. Drop any whose closure pulls in an amaru target (→ SP3 list).
2. **Layer 1 — primitives + registry.** Copy the delta primitive schemas; merge their `registry.json` entries (additive). Validate: registry parses, every restored primitive id resolves to a present schema.
3. **Layer 2 — targets + manifests.** Copy the referenced target source dirs + manifests. Validate: every target/manifest id referenced by the scenario set now resolves.
4. **Layer 3 — scenarios.** Copy the ~197 scenarios. Run `cardano-profile scenario validate --semantic --registry-path <registry>` across all restored scenarios. Validate: 0 failures, 0 unresolved references.
5. **Spot-run.** Execute 1–2 restored cardano-node scenarios locally (e.g. a `cardano-node-cbor-*` and a `runtime-substrate-serdes-*`) to confirm the existing executor still drives them end-to-end.

## Validation gate (success criteria)

- Every restored scenario **passes `scenario validate --semantic`** against the merged registry.
- Every primitive/target/manifest **reference resolves** (0 dangling refs) across the union of restored + existing artifacts.
- v4's pre-existing 28 scenarios / 10 primitives / registry entries are **unchanged** (additive-only diff).
- Membership count is reconciled: restored cardano-node scenarios + deferred (amaru + differential) = 436 may-delta (accounts for every removed scenario).
- 1–2 restored scenarios **execute** locally without executor errors.

## Out of scope

- Profiles (v4 already has all of may's + j/k/l).
- The Antithesis generator (SP2).
- amaru + differential artifacts (SP3).
- **Building/running** the cardano-node fuzz targets (Rust/Haskell harnesses) — build-on-demand, validated separately; SP1 gates on *definitions*, not compilation.

## Risks & mitigations

- **`registry.json` merge format** — confirm its exact structure (it isn't a flat dict-of-lists); write the merge to be additive and conflict-flagging. *Mitigation:* inspect the file first; unit-test the merge.
- **Hidden amaru deps** — a "cardano-node" scenario whose closure needs an amaru target. *Mitigation:* the closure step detects this and reclassifies to SP3.
- **Schema drift** — appears minimal (kept artifacts byte-identical), but a restored scenario may use a field the current validator rejects. *Mitigation:* validate per layer; fix drift inline (small, expected rare).
- **Volume** — ~197 scenarios + closure is large but mechanical; the layered approach keeps each commit reviewable.

## Prerequisites

- `dwarf-deploypackage-may` present locally (it is).
- The `cardano-profile scenario validate --semantic --registry-path` command (exists in dwarf-v4).
