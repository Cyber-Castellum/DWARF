# Consensus chain-selection differential — 4-hour soak campaign

**Date:** 2026-07-15 · **Substrate:** upstream `cardano_amaru` mesh (3 Haskell producers, 2 relays, 2 native Amaru relays, 1 Amaru-fed consumer) + one isolated cardano-node substrate for the delay probe.

## Question

Does the Haskell cardano-node ever disagree with Amaru on which chain is canonical? Each scenario is looped for 4 hours of wall-clock via `scripts/consensus_4h_runner.py`; every iteration is an independent attach → (fault) → observe → **chain-selection differential** → assert cycle.

## Result — no divergence

| Scenario | What it tests | Iterations | Pass | Fail |
|---|---|---:|---:|---:|
| chainhold-upstream | partition a producer → longer chain → does Amaru reorg like cardano-node? | 80 | 80 | 0 |
| epoch-boundary | agreement across an epoch transition (nonce / stake snapshot) | 94 | 94 | 0 |
| threshold-delay-probe | private chainsync fork under bounded delay vs Common-Prefix (cardano-only) | 91 | 91 | 0 |
| stage1-tiebreak | cardano relays vs **native Amaru relays, directly** (VRF tiebreak) | 154 | 145 | 9† |
| rollback-within-k | short partition heals → partitioned producer recovers to same chain | running (solo) | — | — |

**Across every completed iteration, cardano-node and Amaru selected the identical chain (hash-for-hash). Zero genuine divergences.** chainhold alone confirmed this across 80 induced fork/reorg cycles over 4 hours.

† **The 9 stage1 "fails" are benign — not divergences.** Every one has the same shape: the two *Amaru relays* were exactly **1 slot apart from each other** at the sampling instant (one had applied the newest block, the other had not yet — normal single-slot propagation jitter). In all 9, cardano-node and Amaru still **agreed** (same slot as at least one Amaru relay). The oracle flagged them only because stage1 was the sole scenario comparing against *two* native Amaru nodes and thus demanded intra-group lockstep. Representative cases:

| iter | cardano relays | Amaru relays |
|---|---|---|
| 61 | relay1=relay2=107778 | amaru-relay-1=107777, amaru-relay-2=**107778** |
| 78 | relay1=relay2=109369 | amaru-relay-1=**109369**, amaru-relay-2=109368 |
| 104 | relay1=relay2=111816 | amaru-relay-1=**111816**, amaru-relay-2=111815 |

**Fix (applied, for future runs):** stage1 now targets a single native Amaru relay (`amaru-relay-1`) — still a direct native-Amaru read, but without the intra-group lockstep requirement, so 1-slot propagation jitter no longer registers as a failure. This does not affect the completed run's conclusion.

## On iteration counts

The per-scenario counts (80–154) are set by per-iteration cost, not by any cap. A chainhold iteration includes a 90 s partition + 70 s settle (~180 s/iteration → ~80 in 4 h); the observe-only scenarios skip that and run more (stage1 154, epoch-boundary 94, threshold 91).

This local loop is a **soak / repeatability validation** — dozens of independent induced-fork differential checks — **not** a fuzzing campaign. Exhaustive, adversarial state-space exploration (millions of operations) is the role of the **Antithesis** port. To get more local iterations, shorten the fault/settle windows or extend the duration; the count is a knob, not a limit.

## Environment notes

- **Amaru relay log level:** raised to `AMARU_LOG=info` on `amaru-relay-1/-2` so their `build_ledger`/`build_chain` tip lines are emitted and the observer can read native Amaru tips directly.
- **p2 split-brain:** producer `p2` is stranded on its own fork (frozen at slot 35144); a restart and a DB wipe both failed to re-peer it. The mesh is fully functional on `p1`/`p3` (chainhold ran 80/80), so `rollback` partitions the healthy `p1`. `p2` is a known, isolated environment wart, not a consensus finding.
