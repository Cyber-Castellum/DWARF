---
sut_path: /Users/nigel/dwarf-project/dwarf-v4
commit: b2ed2450ebb95e460e7c64f0d2578102be4d7663
updated: 2026-09-06
external_references:
  - path: https://github.com/pragma-org/amaru/commit/a4f15e71cd16f1cef7175cbca49352c3fb9777bc
    why: Current KES validation implementation.
  - path: https://github.com/cardano-foundation/cardano-node-antithesis/wiki/Antithesis-Report-2026-W34
    why: Prior finding and harness coverage boundary.
  - path: https://bench.gainpalfam.com/workbench/dwarf-latest
    why: Project preflight and integration decisions.
  - path: https://antithesis.com/docs/product/writing_tests/test_templates/
    why: Current test-template scheduling contract.
---

# Property relationships

## Summary

`mixed-control-converges`, `both-proxies-connect`, and
`both-victims-advance-honestly` are prerequisites. A failure in any of them
invalidates semantic interpretation of downstream security assertions.

`same-kes-mutation-delivered` enables `invalid-kes-never-adopted`,
`amaru-classifies-invalid-kes`, and `implementations-agree-on-non-adoption`.
The observer must not emit those safety evaluations without the paired delivery
key. `mutation-remains-classifiable-after-faults` depends on baseline recovery
and the paired delivery remaining observable. `evidence-is-bounded` is
independent harness health.

## Assumptions

- Mutation IDs are content-derived rather than timestamp-derived.

## Open Questions

- None.
