# Mixed Cardano/Amaru Phase-1 Fee Corpus Design

**Status:** approved on 2026-08-22; implementation pending.

## Goal

Turn the proven one-lovelace-under-minimum differential into a small semantic
fee-boundary corpus without weakening state provenance, replay safety, or public
repository hygiene. Cardano node remains the stable Haskell reference and Amaru
remains the faultable Rust target.

## Why this is the next property

The completed Antithesis run proved that the paired stores, dual-submit transport,
classification logic, and post-fault recovery path work. It did not vary the
transaction input. The next property stays in the already-reachable minimum-fee
validation path and adds the smallest useful configured-limit family around the
runtime minimum: well below, just below, exactly at, and just above.

This remains distinct from prior decoder, witness, phase-2, rollback, fork,
reconnect, and diffusion campaigns.

## Corpus and state isolation

Five correctly signed Conway transactions use three independent funded inputs from
the same retained block-216 synthetic state:

| Case | Fee delta from calculated minimum | Input | Expected result |
|---|---:|---|---|
| `underfee-minus-100` | `-100` | genesis input 1 | both reject at phase 1 |
| `underfee-minus-2` | `-2` | genesis input 1 | both reject at phase 1 |
| `underfee-minus-1` | `-1` | genesis input 1 | both reject at phase 1 |
| `minimum-exact` | `0` | genesis input 2 | both accept |
| `minimum-plus-1` | `+1` | genesis input 3 | both accept |

The three negative cases may be repeated because rejection cannot consume their
shared input. The two accepted cases use different inputs and run once before fault
injection; repeating an accepted transaction would turn a useful fee result into an
uninteresting duplicate-input result.

The historical configurator container is read only. Its synthetic testnet signing
keys are copied into a mode-700 temporary directory only for offline fixture
generation. No signing key is copied into Git, a Docker build context, Compose, or a
published image. The temporary key directory is removed after fixture verification.

## Runtime design

The workload keeps the existing `parallel_driver_underfee.py` and its assertion
messages so the previous property history remains comparable. It adds:

- one `first_` command that uses the existing idempotent `-1` transaction as a
  readiness probe, then submits the exact and `+1` cases once while faults are off;
- one `parallel_driver_` command that uses Antithesis `random_choice` to select from
  the `-100` and `-2` additions while faults are active;
- the existing eventual recovery command, expanded to choose from all negative
  cases after faults stop.

The menu axis is the configured minimum-fee family. The action space is deliberately
small, so no extra per-timeline weighting layer is added. Every choice comes directly
from the Antithesis SDK for deterministic replay.

## Classification and properties

Transport failures remain `unavailable` and never become ledger disagreements.
Preparation failures remain `unknown`. HTTP 200/202 is `accepted`; the existing
Cardano and Amaru validation markers remain `phase1_reject`.

The workload records case ID, transaction ID, calculated minimum, actual fee, delta,
expected class, and both endpoint observations in every assertion.

New properties are:

- neither implementation ever accepts a below-minimum corpus transaction;
- whenever both below-minimum results are classifiable, both are phase-1 rejects;
- every negative menu neighborhood is reached at least once;
- both implementations accept the exact-minimum transaction before faults;
- both implementations accept the one-lovelace-above-minimum transaction before
  faults;
- after faults stop, both implementations again produce the expected result for a
  selected below-minimum case.

The original `-1` safety, agreement, reachability, and recovery properties remain.

## Error handling

The pre-fault command never risks consuming a valid input as a readiness check. It
first retries the immutable `-1` fixture until both endpoints return classifiable
responses, then submits each valid case exactly once. Driver and recovery commands
treat timeouts and resets as inconclusive and exit zero after emitting assertions.

Fixture loading rejects path traversal, duplicate IDs, malformed envelopes,
incorrect fee deltas, unexpected expected classes, or invalid transaction IDs.

## Verification

Implementation follows red-green-refactor. Verification includes workload unit and
bundle-contract tests, fixture transaction-ID and fee checks with `cardano-cli`, live
same-byte smoke submission to both endpoints, Compose rendering, official `snouty
validate`, and scans for secrets, signing-key markers, `.env`, and macOS `._*`
metadata.
