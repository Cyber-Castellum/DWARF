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

# Property Relationships

## Phase-1 admission cluster

- `phase1-underfee-admission-agreement` is the first member and establishes the
  shared fixture, dual-submit, response-classification, and fault-recovery substrate.
- Future minimum-output, validity-interval, value-conservation, witness, and input-set
  properties can reuse the substrate but require separate fixtures and catalog IDs.
- No future property is dominated by the under-fee property: agreement on one ledger
  rule does not imply agreement on another.

## Excluded neighboring clusters

Decoder/CDDL agreement occurs before ledger admission and is already represented by
prior DWARF findings. Phase-2 script behavior occurs after phase-1. Transaction
diffusion and reconnect/resume determine propagation and peer lifecycle rather than
the local ledger rule. Rollback/chain selection acts on block state. These clusters
are related operationally but none substitutes for the scoped property.

## Assumptions

- Property clusters are separated by the validation or protocol stage whose outcome
  they observe.

## Open Questions

- None for the current single-property cluster.
