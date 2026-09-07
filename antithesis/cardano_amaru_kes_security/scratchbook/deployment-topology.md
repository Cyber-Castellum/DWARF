---
sut_path: /Users/nigel/dwarf-project/dwarf-v4
commit: b2ed2450ebb95e460e7c64f0d2578102be4d7663
updated: 2026-09-06
external_references:
  - path: https://github.com/pragma-org/amaru/commit/a4f15e71cd16f1cef7175cbca49352c3fb9777bc
    why: Current Amaru runtime and bootstrap contract.
  - path: https://github.com/cardano-foundation/cardano-node-antithesis/issues
    why: Mixed-net harness history and issues.
  - path: https://bench.gainpalfam.com/workbench/moog
    why: Proven mixed topology and launch pitfalls.
  - path: https://antithesis.com/docs/setup/docker_compose/
    why: Current Antithesis Compose contract.
---

# Deployment topology

## Summary

The complete relay-bootstrap control is retained. Five faultable services are
added: one Cardano state seeder, two dedicated live proxies, and two dedicated
victims. One fault-excluded workload service owns test commands and
observations.

```text
control: p1/p2/p3 -> relay1/relay2 -> amaru relays -> isolated consumer
attack A: p1 -> kes-cardano-proxy -> kes-cardano-victim
attack B: p1 -> kes-amaru-proxy   -> kes-amaru-victim
observer: proxy evidence + cardano victim socket + amaru victim bounded log
```

Each victim has a private state volume. Each proxy has a private evidence
volume. The observer mounts all evidence read-only except its Antithesis output
directory. Baseline infrastructure exclusions retain the exact Moog token
string; attack services are faultable. Both victims wait for the Cardano seed
service to complete, then receive honest headers until the shared minimum slot
1800 before deterministic mutation selection begins.

## Assumptions

- Cardano CLI is available in the selected observer base or is copied with all
  required runtime libraries during image construction.

## Open Questions

- Final observer image construction is selected by the local image-build test.
