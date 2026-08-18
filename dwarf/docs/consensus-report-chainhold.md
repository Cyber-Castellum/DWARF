# Chainhold — chain-selection differential under induced fork/reorg

**Scenario:** `consensus-chainhold-upstream-differential` · **Run:** 4 h soak, 2026-07-15

## What it tests

The flagship question: when a fork forces a reorg, does Amaru reorg to the same chain cardano-node does? A producer is partitioned from the mesh so the remaining chain grows longer; on reconnect the isolated node must abandon its shorter branch. The oracle checks that the cardano-node side and the Amaru side end up on the **identical** tip.

## Method

- **Substrate:** upstream `cardano_amaru` mesh (attach, no provisioning).
- **Fault:** `runtime_network_partition` on producer `p2` — 90 s partition, 70 s settle, per iteration.
- **Oracle:** `chain_select_differential`, reference = `[relay1, relay2]` (cardano-node), target = `[amaru-consumer]` (a cardano-node syncing *through* Amaru, so its tip reflects what Amaru selected), tolerance 5 slots, `require_real_progress`.
- **Loop:** `consensus_4h_runner.py`, one full partition → reorg → assert cycle per iteration (~180 s each).

## Result

**80 / 80 iterations passed. 0 failures. 0 divergences.** Over 80 independent induced fork/reorg cycles across 4 hours, cardano-node and Amaru selected the identical chain every time (hash-identical or within slot tolerance during propagation).

## Interpretation

This is the strongest single piece of evidence in the suite: cardano-node and Amaru do not disagree on chain selection under repeated, real reorgs. The reorg is genuine (a producer is cut off and must roll back on reconnect), and Amaru — observed through the consumer — tracks the same canonical chain.

## Antithesis assessment — **HIGH value, port it**

This is a prime Antithesis candidate. Antithesis's core strength is autonomously exploring the network-fault state space (partition topologies, durations, orderings) far beyond our single scripted `p2` partition. Ported as the invariant *"the cardano-node group and the Amaru group always agree on the canonical tip at stability,"* Antithesis would drive thousands of distinct fork/reorg patterns against that invariant. The differential oracle maps cleanly to an `always()` SDK assertion.
