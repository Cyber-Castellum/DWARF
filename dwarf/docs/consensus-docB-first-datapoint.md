# Design Doc B — first adversarial-stake data point (f = 0.40)

**Date:** 2026-07-16 · **Substrate:** `cardano_amaru_adv` — a proper renamed clone of the upstream `cardano_amaru` testnet (testnetDomain `adv`, isolated network/names), genesis stake-split so producer `adv-p1` holds **40%** and `adv-p2`/`adv-p3` hold 30% each. **Attack:** private-chain withholding — the 40% adversary is partitioned from the mesh, forges a private chain in isolation, then reconnects to attempt a reorg.

## Result (seed 1, 90 s withholding window)

| Measure | Value |
|---|---|
| Adversary (40%) private blocks, **A** | **9** |
| Honest (60%) public blocks, **H** | **8** |
| Adversary chain longer (A > H)? | **YES** |
| Lasting rollback depth | 0 (k = 5) |
| Common-Prefix violation (H1) | No |
| Verdict | adversary-chain-longer-but-shallow |

## Interpretation

Over a 90-second window the **40% adversary out-forged the 60% honest majority** (9 vs 8 blocks), building a *longer* private chain. This is the meaningful signal: at 40% stake, block-production variance is enough to win short chain races — which is exactly why "effective threshold ~40%, not 51%" is directionally plausible rather than absurd. In this single trial the honest majority recovered on reconnect (no lasting rollback), so no Common-Prefix breach occurred — but the adversary demonstrably *led* the race.

## Honest caveats (being fixed next)

1. **Rollback measurement is imprecise.** "Adversary chain longer = true" with "rollback depth = 0" is in tension: tip-number sampling cannot see a same-length fork switch (blocks 21–28 replaced while the tip number rises 28→29). The authoritative signal is the node's `ChainDB.SwitchedToAFork` reorg trace, which states the rollback point and depth. Switching the metric to that trace before any H1 breach is claimed.
2. **Amaru differential not yet wired.** This measures the *protocol* attack only. The Amaru security payload — does `adv-amaru-consumer` follow the same post-attack chain cardano-node does — attaches once Amaru finishes bootstrapping. A divergence there is the actual Amaru finding.
3. **Single trial.** Needs multiple seeds and longer withholding windows per cell to estimate how often 40% wins and where CP actually breaks.

## Status

Substrate is real and reproducible (renamed clone via the repo's documented bring-up); the attack runs and produces data. This is the first genuine adversarial data point — the next steps are precise reorg-depth measurement, the Amaru differential, and the seed/window sweep at f ∈ {0.40, 0.45}.
