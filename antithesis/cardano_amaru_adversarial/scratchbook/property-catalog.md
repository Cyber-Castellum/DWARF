---
sut_path: /Users/nigel/dwarf-project/dwarf-v4/antithesis/cardano_amaru_adversarial
<<<<<<< HEAD
commit: 0c8fed6cda53b5222d41fdf463a6e966169f143a
=======
commit: b2ed2450ebb95e460e7c64f0d2578102be4d7663
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
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

# Property Catalog

<<<<<<< HEAD
This targeted cycle extends one proven cross-implementation property along a
small semantic boundary. It is not a claim that the full Cardano/Amaru property
portfolio contains only this property.

## Transaction admission boundaries

### phase1-underfee-admission-agreement — Signed Minimum-Fee Corpus Agreement
=======
This targeted cycle intentionally implements one property at a time. It is not a
claim that the full Cardano/Amaru property portfolio contains only one property.

## Transaction admission boundaries

### phase1-underfee-admission-agreement — One-Lovelace-Under-Minimum Agreement
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47

| | |
|---|---|
| **Priority** | P0 for the next mixed run |
<<<<<<< HEAD
| **Status** | Original `-1` case completed in Antithesis; five-case preflight found Amaru rejects exact and `+1`; corpus run pending |
| **Type** | Safety plus liveness/reachability support |
| **Property** | For identical correctly signed Conway transactions at `minimum + {-100,-2,-1,0,+1}`, Cardano and Amaru return the manifest-declared outcome: negative deltas reject at phase 1, while exact minimum and `+1` accept. |
| **Invariant** | Corpus assertions require classifiable paired results to match the expected outcome and prohibit acceptance of every negative case. Case ID, delta, expectation, input, transaction ID, and both responses are attached to every evaluation. Reachability and `Sometimes` assertions prevent a vacuous green run. |
| **Antithesis Angle** | A `first_` command proves readiness with the replay-safe `-1` case and submits the two valid cases exactly once before faults. During faults, `antithesis.random.random_choice` selects only the three idempotent negative cases. An `eventually_` command selects one negative case and checks recovery after faults stop. Cardano remains a stable control. |
=======
| **Status** | Implemented; same-byte live validation, anonymous image access, and digest-pinned `snouty validate` passed; MOOG run pending |
| **Type** | Safety plus liveness/reachability support |
| **Property** | The identical correctly signed Conway transaction at exactly `minimum fee - 1 lovelace` is never accepted by Cardano or Amaru, and classifiable responses from both are phase-1 fee rejections. |
| **Invariant** | `Always(not any_accepted)` checks the safety guarantee on every observation. `Always(not both_classifiable or phase1_agreement)` checks semantic agreement only when the precondition is observable. `Sometimes(both_classifiable)` and outcome-specific `Reachable` calls prevent a green but vacuous run. An eventual command uses `Sometimes(recovered)` for post-fault progress. |
| **Antithesis Angle** | Amaru is killed, paused, stopped, or partitioned while repeated idempotent submissions occur; Cardano remains a stable control. Antithesis explores submission during partial startup, shutdown, listener replacement, and recovery. |
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
| **Why It Matters** | Different phase-1 admission decisions can produce mempool and block-validation divergence. This exercises a semantic ledger boundary that the existing decoder, rollback, diffusion, reconnect, and phase-2 reports do not cover. |

**Resolved reachability question:** the paired block-216 Cardano snapshot and baked
Amaru store both retain the fixture input. Cardano returns the exact fee mismatch;
Amaru returns its generic validation-layer error. A fresh configurator UTxO does not
<<<<<<< HEAD
match and must never be used for this property. The exact-minimum and `+1` cases use
separate retained inputs because accepted transactions cannot be replayed safely.

**Confirmed finding:** Amaru `v10.11.20260807` mempool validation charges the
full 201-byte standalone transaction while Cardano's ledger fee calculation uses
200 bytes without `is_valid`. The exact and `+1` cases therefore disagree, and a
controlled `+44` case is accepted by both. Each corpus case now has a separate
fixed assertion identity, so Antithesis will report reachability and correctness
without aggregate-property masking.

## Completed baseline run

The replacement one-hour run at public commit
`6082f0eedabe478801051a661435a5ffb3424f47` completed as Antithesis run
`0195e57647d3ee8322893e6f8af159a5-59-13` (MOOG test-run
`f1334b7d8425d76f219d62dd0c01f462e35c6faf9f987b11f6b86c2ad83a9bd7`).
It exercised only the original `-1` fixture.

- 43 of 44 reported properties passed. The only failure was the pre-existing
  Cardano `cluster fork depth < k` property, with 43 counterexamples; no new
  mixed admission issue was surfaced.
- The two mixed safety properties each recorded 155,608 passing examples.
- Paired classifiability was reached 148,003 times, and post-fault recovery
  recorded 3,775 passing examples.
- The fault injector ran, all test commands were discovered and completed, and
  both Amaru relays participated in network traffic.

## Assumptions

- Valid transactions run only in the fault-free `first_` phase and only once.
- Repeated and recovery commands select only negative, non-consuming cases.
- Selection uses Antithesis structured randomness at the decision point; no SDK
  random value is cached or used to seed another RNG.

## Open Questions

- After this corpus is exercised, the next fee work should cross a serialized-size
  transition or add another phase-1 rule rather than adding more nearby fee deltas.
=======
match and must never be used for this property.

## Assumptions

- The one-lovelace boundary is the fixed menu value for this first property; no
  randomness axis applies until this end-to-end path is validated.

## Open Questions

- Which phase-1 boundary should be added second after this property produces a
  clean, classifiable live result?
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
