---
sut_path: /Users/nigel/dwarf-project/dwarf-v4/antithesis/cardano_amaru_adversarial
commit: b2ed2450ebb95e460e7c64f0d2578102be4d7663
updated: 2026-08-22
external_references:
  - path: https://github.com/pragma-org/amaru/wiki
    why: Amaru architecture, operating model, and documented limitations.
  - path: https://bench.gainpalfam.com/wb/moog
    why: Project decisions, MOOG deployment state, and prior Antithesis handoffs.
  - path: /Users/nigel/dwarf-project/dwarf-v4/dwarf/docs
    why: DWARF finding reports and prior Amaru analysis.
  - path: /Users/nigel/dwarf-project/dwarf-v4/reports
    why: Evidence bundles for previously exercised Amaru failure classes.
  - path: https://github.com/pragma-org/amaru/issues/1265
    why: Known reconnect/resume behavior excluded from this scenario.
  - path: https://github.com/pragma-org/amaru/issues/1156
    why: Known local transaction diffusion behavior excluded from this scenario.
  - path: https://github.com/pragma-org/amaru/issues/1004
    why: Known phase-2 behavior excluded from this phase-1 scenario.
---

# SUT Analysis

## Scope

This is a targeted analysis of the mixed admission path in
`cardano_amaru_adversarial`, not a fresh claim to cover every Amaru or Cardano
subsystem. The user explicitly asked that the Amaru wiki, project notes, earlier
findings, and reports be checked so the new run does not repeat known work.

## Architecture and data flow

The existing three-producer Cardano cluster plus two relays remains for legacy mixed
properties. The phase-1 semantic reference is a separate baked Cardano node at block
216, paired to the exact synthetic chain used by Amaru's baked store.
`cardano-submit-api` exposes that reference node's local submission decision.

The comparison side is Amaru 10.11.20260807 with a baked `testnet_42` ledger/chain
store. `amaru-relay-1` has an honest Cardano peer plus the DWARF block-fetch
adversary and is faultable. Its HTTP submit API accepts raw transaction CBOR. The
mixed workload POSTs one immutable payload to Amaru and Cardano and compares the
endpoint decisions.

The workload image contains a static, correctly signed Conway transaction at
`minimum fee - 1 lovelace` plus non-sensitive metadata. It contains no signing key.
The matching key was recovered temporarily only to generate this fixture and was
discarded before image publication.

## State management and persistence

The phase-1 Cardano state initializes a named volume from a public baked image. Amaru
starts from the matching baked chain and ledger stores copied into relay-specific
writable volumes.
The signed fixture is immutable and invalid, so repeated submission should never
consume its input. This makes replay idempotent if both implementations reject it.

Live testing proved that a fresh configurator UTxO does not exist in Amaru's store:
Amaru fails during preparation. The paired baked Cardano snapshot fixes that boundary.
Preparation failures and `BadInputsUTxO` remain non-evidence and are never classified
as the target result.

## Concurrency and faults

Antithesis may kill, pause, stop, or partition Amaru while test commands execute.
The baked Cardano reference, submit API, sidecars, adversary, and workload are
explicitly excluded from faults; Cardano therefore acts as a stable semantic
control. Each composer invocation can overlap with a different Amaru lifecycle
state. Transport resets and timeouts are expected observations and remain
inconclusive.

## Claimed safety and liveness guarantees

- A correctly signed transaction below the minimum fee is not admitted.
- When both endpoints give classifiable ledger responses for the identical bytes,
  their phase-1 fee decision agrees.
- After fault injection stops, Amaru eventually resumes returning a classifiable
  phase-1 rejection for the same idempotent fixture.
- The exact payload used by both endpoints does not change within an observation.

## Failure-prone areas

- response classification can turn unrelated HTTP failures into false findings;
- fee calculation can be off by a CBOR-size fixed point unless recalculated;
- a valid transaction would be non-idempotent across two separate mempools, so this
  first scenario deliberately uses an invalid boundary transaction;
- state-provenance mismatch can prevent the intended validation branch;
- SDK diagnostic stdout can contaminate the adversary seed unless numeric lines are
  selected explicitly;
- Amaru restart or network disruption can look like rejection unless transport and
  ledger outcomes are separated.

## Prior findings excluded from novelty

Restart rollback, reconnect/resume, local diffusion, invalid phase-2 processing,
transaction array arity, trailing bytes, and other decoder-shape findings already
have upstream issues or DWARF reports. They remain useful regression context but are
not the question this property claims to answer.

## Assumptions

- The checked-in Cardano snapshot and baked Amaru store remain paired artifacts.
- Both implementations use equivalent Conway minimum-fee parameters.
- The existing sidecar path emits Antithesis `setup_complete` before commands run.

## Resolved question

Live same-byte submission proves both stores contain the input and both reach
validation. The remaining gate is the public MOOG/Antithesis run, not reachability.
