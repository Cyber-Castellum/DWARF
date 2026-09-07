---
sut_path: /Users/nigel/dwarf-project/dwarf-v4
commit: b2ed2450ebb95e460e7c64f0d2578102be4d7663
updated: 2026-09-06
external_references:
  - path: https://github.com/pragma-org/amaru/commit/a4f15e71cd16f1cef7175cbca49352c3fb9777bc
    why: Current KES validation implementation.
  - path: https://github.com/IntersectMBO/cardano-node/issues
    why: Current Cardano-node KES issue audit.
  - path: https://github.com/cardano-foundation/cardano-node-antithesis/wiki/Antithesis-Report-2026-W34
    why: Prior finding and harness coverage boundary.
  - path: https://antithesis.com/docs/product/writing_tests/test_templates/
    why: Current test-template and property scheduling contract.
---

# Property catalog

## Live result

Antithesis run `8417206dcfc6e6c97dc31e0c11a96bcb-60-7` completed on
2026-09-06 with active fault injection. Every `mixed_kes_*` property below
passed, including 24,539 classifiable examples with no invalid-header adoption
counterexample. The run found no mixed hot-KES divergence. See
`notes/mixed-kes-antithesis-outcome-2026-09-06.html` for the separate
classification of inherited and harness failures.

## Mixed-control prerequisites

### mixed-control-converges — Benign mixed path converges

| | |
|---|---|
| **Type** | Liveness |
| **Property** | The inherited Amaru-only consumer reaches the current producer tip. |
| **Invariant** | `Sometimes(condition)`, because real convergence must be observed at least once. |
| **Antithesis Angle** | Prevents a broken base network from producing vacuous security greens. |
| **Why It Matters** | Establishes the Brown-M&M control for every attack timeline. |

**Open Questions:** None.

### both-proxies-connect — Both dedicated victims connect

| | |
|---|---|
| **Type** | Reachability |
| **Property** | Each dedicated proxy accepts its intended victim over N2N. |
| **Invariant** | Two unique `Reachable` assertions, one per path. |
| **Antithesis Angle** | Faults repeatedly exercise handshake and intersection recovery. |
| **Why It Matters** | A present but unused adversary is not a security test. |

**Open Questions:** None.

### both-victims-advance-honestly — Honest prefix reaches both victims

| | |
|---|---|
| **Type** | Liveness |
| **Property** | Both victims adopt at least one honest header served by their proxies before classification. |
| **Invariant** | `Sometimes(condition)` over observed tip movement and honest serve events. |
| **Antithesis Angle** | Distinguishes validation from a stalled seed or bad topology. |
| **Why It Matters** | Proves the attacked protocol path is usable. |

**Open Questions:** None.

## Hot-KES security

### same-kes-mutation-delivered — Same mutation reaches both implementations

| | |
|---|---|
| **Type** | Reachability |
| **Property** | Both proxies serve the same source header, mutated hash, byte offset, and bit. |
| **Invariant** | `Sometimes(condition)` because at least one paired encounter is required. |
| **Antithesis Angle** | Seeded selection plus faults explores headers and restart boundaries reproducibly. |
| **Why It Matters** | Makes the comparison differential rather than two unrelated errors. |

**Open Questions:** None.

### invalid-kes-never-adopted — Invalid hot-KES header is never adopted

| | |
|---|---|
| **Type** | Safety |
| **Property** | Neither victim may report the delivered mutated header hash as its adopted tip. |
| **Invariant** | `Always(condition)` only when delivery and both live observations are concrete. |
| **Antithesis Angle** | Process/network faults explore replay and restart ordering around validation. |
| **Why It Matters** | Adoption would violate Praos authentication and consensus safety. |

**Open Questions:** None.

### amaru-classifies-invalid-kes — Amaru reaches its KES rejection branch

| | |
|---|---|
| **Type** | Reachability |
| **Property** | Amaru emits its explicit invalid-KES validation result for a delivered mutation. |
| **Invariant** | `Sometimes(condition)` because the semantic branch must be observed at least once. |
| **Antithesis Angle** | Confirms faults did not merely make the victim unavailable. |
| **Why It Matters** | Avoids treating disconnect or timeout as semantic rejection. |

**Open Questions:** None.

### implementations-agree-on-non-adoption — Differential non-adoption agrees

| | |
|---|---|
| **Type** | Safety |
| **Property** | For every paired classifiable mutation, Cardano and Amaru both refuse adoption. |
| **Invariant** | `Always(condition)` over paired, classifiable observations. |
| **Antithesis Angle** | Finds implementation divergence under identical input and fault history. |
| **Why It Matters** | A one-sided acceptance is the core mixed-client security failure. |

**Open Questions:** None.

## Recovery and harness integrity

### mutation-remains-classifiable-after-faults — Attack remains classifiable after recovery

| | |
|---|---|
| **Type** | Liveness |
| **Property** | After faults stop, the benign control progresses and the paired mutation remains classifiable. |
| **Invariant** | `Sometimes(condition)` from the bounded `eventually_` command. |
| **Antithesis Angle** | Tests restart/reintersection without requiring availability during faults. |
| **Why It Matters** | Separates control-network recovery from attack-path observability. |

**Open Questions:** None.

### evidence-is-bounded — Attack evidence remains bounded

| | |
|---|---|
| **Type** | Safety |
| **Property** | Proxy and victim evidence files stay below configured caps and reconnect logging is sampled. |
| **Invariant** | `AlwaysOrUnreachable` locally and a unique `Always` when the observer can stat the files. |
| **Antithesis Angle** | Reconnect storms are amplified by faults. |
| **Why It Matters** | W34 showed output pressure can invalidate an otherwise useful campaign. |

**Open Questions:** None.

## Assumptions

- Inherited control properties retain their existing implementation.

## Open Questions

- None.
