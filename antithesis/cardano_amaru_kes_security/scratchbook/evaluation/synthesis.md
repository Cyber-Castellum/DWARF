---
sut_path: /Users/nigel/dwarf-project/dwarf-v4
commit: b2ed2450ebb95e460e7c64f0d2578102be4d7663
updated: 2026-09-06
external_references:
  - path: https://github.com/pragma-org/amaru/commit/a4f15e71cd16f1cef7175cbca49352c3fb9777bc
    why: Current KES implementation boundary.
  - path: https://github.com/cardano-foundation/cardano-node-antithesis/wiki/Antithesis-Report-2026-W34
    why: Prior finding and harness boundary.
  - path: https://bench.gainpalfam.com/workbench/dwarf-latest
    why: Project preflight and prioritization decisions.
  - path: https://antithesis.com/docs/product/writing_tests/test_templates/
    why: Current autonomous testing model.
---

# Evaluation synthesis

## Summary

Proceed with one focused hot-KES campaign. Preserve the full proven mixed
control, add isolated paired victims, make mutation selection pure and
replayable, require explicit delivery and classification, and cap evidence.
Do not combine expired-KES or future-opcert cases until each has a
consensus-compatible local generator and dedicated evidence.

The first stopping gate is local end-to-end DWARF proof. Public image digest,
Moog payload validation, and Antithesis submission are later gates and are not
authorized here.

## Assumptions

- The one-bit mutation remains within the signature payload for all selected
  Shelley-family headers.

## Open Questions

- None that blocks writing failing tests.
