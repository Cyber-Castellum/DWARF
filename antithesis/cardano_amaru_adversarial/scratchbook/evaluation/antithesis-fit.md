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

# Antithesis-Fit Evaluation

The fee boundary itself is deterministic integration-test territory. Its Antithesis
value comes from repeated admission during partial Amaru failure and post-fault
recovery. The catalog correctly combines an `Always` safety check with meaningful
`Sometimes`/`Reachable` non-vacuity and recovery checks. Stable Cardano control keeps
fault scheduling from destroying the oracle.

Refinement applied: transport outages are explicitly inconclusive, while acceptance
by either endpoint remains a safety failure.

## Assumptions

- Node/network faults reach Amaru in the tenant.

## Open Questions

- None beyond the shared-ledger reachability question in the catalog.
