---
sut_path: /Users/nigel/dwarf-project/dwarf-v4/antithesis/cardano_amaru_adversarial
commit: b2ed2450ebb95e460e7c64f0d2578102be4d7663
updated: 2026-08-22
external_references:
  - path: https://github.com/pragma-org/amaru/wiki
    why: Amaru architecture, operating model, and documented limitations.
  - path: https://bench.gainpalfam.com/wb/moog
    why: Project decisions, MOOG deployment state, and prior Antithesis handoffs.
  - path: /Users/nigel/dwarf-project/dwarf-v4/dwarf/docs
    why: DWARF finding reports and prior Amaru analysis.
  - path: /Users/nigel/dwarf-project/dwarf-v4/reports
    why: Evidence bundles for previously exercised Amaru failure classes.
  - path: https://github.com/pragma-org/amaru/issues/1265
    why: Known reconnect/resume behavior excluded from this scenario.
  - path: https://github.com/pragma-org/amaru/issues/1156
    why: Known local transaction diffusion behavior excluded from this scenario.
  - path: https://github.com/pragma-org/amaru/issues/1004
    why: Known phase-2 behavior excluded from this phase-1 scenario.
---

# Existing Assertions

## Mixed phase-1 workload

Source: `workload/mixed_phase1.py`.

| Line | Type | Message |
|---:|---|---|
| 197 | Reachable | `mixed phase-1 underfee fixture loaded` |
| 199 | Reachable | `both implementations returned classifiable phase-1 results` |
| 200 | Sometimes | `both implementations sometimes return classifiable phase-1 results` |
| 205 | Always | `neither implementation accepts a one-lovelace-under-minimum transaction` |
| 210 | Always | `classifiable Cardano and Amaru results agree on phase-1 fee rejection` |
| 216 | Sometimes | `both implementations recover phase-1 fee rejection after faults` |

These assertions implement the scoped property. The catalog copy under
`/opt/antithesis/catalog/` ensures discovery even if a timeline never obtains a
classifiable response.

## Existing adversarial-chain oracle

Source: `oracle/oracle.py`.

| Line | Type | Message |
|---:|---|---|
| 98 | Sometimes | `honest amaru relay advances the real chain during the attack` |
| 100 | Reachable | `honest amaru relay reached a new tip` |
| 106 | Sometimes | `adversarial amaru relay rejected a forged block at decode` |
| 114 | Always | `adversarial amaru relay never adopts a forged fork (its tip matches the honest chain at equal height)` |
| 119 | Always | `adversarial amaru relay never advances ahead of the honest chain (no forged advance)` |
| 125 | Always | `adversarial amaru relay does not panic on forged input` |
| 127 | Always | `honest amaru relay does not panic` |

The two existing `Sometimes(True, ...)` calls are legacy instrumentation and should
be converted to outcome-specific `Reachable` calls in a separate cleanup. They do
not implement or substitute for the phase-1 property.

## Assumptions

- Line numbers describe the uncommitted implementation based on commit `b2ed245`.

## Open Questions

- None for the scoped property.
