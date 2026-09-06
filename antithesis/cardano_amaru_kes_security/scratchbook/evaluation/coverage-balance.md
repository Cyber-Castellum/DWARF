---
sut_path: /Users/nigel/dwarf-project/dwarf-v4
commit: b2ed2450ebb95e460e7c64f0d2578102be4d7663
updated: 2026-09-06
external_references:
  - path: https://github.com/cardano-foundation/cardano-node-antithesis/wiki/Antithesis-Report-2026-W34
    why: Existing finding and coverage categories.
  - path: https://bench.gainpalfam.com/workbench/dwarf-latest
    why: Project campaign prioritization.
  - path: https://antithesis.com/docs/resources/blockchain_property_catalog/
    why: Blockchain property categories.
---

# Coverage balance

## Summary

The catalog balances control liveness, path reachability, safety, semantic
classification, recovery, and harness health. It intentionally avoids adding
fork/rollback properties already heavily covered by prior campaigns.

## Assumptions

- The inherited control continues to score its existing consensus properties.

## Open Questions

- None.
