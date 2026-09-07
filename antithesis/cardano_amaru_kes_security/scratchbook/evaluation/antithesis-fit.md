---
sut_path: /Users/nigel/dwarf-project/dwarf-v4
commit: b2ed2450ebb95e460e7c64f0d2578102be4d7663
updated: 2026-09-06
external_references:
  - path: https://antithesis.com/docs/product/writing_tests/test_templates/
    why: Current autonomous scheduling model.
  - path: https://antithesis.com/docs/using_antithesis/sdk/fallback/
    why: Current fallback SDK contract.
  - path: https://bench.gainpalfam.com/workbench/moog
    why: Prior mixed-net execution evidence.
---

# Antithesis fit

## Summary

The mutation is deterministic from seed and immutable input, the interesting
boundary is restart/reconnect timing, and the invariant is externally
checkable. Test-template drivers provide repeated observations while Antithesis
controls scheduling and faults. Fit is high if paired mutation and victim
observability gates pass locally.

## Assumptions

- Custom images are public and digest-pinned before launch.

## Open Questions

- None before local proof.
