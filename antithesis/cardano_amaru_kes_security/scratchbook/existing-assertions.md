---
sut_path: /Users/nigel/dwarf-project/dwarf-v4
commit: b2ed2450ebb95e460e7c64f0d2578102be4d7663
updated: 2026-09-06
external_references:
  - path: https://github.com/pragma-org/amaru/commit/a4f15e71cd16f1cef7175cbca49352c3fb9777bc
    why: Current Amaru source and issue audit.
  - path: https://github.com/cardano-foundation/cardano-node-antithesis/wiki/Antithesis-Report-2026-W34
    why: Prior assertion and harness outcomes.
  - path: https://bench.gainpalfam.com/workbench/dwarf-latest
    why: Project assertion and preflight decisions.
  - path: https://antithesis.com/docs/using_antithesis/sdk/fallback/
    why: Current assertion transport contract.
---

# Existing assertions

## Summary

The inherited sidecars already own setup-complete, producer fork-depth, Amaru
fatal-log, and Amaru-served consumer convergence properties. Existing DWARF
adversaries emit `dwarf_node_connected` and per-mutation reachability through
the fallback SDK. The fail-closed KES oracle is an Antithesis test template and
completed a one-hour live run on 2026-09-06.

The new workload must use unique `mixed_kes_*` identities and must not duplicate
or reinterpret inherited assertions. It adds only mutation delivery,
classification, non-adoption, and post-fault recurrence properties.

Live result: all `mixed_kes_*` properties passed in run
`8417206dcfc6e6c97dc31e0c11a96bcb-60-7`. The inherited
`cluster fork depth < k` failure remained separately identifiable and must not
be relabelled as a KES result.

## Assumptions

- Inherited sidecar assertions remain byte-for-byte part of the copied control.

## Open Questions

- None.
