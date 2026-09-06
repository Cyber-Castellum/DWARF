---
sut_path: /Users/nigel/dwarf-project/dwarf-v4
commit: b2ed2450ebb95e460e7c64f0d2578102be4d7663
updated: 2026-09-06
external_references:
  - path: https://github.com/pragma-org/amaru/commit/a4f15e71cd16f1cef7175cbca49352c3fb9777bc
    why: Current Amaru source, KES validation, bootstrap, and issue audit.
  - path: https://github.com/pragma-org/amaru/wiki
    why: Current project log and historical mixed/Antithesis notes.
  - path: https://github.com/IntersectMBO/cardano-node/issues
    why: Current Cardano-node KES and runtime issue audit.
  - path: https://github.com/cardano-foundation/cardano-node-antithesis/issues
    why: Current mixed-net harness issues and successful-run lineage.
  - path: https://github.com/cardano-foundation/cardano-node-antithesis/wiki/Antithesis-Report-2026-W34
    why: Prior DWARF results and harness failure classification.
  - path: https://antithesis.com/docs/product/writing_tests/test_templates/
    why: Current test-template scheduling and command contracts.
  - path: https://antithesis.com/docs/using_antithesis/sdk/fallback/
    why: Current fallback SDK transport contract.
  - path: https://antithesis.com/docs/using_antithesis/sdk/python/
    why: Current Python SDK and cataloging contract.
  - path: https://bench.gainpalfam.com/workbench/dwarf-latest
    why: Project preflight, security baseline, and integration decisions.
  - path: https://bench.gainpalfam.com/workbench/moog
    why: Proven mixed topology, prior runs, and Moog launch pitfalls.
---

# SUT analysis

## Summary

The SUT is a mixed short-epoch Cardano network with Cardano-node producers,
Amaru relays, an Amaru-served Cardano consumer, and two isolated validation
victims. The attack surface is the real ChainSync header-serialization boundary.
DWARF mirrors honest live headers and changes one bit only inside the final
448-byte KES signature payload before delivery.

The trusted control plane is the additive copy of
`cardano_amaru_relay_bootstrap_control`; the security plane is two dedicated
proxy/victim paths. Shared genesis and source ChainDB lineage prevent a
configuration mismatch from masquerading as a validation result.

## Components and data flow

- `configurator`, `p1..p3`, `relay1..2`: unchanged Cardano control cluster.
- `amaru-relay-1..2`: unchanged independently bootstrapped Amaru control relays.
- `amaru-consumer`: unchanged Amaru-only control path.
- `kes-*-proxy`: live ChainSync clients of `p1` and servers to one victim each.
- `kes-cardano-victim`: seeded Cardano node with only its proxy as upstream.
- `kes-amaru-victim`: bootstrapped Amaru node with only its proxy as upstream.
- `kes-workload`: bounded observer/test-template owner; never emits setup-complete.

## Attack surfaces

1. Hot-KES signature verification after otherwise-valid header decoding.
2. ChainSync reconnect/intersection behavior at a repeatedly rejected header.
3. Node/proxy restart ordering around an advancing honest chain.
4. Oracle vacuity caused by unavailable victims or missing mutation delivery.
5. Resource/output pressure from reconnect loops.

## Assumptions

- A Shelley-family N2N header serialization ends in the 448-byte KES signature
  payload; current Amaru source independently defines that signature size.
- Identical seed and header bytes must yield identical mutation decisions; both
  proxies use one fixed explicit seed per reviewed campaign.
- Both victims wait for the same seed completion, and both proxies keep the
  stream honest before slot 1800. This prevents different bootstrap starting
  points from selecting different first invalid successors.
- Explicit Amaru `Invalid KES signature` output is required for classification.

## Open Questions

- Exact public digest for the new KES proxy image is intentionally unresolved
  until the locally proven source is published and built.
