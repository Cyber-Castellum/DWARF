# Threshold delay probe — private fork under bounded network delay

**Scenario:** `consensus-threshold-delay-probe` · **Run:** 4 h soak, 2026-07-15

## What it tests

Whether a private chainsync fork, injected under a bounded upstream network delay, can break Common-Prefix consistency among honest nodes. This is an Ouroboros **safety** question (the λ·Δ delay bound), *not* a cardano-node-vs-Amaru differential.

## Method

- **Substrate:** an **isolated cardano-node-only** devnet (`node1/2/3` honest + `adv1` adversary), self-provisioned per run. **No Amaru node is involved.**
- **Load:** `runtime_network_impairment` (100 ms latency `adv1 → node1`) + `runtime_chainsync_responder_fork_switch` on `node1` (the private fork).
- **Oracle:** `chain_select_consistent` — do the observed honest nodes agree on the same tip despite the fork+delay?
- **Note on prior assertions:** the scenario originally also asserted `k_bound_rollback_recovered` (which evaluates a `runtime_force_rollback` primitive this scenario never runs) and `all_nodes_responsive` (incompatible with an attack that deliberately disrupts `node1`). Both were mis-authored and were removed; the retained assertion is the invariant the scenario actually tests.

## Result

**91 / 91 iterations passed. 0 failures.** Under a 100 ms-delayed private fork, honest nodes maintained Common-Prefix consistency the entire run.

## Interpretation

At this delay and fork strength, cardano-node's honest chain wins — the private fork does not corrupt consensus among honest nodes. This is a valid single-implementation safety soak result.

## Antithesis assessment — **LOW value / do NOT port as-is**

This scenario should **not** go into the Amaru consensus Antithesis campaign in its current form, for two reasons:

1. **It is cardano-node-only.** There is no Amaru node in it, so it tests nothing about the cardano-node-vs-Amaru question that motivates this entire work stream. It cannot surface a differential because there is nothing to differ.
2. **It re-tests a well-studied property.** Ouroboros Common-Prefix safety under bounded delay is a property of the *protocol*, extensively analysed and already exercised by cardano-node's own test suites. Re-running it in Antithesis adds little new signal about *Amaru*.

**Recommendation:** either drop it from the Antithesis scope, or — only if a private-fork-under-delay differential is genuinely wanted — rebuild it as a *mixed* cardano-node/Amaru scenario so it asks "do cardano-node and Amaru agree on which fork wins under delay?" That version would be worth porting, but it requires a mixed substrate where Amaru is correctly bootstrapped (the upstream mesh), not the isolated cardano-only devnet used here. As written, it is a soak/safety probe, not differential security testing.
