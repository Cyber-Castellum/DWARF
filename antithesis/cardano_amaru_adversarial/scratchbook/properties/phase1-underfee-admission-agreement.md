# phase1-underfee-admission-agreement Evidence

## Why this property is new

Earlier DWARF transaction differentials covered decoder shape and unfunded semantic
paths; reports and Amaru issues also cover fork/rollback, reconnect, local diffusion,
and phase-2 behavior. No reviewed artifact established cross-implementation agreement
at the exact phase-1 minimum-fee boundary with a funded input common to both ledgers.

## State-provenance incident and resolution

A fresh configurator run produces new genesis keys. Amaru's image starts from an older
baked store, so a transaction signed from the fresh `genesis.1` UTxO is unknown to
Amaru. Live evidence showed:

- Cardano: `FeeTooSmallUTxO`, proving fee-rule reachability;
- Amaru: `failed to prepare transaction ... for validation`, proving the input could
  not be hydrated and validation was never reached.

The correct design pairs Amaru's baked store with the retained Cardano block-216
snapshot from the same synthetic chain. Querying that snapshot proved the shared
genesis UTxO exists with 200,000,000,000,000 lovelace. A matching signing key was
recovered temporarily, used once, and discarded; only the signed invalid transaction
and public metadata are shipped.

## Proven live observation and completed baseline

Transaction id:
`40b279fb4ce5cf95e4def7b5c10086937a7b4ee8c3f72fd8c0761b0aceaccc17`

| Field | Observation |
|---|---|
| Cardano minimum fee | `164181` |
| Fixture fee | `164180` |
| Cardano response | HTTP 400, `FeeTooSmallUTxO`, supplied 164180, expected 164181 |
| Amaru response | HTTP 400, `transaction <txid> is invalid` |
| Classification | both `phase1_reject`; agreement true; no acceptance |

Source tracing distinguishes Amaru's generic response: `TransactionValidationError::Preparation`
formats `failed to prepare ... for validation`, while the observed `transaction ... is
invalid` comes from `TransactionValidationError::Validation`. Because the fixture has
one controlled defect, this is sufficient phase-1 rejection evidence without falsely
claiming Amaru exposed a fee-specific reason.

The replacement Antithesis run at commit
`6082f0eedabe478801051a661435a5ffb3424f47` completed as run
`0195e57647d3ee8322893e6f8af159a5-59-13` (MOOG test-run
`f1334b7d8425d76f219d62dd0c01f462e35c6faf9f987b11f6b86c2ad83a9bd7`).
The original `-1` mixed safety properties each passed 155,608 observations;
paired classifiability was reached 148,003 times and recovery passed in 3,775
examples. No mixed admission issue surfaced. The run was 43/44 overall solely
because the known Cardano `cluster fork depth < k` property failed.

## Extended signed corpus

| Case | Fee delta | Expected | Replay model |
|---|---:|---|---|
| `underfee-minus-100` | -100 | phase-1 rejection | randomized under faults |
| `underfee-minus-2` | -2 | phase-1 rejection | randomized under faults |
| `underfee-minus-1` | -1 | phase-1 rejection | historical driver plus randomized menu |
| `minimum-exact` | 0 | acceptance | once, before faults |
| `minimum-plus-1` | +1 | acceptance | once, before faults |

All five have independently checked transaction IDs, inputs, fees, and witnesses.
An isolated Cardano reference accepted the exact and `+1` transactions with HTTP
202 and rejected all negative cases with the precise `FeeTooSmallUTxO` mismatch.
The accepted cases spend distinct retained genesis inputs.

## New finding: Amaru mempool charges one extra fee byte

The full mixed-stack preflight on Amaru `v10.11.20260807` found a deterministic
disagreement before a paid corpus run was submitted:

| Case | Fee | Cardano | Amaru |
|---|---:|---|---|
| `minimum-exact` | 164181 | HTTP 202 accepted | HTTP 400 validation reject |
| `minimum-plus-1` | 164182 | HTTP 202 accepted | HTTP 400 validation reject |
| isolated threshold probe | 164225 | HTTP 202 accepted | HTTP 202 accepted |

Every signed envelope is 201 bytes. Cardano's ledger fee size is 200 bytes,
excluding the standalone transaction's one-byte `is_valid` field. With
`min_fee_a = 44`, Amaru's observed acceptance threshold is exactly
`164181 + 44 = 164225`.

The implementation explains the result. In Amaru commit
`493bffba0cc4db2291643cdd6698197c374958b3`, mempool validation passes
`to_cbor(transaction).len()` from `crates/amaru-ledger/src/state.rs` to the fee
rule. Amaru's own phase-one fixtures and ledger-state evaluator instead subtract
one byte and explicitly document that the ledger transaction excludes
`is_valid`. The full-length mempool calculation was introduced by PR #788. A
search of current Amaru issues and pull requests found no existing report of
this specific discrepancy.

This is not a preparation/input-hydration artifact: Amaru logged validation-layer
rejections for exact and `+1`, then accepted the isolated `+44` probe from the
same retained chain state. The temporary signing material used for that probe
was removed immediately after submission.

## Relevant paths

- `fixture/static/`: immutable signed envelopes, manifest, and public fee metadata;
  no key material.
- `fixture/verify-corpus-container.sh`: one-command verifier using a pinned
  Cardano configurator image; it recomputes CBOR hash/size, transaction ID, era,
  fee, input, witness shape, uniqueness, and replay contracts.
- `reference-image/`: matching public Cardano chain/config snapshot.
- `workload/mixed_phase1.py`: classification, exact-byte submission, and assertions.
- `workload/test/v1/mixed-phase1/`: driver and eventual commands.
- `docker-compose.yaml`: stable Cardano reference and faultable Amaru target.

## Safety semantics

- Acceptance of any negative case always fails safety.
- A classifiable result that differs from the case expectation always fails.
- A transport failure is inconclusive, not disagreement.
- A preparation failure is `unknown`, not phase-1 rejection.
- Dual classifiability and eventual agreement are separately required to prevent a
  vacuous pass.

## Verification status

- 48 unit/contract tests pass for the corpus-aware workload.
- The workload image uses a digest-pinned Python base and Antithesis Python SDK 0.2.0.
- The structural verifier passes all five signed transactions.
- Local command smoke confirms bounded zero-exit behavior when endpoints are unavailable.
- The original `-1` Antithesis run completed successfully for all mixed properties.
- The hardened image is published and digest-pinned at
  `sha256:26d02ae22e0d802b78285a3b19bd53687a257dd77a12879831b5262a4792811d`.
- Official `snouty validate` passes and discovers 1 first, 2 driver, and 1 eventual
  command. The public commit and any paid corpus run remain pending.

## Value menu

The fee menu `{-100,-2,-1,0,+1}` already produced a new deterministic finding.
An Antithesis run can formally capture the per-case failure and explore its
interaction with faults, but more nearby deltas are low-yield. After that, move
to a different phase-1 rule or a serialized-size transition.
