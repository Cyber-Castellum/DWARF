---
sut_path: /Users/nigel/dwarf-project/dwarf-v4/antithesis/cardano_amaru_adversarial
commit: 0c8fed6cda53b5222d41fdf463a6e966169f143a
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

- `phase1-underfee-admission-agreement` establishes the shared fixture,
  dual-submit, response-classification, and fault-recovery substrate across five
  minimum-fee values: `-100`, `-2`, `-1`, exact, and `+1`.
- The exact and `+1` cases are fault-free, exactly-once reachability checks. They
  share the semantic property with the negative cases but not their replay model.
- The three negative cases are replay-safe and form the only structured-random
  menu used by parallel and eventual commands.
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

## Dominance and next-step rule

The three nearby negative deltas do not justify another run by themselves; they
are one compact boundary corpus. A subsequent scenario should add a new semantic
axis, such as a fee transition caused by serialized transaction size, a
minimum-output rule, validity interval, value conservation, witness validity, or
input-set behavior. Consensus rollback/fork behavior remains a separate,
previously exercised cluster and is not the next target here.
