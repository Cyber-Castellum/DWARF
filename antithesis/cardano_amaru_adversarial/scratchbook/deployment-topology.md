---
sut_path: /Users/nigel/dwarf-project/dwarf-v4/antithesis/cardano_amaru_adversarial
updated: 2026-08-22
external_references:
  - path: https://github.com/pragma-org/amaru/wiki
    why: Amaru architecture and operating model.
  - path: https://bench.gainpalfam.com/wb/moog
    why: Prior test decisions and deployment state.
  - path: /Users/nigel/dwarf-project/dwarf-v4/reports
    why: Prior evidence bundles and exclusions.
---

# Deployment Topology

## Differential data path

```text
mixed-phase1-workload (static signed CBOR)
  |-- same bytes --> cardano-submit-api --> cardano-phase1-reference socket
  `-- same bytes --> amaru-relay-1 submit API --> baked Amaru store
```

The reference image and Amaru image derive from the same synthetic chain. The
reference node uses an empty topology so its shared genesis UTxO remains stable.

## Components

| Container(s) | Role | Faults | Justification |
|---|---|---|---|
| `cardano-phase1-reference` | phase-1 oracle ledger | excluded | Baked block-216 state matches Amaru; initializes shared socket volume. |
| `cardano-submit-api` | Cardano protocol adapter | excluded | Exposes the real local submission decision over HTTP. |
| `mixed-phase1-workload` | Antithesis client/catalog | excluded | Holds immutable fixture, commands, and assertions. |
| `amaru-relay-1` | target implementation | enabled | Its admission and recovery behavior is explored under faults. |
| `p1`, `p2`, `p3`, `relay1`, `relay2` | existing mixed consensus topology | excluded | Retained for established properties and to feed Amaru. |
| `amaru-relay-2` | existing control | enabled | Retained for the legacy oracle properties. |
| `dwarf-adversary` | hostile chain-sync peer | excluded | Uses Antithesis randomness; sanitized numeric seed prevents crash loops. |
| tracer/sidecar/oracle services | readiness/observability | excluded | Preserve setup-complete and existing assertions. |

## Fixture provenance and security

`fixture/static/underfee.tx` is a signed invalid testnet transaction, not a secret.
The matching genesis signing key is not mounted or included. The build contexts and
published image paths were scanned for `.skey`, key, PEM, and environment files.

## Readiness

The established sidecar/tracer path emits setup-complete. The workload then runs one
driver command during faults and one eventual command for recovery. Official local
validation found both commands.

## Assumptions

- The `amaru-cardano` tenant enables node faults for Amaru containers.
- Anonymous manifest retrieval for every new digest-pinned image returns HTTP 200.

## Deferred optimization

A later phase-1-only directory could remove the legacy consensus/oracle components to
reduce state space, but this first run deliberately extends the already working mixed
bundle instead of risking another topology migration.
