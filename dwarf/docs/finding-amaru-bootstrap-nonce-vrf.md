# Finding — Amaru fast-bootstrap: dormant-epoch crash vs deep-bootstrap VRF/nonce failure (a tension)

**From:** DWARF devnet operations / consensus robustness (Cyber-Castellum) · **Date:** 2026-07-20
**Severity:** node-availability (a fresh Amaru either crash-loops or can't sync). Not a
consensus-safety issue. cardano-node is unaffected. **Companion to**
`finding-amaru-dormant-epoch-rewards-crash.md` and the bootstrap trust-source finding (#27).

## The tension (both observed live on the `cardano_amaru` devnet)

A freshly fast-bootstrapped Amaru node has **two mutually-exclusive failure modes** depending on how
deep in the chain its snapshot bundle lands:

| bootstrap depth | outcome |
|---|---|
| **Shallow** (early epoch, e.g. epoch 2) | crosses a **dormant** early epoch (no block in its stability window) → fatal `RewardsSummaryNotReady` crash-loop (the dormant-epoch finding) |
| **Deep** (mature epoch, e.g. epoch 14) | avoids the crash, but the first header after the bundle tip **fails VRF verification** → Amaru disconnects its only upstream peer → permanently stuck with "no connections available to fetch blocks" |

So the two failure modes are real — **but they are triggered by *bootstrapping directly into* the bad
condition, not by the normal sync path.**

> **Correction / resolution (2026-07-20):** a **shallow bootstrap that then syncs forward
> continuously** boots a fully-synced Amaru **cleanly** — verified live: both relays came up
> `restarts=0`, crossed multiple epoch boundaries (including a governance-`is_dormant_epoch=true`
> one) with **no `RewardsSummaryNotReady`**, hit **no VRF failures**, and tracked the cardano tip
> **block-for-block**. The two crashes are specific to *bootstrapping the node's tip directly onto*
> (a) a dormant epoch's boundary (rewards not yet computed) or (b) a deep point whose bundle nonce
> doesn't validate the next header (VRF). Syncing forward from a near-genesis shallow bundle avoids
> both: rewards get computed as each epoch's stability-window blocks are processed, and the
> near-genesis nonce is self-consistent. So the normal devnet flow (shallow bundle + forward sync)
> works; the findings below are hazards of *arbitrary-point* fast bootstrap.

## The VRF/nonce failure (deep bootstrap) — mechanism

Amaru validates each header's leader-election VRF against the **epoch nonce**
(`validate_header.rs`: `epoch_nonce = evolve_nonce(header)` → `check_header(..., epoch_nonce)` →
`praos::header::assert_all` VRF check). `evolve_nonce` (`store.rs`) derives the active/evolving nonce
from the **parent header's stored nonces**, which come from the **bootstrap bundle** (Amaru imports
nonces at bootstrap rather than deriving them from validated genesis history — the #27 trust-root
divergence).

Observed: a node deep-bootstrapped with its ledger/chain tip at slot 1874 (epoch ~14/15) rejected the
next header (slot 1876) with `Invalid VRF proof: VerificationFailed`, repeatedly, then bailed on its
peer. The nonce state baked into the deep bundle by `/bin/bootstrap-producer` does not correctly
validate the subsequent headers' VRF — i.e. the **deep-bundle nonce derivation is inconsistent with
the live chain's actual epoch nonce** at that point. (A near-genesis shallow bootstrap does not hit
this, because the early nonce is well-defined from genesis — but shallow bootstrap hits the dormant
crash instead.)

## Why cardano-node is immune

cardano-node **derives** its epoch nonces by validating chain history from genesis, so its nonce is
always correct and rewards are always computed at the boundary. Amaru's fast bootstrap depends on the
snapshot/bundle carrying a correct nonce state (and a non-dormant next epoch) — a strictly stronger
requirement that the fresh, sparse devnet violates.

## Practical path to a working local devnet (and Antithesis readiness, #42)

1. **Ensure dense early-epoch block production** so no early epoch is dormant, then use a **shallow**
   bootstrap (near-genesis nonce is consistent → no VRF failure; non-dormant epochs → no crash). This
   is how the original long-running deploy happened to work. Lever: raise early block density (the
   producers' warm-up is the culprit) or redeploy until the early epochs are dense.
2. If deep bootstrap is required, the **`bootstrap-producer` deep-nonce derivation** must be verified
   against the live chain's nonce — likely an Amaru-team item.

## Recommendation for the Amaru team

- Make fast-bootstrap robust to **both** a dormant next epoch (rewards path) **and** deep-bundle
  nonce consistency (VRF path), so a snapshot taken at an arbitrary mature point boots and syncs.
- Consider an independently-verifiable nonce (derive-and-check) rather than trusting the imported
  bundle nonce blindly.
