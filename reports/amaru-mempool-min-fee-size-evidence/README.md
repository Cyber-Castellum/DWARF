# Amaru mempool minimum-fee size differential

## Finding

Amaru `v10.11.20260807` rejects valid Conway transactions whose fee is valid by
Cardano ledger rules but is less than an Amaru-only one-byte surcharge. With the
test network's `min_fee_a = 44`, Amaru's mempool acceptance threshold is exactly
44 lovelace above Cardano's.

This was found during mixed Cardano/Amaru Antithesis preflight on 2026-08-22. No
paid five-case corpus run had been submitted when the issue was reproduced.

## Reproduction matrix

All transactions are correctly signed, use retained inputs present in both
implementations, and have 201-byte standalone Conway envelopes.

| Transaction | Fee | Cardano submit API | Amaru submit API |
|---|---:|---|---|
| `60dffadd16741960c6dc5296bac99db6f84cfe64f5c826a498cbb943a66f0c36` | 164181 (minimum) | HTTP 202 | HTTP 400 validation reject |
| `81222acadad135fb60c19cc0571a22ab8421d44e2e78d7927bd7e0baae07969d` | 164182 (minimum + 1) | HTTP 202 | HTTP 400 validation reject |
| `99d12da5eb1afc5ef2255b4fe07b1dc2bdfa2d5c79ebc6c0e660fee912f1253d` | 164225 (minimum + 44) | HTTP 202 | HTTP 202 |

Amaru logs identify the first two as validation-layer mempool rejections and the
third as accepted into the mempool. The `+44` transaction was an isolated
diagnostic probe. Its signed public envelope is retained as
`minimum-plus-44.tx`; its temporary signing key, body, and build directory were
deleted immediately after generation.

## Root cause evidence

The tested release is Amaru commit
`493bffba0cc4db2291643cdd6698197c374958b3`.

- `crates/amaru-ledger/src/state.rs:691` calculates the mempool validation size
  with `to_cbor(transaction).len()` and passes it to phase-one validation.
- `crates/amaru-ledger/src/rules/transaction/phase_one/mod.rs:382-385` states
  that standalone fixtures include the one-byte `is_valid` field while the
  ledger transaction does not, and therefore subtracts one byte.
- `crates/amaru-ledger/tests/evaluate_ledger_states.rs:331-337` documents and
  applies the same subtraction.
- The fee rule is `min_fee_a * tx_size + min_fee_b`; this network uses
  `min_fee_a = 44` and `min_fee_b = 155381`.

Therefore:

```text
Cardano ledger minimum: 155381 + 44 * 200 = 164181
Amaru mempool minimum:  155381 + 44 * 201 = 164225
```

The full-length mempool validation call was introduced by Amaru PR #788 in May
2026. A current search of Amaru issues and pull requests found no report of this
specific minimum-fee discrepancy.

Stable source links and sanitized excerpts are recorded in `source-excerpts.txt`.

## Harness integrity

The public corpus contains no signing keys. `fixture/verify-corpus-container.sh`
uses a digest-pinned Cardano image to check each envelope's SHA-256 and size,
recompute its transaction ID, inspect its Conway era, signed fee, input, and
single witness, and enforce the negative-case replay and accepted-case uniqueness
contracts.

The hardened Antithesis workload gives readiness and every individual case a
distinct fixed assertion identity. As a result, a missing case cannot be hidden
by aggregate observations from another corpus member.

## Evidence files

- `responses.json`: sanitized endpoint statuses, bodies, and classifications.
- `amaru-mempool.log`: the two rejections and threshold acceptance from Amaru.
- `environment.json`: exact commits, image references, network, and fee parameters.
- `minimum-plus-44.tx`: signed public threshold probe; no signing material.
- `verification.txt`: corpus verifier output and independent probe inspection.
- `source-excerpts.txt`: relevant source excerpts and stable commit/PR links.
