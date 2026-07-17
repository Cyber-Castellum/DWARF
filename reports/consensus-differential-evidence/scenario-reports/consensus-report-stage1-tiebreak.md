# Stage-1 tiebreak — direct native-Amaru chain-selection differential

**Scenario:** `consensus-chainhold-differential-stage1-tiebreak` · **Run:** 4 h soak, 2026-07-15

## What it tests

Two things the other scenarios don't:
1. **Direct native-Amaru comparison.** chainhold/rollback/epoch-boundary read Amaru *indirectly* through `amaru-consumer` (a cardano-node syncing behind Amaru). This scenario reads a **native Amaru relay's** own selected tip and compares it against cardano-node directly — a stronger, first-party measurement.
2. **VRF tiebreak.** When two chains are the same length, Ouroboros breaks the tie by VRF value. This watches whether cardano-node and Amaru pick the same winner when natural equal-length competition occurs.

## Method

- **Substrate:** upstream `cardano_amaru` mesh (attach).
- **Prerequisite:** the mesh Amaru relays were raised to `AMARU_LOG=info` so their `build_ledger`/`build_chain` tip lines are emitted and the observer can read a native Amaru tip.
- **Oracle:** `chain_select_differential`, reference = `[relay1, relay2]` (cardano-node), target = `[amaru-relay-1]` (native Amaru), tolerance 5, `require_real_progress`.
- **Loop:** observe-only, repeated windows over 4 h.

## Result

**145 / 154 iterations passed; 9 flagged, all benign; 0 genuine divergences.**

The 9 flags occurred while the target was two Amaru relays: at the sampling instant the two relays were exactly **1 slot apart from each other** (one had applied the newest block, the other hadn't — single-slot propagation jitter). In every one, cardano-node and Amaru still **agreed** — the oracle only flagged the intra-Amaru group for not being in perfect lockstep. Fixed by targeting a single native Amaru relay (`amaru-relay-1`), which removes the lockstep requirement without weakening the differential.

| iter | cardano relays | Amaru relays |
|---|---|---|
| 61 | 107778 / 107778 | 107777 / **107778** |
| 78 | 109369 / 109369 | **109369** / 109368 |
| 104 | 111816 / 111816 | **111816** / 111815 |

## Interpretation

Read directly from a native Amaru node — not a proxy — Amaru's selected chain matches cardano-node's, including through natural VRF-tiebreak competition. The 9 flags are a harness strictness artifact, not a consensus finding.

## Antithesis assessment — **MEDIUM–HIGH value, port the invariant**

The *invariant* — "a native Amaru node's selected tip equals cardano-node's at stability" — is exactly what belongs in Antithesis, and reading Amaru directly makes it a first-party check. The VRF-*tiebreak* framing is harder: forcing a genuine equal-length tie on demand isn't reliably reproducible even under Antithesis's random faults, so it stays an opportunistic observation rather than a targeted condition. Port the direct-agreement invariant; treat tiebreak as coverage Antithesis will hit occasionally, not a guaranteed target.
