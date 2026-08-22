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

# Property Catalog

This targeted cycle intentionally implements one property at a time. It is not a
claim that the full Cardano/Amaru property portfolio contains only one property.

## Transaction admission boundaries

### phase1-underfee-admission-agreement — One-Lovelace-Under-Minimum Agreement

| | |
|---|---|
| **Priority** | P0 for the next mixed run |
| **Status** | Implemented; same-byte live validation, anonymous image access, and digest-pinned `snouty validate` passed; MOOG run pending |
| **Type** | Safety plus liveness/reachability support |
| **Property** | The identical correctly signed Conway transaction at exactly `minimum fee - 1 lovelace` is never accepted by Cardano or Amaru, and classifiable responses from both are phase-1 fee rejections. |
| **Invariant** | `Always(not any_accepted)` checks the safety guarantee on every observation. `Always(not both_classifiable or phase1_agreement)` checks semantic agreement only when the precondition is observable. `Sometimes(both_classifiable)` and outcome-specific `Reachable` calls prevent a green but vacuous run. An eventual command uses `Sometimes(recovered)` for post-fault progress. |
| **Antithesis Angle** | Amaru is killed, paused, stopped, or partitioned while repeated idempotent submissions occur; Cardano remains a stable control. Antithesis explores submission during partial startup, shutdown, listener replacement, and recovery. |
| **Why It Matters** | Different phase-1 admission decisions can produce mempool and block-validation divergence. This exercises a semantic ledger boundary that the existing decoder, rollback, diffusion, reconnect, and phase-2 reports do not cover. |

**Resolved reachability question:** the paired block-216 Cardano snapshot and baked
Amaru store both retain the fixture input. Cardano returns the exact fee mismatch;
Amaru returns its generic validation-layer error. A fresh configurator UTxO does not
match and must never be used for this property.

## Assumptions

- The one-lovelace boundary is the fixed menu value for this first property; no
  randomness axis applies until this end-to-end path is validated.

## Open Questions

- Which phase-1 boundary should be added second after this property produces a
  clean, classifiable live result?
