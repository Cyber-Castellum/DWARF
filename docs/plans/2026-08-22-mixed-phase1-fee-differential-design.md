# Mixed Cardano/Amaru Phase-1 Fee Differential Design

**Status:** implemented and locally validated on `cardano-box` on 2026-08-22; public commit and MOOG launch remain.

## Goal

Submit one identical, correctly signed Conway transaction whose fee is exactly one
lovelace below Cardano's calculated minimum to Cardano node and Amaru. Neither may
accept it, and a ledger disagreement must never be inferred from a transport outage
or an earlier preparation failure.

This is intentionally distinct from the already well-covered decoder, rollback,
fork, reconnect, local-diffusion, and phase-2 scenarios in `reports/`, `dwarf/docs/`,
the Amaru wiki, and tracked Amaru issues.

## Critical state-provenance rule

The original design generated a fresh testnet and signed from its `genesis.1` UTxO.
That design is invalid for the baked Amaru image: the configurator creates new genesis
keys on every run, while Amaru starts from a previously baked store. The transaction
therefore reached Cardano's fee rule but failed Amaru preparation with an unresolved
input. That is missing reachability, not a phase-1 differential.

The final design uses two artifacts derived from the same public synthetic testnet
state that produced the baked Amaru store:

- a 544 KiB Cardano chain snapshot at block 216, including the two-byte network-magic
  database marker and matching genesis/configuration files;
- a 493-byte statically signed transaction envelope plus public fee metadata.

The recovered genesis signing key was used once in a mode-700 temporary directory on
`cardano-box` and discarded. It is not in Git, the build contexts, the images, Compose,
or the fixture. A signed invalid testnet transaction is public data and cannot reveal
the signing key.

## Runtime topology

```text
workload image (immutable CBOR)
  |-- POST application/cbor --> cardano-submit-api --> baked Cardano reference socket
  `-- POST application/cbor --> Amaru submit API --> baked Amaru ledger
```

`cardano-phase1-reference` initializes a named volume from its image and runs with an
empty topology, preserving the shared UTxO. `cardano-submit-api`, the reference node,
and the workload are excluded from Antithesis faults; the Amaru target remains
faultable. The broader mixed topology stays present for its existing properties.

## Classification contract

Responses are classified as `accepted`, `phase1_reject`, `decode_reject`,
`unavailable`, or `unknown`.

- Cardano's `FeeTooSmallUTxO` is `phase1_reject`.
- Amaru's exact `transaction <64 hex> is invalid` response is `phase1_reject`; source
  tracing and live submission verify this is `TransactionValidationError::Validation`.
- Amaru's `failed to prepare transaction ... for validation` remains `unknown`.
- Network and timeout failures are `unavailable` and therefore inconclusive.
- Any observed acceptance is an unconditional safety failure, even if the peer is down.

Reachability and `Sometimes` assertions prevent a vacuous green result when one side
never becomes classifiable.

## Published images

- `ghcr.io/j-gainsec/dwarf-cardano-phase1-reference@sha256:cadd549396712c649f6f5683fab36fa6c183b8db0f85440a82a05b59bbcb39e4`
- `ghcr.io/j-gainsec/dwarf-mixed-phase1-workload@sha256:31dc030ed0cd5884fa36ce0b230609167678f44ca55d565dc4169dafb018a0a1`
- `ghcr.io/j-gainsec/dwarf-adversary-anti@sha256:e99cb81ffc51465042b77ac3100d18092f69a25c36c66fff4954663b7200d2bd`

The adversary image includes a related validation fix: Antithesis SDK diagnostics can
precede the random seed on stdout, so the wrapper now accepts only a complete unsigned
decimal line. Without this, local `snouty validate` crash-looped the adversary.

## Evidence

Live same-byte smoke result for transaction
`40b279fb4ce5cf95e4def7b5c10086937a7b4ee8c3f72fd8c0761b0aceaccc17`:

- metadata: minimum `164181`, actual `164180`;
- Cardano: HTTP 400, `FeeTooSmallUTxO`, supplied `164180`, expected `164181`;
- Amaru: HTTP 400, `transaction ... is invalid` from the validation layer;
- result: `both_classifiable=true`, `phase1_agreement=true`, `any_accepted=false`.

The 22-test unit/contract suite passes. Official `snouty` 0.6.1 validation against
the published digest-pinned images detected setup-complete and found one driver plus
one eventual command. Anonymous manifest retrieval returns HTTP 200 for all three new
digests.

## Remaining launch gate

Commit and push this exact public-safe bundle to `Cyber-Castellum/DWARF`, then use the
release MOOG CLI (never `moog-head`) to create the test in the `amaru-cardano` tenant.
Record the public commit, MOOG request/test identifiers, Antithesis run id, and final
triage outcome in the runbook and workbench note.
