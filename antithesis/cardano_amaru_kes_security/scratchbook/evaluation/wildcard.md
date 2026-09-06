---
sut_path: /Users/nigel/dwarf-project/dwarf-v4
commit: b2ed2450ebb95e460e7c64f0d2578102be4d7663
updated: 2026-09-06
external_references:
  - path: https://github.com/pragma-org/amaru/wiki
    why: Opcert and rollback implementation history.
  - path: https://github.com/cardano-foundation/cardano-node-antithesis/issues
    why: Mixed-net restart and harness history.
  - path: https://bench.gainpalfam.com/workbench/moog
    why: Prior differential findings.
---

# Wildcard evaluation

## Summary

The highest-value unexpected result is not simply one implementation accepting
the bad header. It is state-dependent disagreement after a victim/proxy restart,
especially if opcert sequence state or a stale intersection changes which
validation rule fires first. Structured mutation IDs preserve that evidence.

## Assumptions

- The proxy rebuilds from the live source chain after restart.

## Open Questions

- None.
