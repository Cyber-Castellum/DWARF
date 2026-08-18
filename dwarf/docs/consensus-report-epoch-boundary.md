# Epoch-boundary — chain-selection differential across epoch transitions

**Scenario:** `consensus-epoch-boundary-differential` · **Run:** 4 h soak, 2026-07-15

## What it tests

An epoch transition is where the protocol rotates the epoch nonce and freezes a new stake snapshot — a moment where an implementation bug could cause the two clients to compute a different leader schedule and drift onto different chains. This scenario checks that cardano-node and Amaru stay in agreement *across* epoch boundaries, with no injected fault — the transition itself is the event under test.

## Method

- **Substrate:** upstream `cardano_amaru` mesh (attach).
- **Fault:** none — observe-only across natural epoch transitions.
- **Oracle:** `chain_select_differential`, reference = `[relay1, relay2]` (cardano-node), target = `[amaru-consumer]` (Amaru-fed), tolerance, `require_real_progress`.
- **Loop:** repeated observation windows over 4 h (fast profile → many epochs elapse).

## Result

**94 / 94 iterations passed. 0 failures. 0 divergences.** Across every epoch transition sampled in 4 hours, the cardano-node and Amaru sides agreed on the canonical tip.

## Interpretation

The epoch-boundary machinery (nonce evolution, stake-snapshot handoff) does not induce any cardano-node↔Amaru disagreement. Combined with chainhold, this covers both "agreement under reorg" and "agreement across the protocol's most sensitive scheduled transition."

## Antithesis assessment — **MEDIUM value, port as a layered invariant**

The epoch transition is a *time-driven* event, not a fault with a large state space to explore, so on its own it gives Antithesis little to fuzz. Its value in Antithesis is as a **layered invariant**: keep the "cardano-node ≡ Amaru" assertion asserted continuously while Antithesis injects network faults, and let it discover whether a fault landing *during* an epoch handoff can cause divergence — a combination our scripted runs don't exercise. Worth including in the differential workload, but not as a distinct Antithesis test of its own.
