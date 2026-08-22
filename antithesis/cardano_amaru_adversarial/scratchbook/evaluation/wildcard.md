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

# Wildcard Evaluation

The non-obvious risk is false symmetry: both endpoints can return HTTP 400 while
rejecting at different ledger stages. Comparing status alone would recreate the weak
earlier differential. The strict fee-marker classifier and dual-classifiability
property are therefore load-bearing, as is preserving bounded response text in
assertion details.

Another cross-cutting risk is submission order. A valid fixture would be accepted by
the first implementation and then become semantically different for the second. The
under-fee fixture avoids that state mutation and makes identical replay meaningful.

## Assumptions

- Error bodies remain sufficiently specific in both pinned versions.

## Open Questions

- If Amaru changes its response text, should a future workload use structured error
  codes or SUT-side instrumentation instead of expanding string markers?
