---
sut_path: /Users/nigel/dwarf-project/dwarf-v4/antithesis/cardano_amaru_adversarial
commit: b2ed2450ebb95e460e7c64f0d2578102be4d7663
updated: 2026-08-22
external_references:
  - path: https://github.com/pragma-org/amaru/wiki
    why: Amaru architecture and operating model.
  - path: https://bench.gainpalfam.com/wb/moog
    why: Prior test decisions and deployment state.
  - path: /Users/nigel/dwarf-project/dwarf-v4/dwarf/docs
    why: Prior findings and exclusions.
  - path: /Users/nigel/dwarf-project/dwarf-v4/reports
    why: Prior evidence bundles.
  - path: https://github.com/pragma-org/amaru/issues/1265
    why: Excluded reconnect behavior.
  - path: https://github.com/pragma-org/amaru/issues/1156
    why: Excluded diffusion behavior.
  - path: https://github.com/pragma-org/amaru/issues/1004
    why: Excluded phase-2 behavior.
---

# Coverage-Balance Evaluation

The catalog is intentionally one property, so it is not a balanced full-system
portfolio. Within the targeted cycle it includes safety, reachability, and liveness
signals. The largest future gap is other phase-1 rule families; adding them now would
reduce first-run triage quality and violate the one-property workflow.

Gap recorded for later discovery: minimum output, validity interval, value
conservation, witness validity, and input-set semantics.

## Assumptions

- The existing bundle continues to catalog the older consensus/adversarial-chain
  properties separately.

## Open Questions

- Which phase-1 rule family has the best risk-to-fixture-cost ratio after under-fee?
