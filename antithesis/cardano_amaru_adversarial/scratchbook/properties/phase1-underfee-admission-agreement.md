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

## Proven live observation

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

## Relevant paths

- `fixture/static/`: immutable signed envelope and fee metadata; no key material.
- `reference-image/`: matching public Cardano chain/config snapshot.
- `workload/mixed_phase1.py`: classification, exact-byte submission, and assertions.
- `workload/test/v1/mixed-phase1/`: driver and eventual commands.
- `docker-compose.yaml`: stable Cardano reference and faultable Amaru target.

## Safety semantics

- Acceptance by either implementation always fails safety.
- A transport failure is inconclusive, not disagreement.
- A preparation failure is `unknown`, not phase-1 rejection.
- Dual classifiability and eventual agreement are separately required to prevent a
  vacuous pass.

## Verification status

- 22 unit/contract tests pass.
- Same-byte packaged-image smoke passes on `cardano-box`.
- Official `snouty` 0.6.1 validates the published digest-pinned bundle and discovers one
  driver and one eventual command.
- Anonymous manifest retrieval returns HTTP 200 for all new images.
- Paid MOOG/Antithesis execution is pending the public commit.

## Value menu

This first property intentionally uses only fee delta `-1`. Later `0`, `+1`,
size-transition, minimum-output, validity-interval, and value-conservation cases must
be separately cataloged so triage remains unambiguous.
