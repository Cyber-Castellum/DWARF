---
sut_path: /Users/nigel/dwarf-project/dwarf-v4
commit: b2ed2450ebb95e460e7c64f0d2578102be4d7663
updated: 2026-09-06
external_references:
  - path: https://github.com/pragma-org/amaru/commit/a4f15e71cd16f1cef7175cbca49352c3fb9777bc
    why: KES validation and runtime observability seams.
  - path: https://antithesis.com/docs/using_antithesis/sdk/python/
    why: Python observer packaging contract.
  - path: https://antithesis.com/docs/using_antithesis/sdk/fallback/
    why: Haskell assertion transport contract.
---

# Implementability

## Summary

The existing advancing ChainSync server, fallback SDK emitter, proven bootstrap
control, local KES codec, and Python observer patterns cover all required seams.
The primary implementation risks are victim state seeding, Cardano tip
observation from the workload image, and bounded log capture.

## Assumptions

- No change to Cardano-node or Amaru production binaries is required.

## Open Questions

- The observer image strategy must pass a local container test.
