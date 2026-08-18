# Genesis/config-acceptance differential (cardano-node vs Amaru)

**Harness experiment** (config-mutation battery, not a runner-native DSL scenario) · **Run:** 2026-07-18
**Devnet:** `cardano_amaru` testnet_42 (f=0.2, k=5, epoch 125, Conway)

## Severity & scope (read first)

This is a **new test method** (a config-acceptance differential) and its first results. To set
expectations honestly:

- **Finding 1 is a low-severity robustness bug, not a security vulnerability.** The genesis file
  is trusted, operator-set config — an attacker cannot feed a node a malicious genesis over the
  network — so there is **no attack surface**. The bug is that cardano-node *crashes* (a loud,
  fast startup exception) instead of cleanly rejecting a malformed genesis. It belongs upstream
  as a `cardano-node` (IntersectMBO) robustness issue, not a security advisory.
- **Findings 2 & 3 are the Amaru-relevant result** — confirmed *from Amaru's source code*: the
  Amaru node never reads the raw genesis at runtime and runs on a reduced, integer parameter model
  that omits fields cardano-node validates (e.g. `slotLength`). No exploit; a design/trust
  observation worth the team's attention.

Nothing here is a critical or exploitable security finding. The value is the **method** (a
config-acceptance differential is a new surface DWARF didn't cover) and concrete, honest,
source-confirmed observations.

## What it tests

A new surface for DWARF: the **genesis/config both nodes ingest at startup**. Existing fuzzing targets protocol messages and blocks (the "reflected" input); this targets the *stored* input — the `shelley-genesis.json` that defines the protocol's core parameters. The question: **do cardano-node and Amaru agree on which genesis configurations are valid?** We mutate one genesis field at a time (edge/invalid values) and compare accept/reject. A config one implementation accepts and the other rejects (or crashes on) is a semantic divergence at the configuration layer — before any block is ever produced.

## Method

- Baseline: the real testnet_42 `shelley-genesis.json`. 15 single-field mutations (out-of-range `activeSlotsCoeff`, zero/negative/float `securityParam`, `epochLength`/`slotLength = 0`, type confusion, missing/extra fields).
- **cardano-node side:** feed each mutated genesis and start the node; classify ACCEPT (starts) vs REJECT (fast exit + error) vs CRASH; record latency.
- **Amaru side:** feed each mutated genesis through Amaru's derivation path (`bootstrap-producer`, which computes Amaru's `AMARU_GLOBAL_*` params from the genesis); classify accept/derive vs reject vs hang. **Control** (`activeSlotsCoeff=0.5` → Amaru derives `1/f=2`, correct) confirms the mutated genesis actually reaches Amaru's derivation.

## Findings

### Finding 1 (CONFIRMED, systematic) — cardano-node crashes on unguarded time-parameter division

We swept **417 single-field mutations** of `shelley-genesis.json` (every top-level and
`protocolParams` numeric field × a 15-value edge palette: zero, negative, float-where-int,
`2^63`/`2^64`/`10^30`, `1e-300`, string, null, bool, missing, …) and classified each by
timing: a clean parse-time **REJECT** exits in **~20 ms**; a **CRASH** fast-fails at ~140 ms
with an uncaught exception; an **ACCEPT** doesn't reject within a 3 s cap.

| Outcome | Count |
|---|---:|
| REJECT (clean, ~20 ms) | 339 |
| ACCEPT (not rejected) | 75 |
| **CRASH (uncaught exception)** | **3** |

The 3 crashes are all a **division reaching zero on a time parameter**:

| Mutation | Result |
|---|---|
| `epochLength = 0` | `divide by zero` |
| `slotLength = 0` | `Ratio has zero denominator` |
| `slotLength = 1e-300` (underflows) | `Ratio has zero denominator` |

So the node crashes rather than cleanly rejecting exactly where a genesis time parameter is
zero (or underflows to zero) and reaches an unguarded division. Fast-failing, not a hang — but
a *crash* instead of an "invalid genesis" rejection. **Fix:** range-check `epochLength > 0` and
`slotLength > 0` at decode, alongside the existing `activeSlotsCoeff`/`securityParam` checks.

**Validation-completeness observation (weaker, hedged).** The 75 ACCEPTs show cardano-node's
genesis validation is largely **type/parse-level, not semantic**: it accepts `slotLength=-1`
(negative!) — while `slotLength=0` *crashes* — and also zero `maxKESEvolutions`, zero
`updateQuorum`, and enormous `securityParam`/`epochLength` (`2^63`). Some of these may be
intentionally unconstrained at the genesis layer, so we flag them as observations, not bugs —
but the *negative-slotLength-accepted vs zero-slotLength-crashes* inconsistency on one field is a
concrete robustness gap.

### Finding 2 (CONFIRMED, from source) — the Amaru node never reads the raw genesis; it runs on a reduced, pre-set parameter model

This is the substantive gap-#2 result, and it is confirmed by reading Amaru's source (not the
flaky runtime):

- **cardano-node** reads and validates the raw `shelley-genesis.json` **directly, at node startup**, rejecting a bad one in **~20 ms**.
- **The Amaru node does not read `shelley-genesis.json` at all at run time.** The `amaru`, `amaru-consensus`, and `amaru-ledger` crates contain *zero* references to `shelley-genesis` / `activeSlotsCoeff`. Amaru's consensus parameters come from a **hardcoded per-network preset** (`TESTNET_/PREPROD_/MAINNET_GLOBAL_PARAMETERS`, resolved by a `match network`) or an `AMARU_GLOBAL_*` env override — a value computed *once, at setup*, by separate tooling (`bootstrap-producer`).

So the two clients have fundamentally different **config-trust models**: cardano-node treats the genesis as untrusted input to validate at every startup; Amaru trusts a pre-set/derived parameter artifact and never re-checks the source genesis. Any genesis-level validation happens (if at all) only in the setup/derivation tooling — which we observed to be slow, to intermittently hang, and to derive **non-deterministically** on invalid input.

### Finding 3 (CONFIRMED, from source — upgraded from a held-back lead) — Amaru's parameter model omits fields cardano-node validates, and can't represent all valid genesis values

The earlier runtime hints were flaky, so they were held back. Reading the source confirms them
deterministically via the `GlobalParameters` struct Amaru actually uses:

- **`slotLength` is not in Amaru's parameter model at all.** So a malformed `slotLength` (`0`, negative, `1e-300`) that **crashes cardano-node** is simply **ignored** by Amaru — it accepts and runs. This is the clearest divergence, and it is now a *structural fact*, not a one-off observation.
- **The active-slot coefficient is stored only as `active_slot_coeff_inverse: usize` — an integer 1/f.** Amaru therefore cannot faithfully represent an arbitrary-precision or out-of-range genesis `activeSlotsCoeff`: `f=0` divides by zero in derivation (matching the observed `asc_0` error); `f>1` truncates to a nonsense small integer; a non-`1/n` value is rounded.

**Net (Findings 2 + 3 together):** genesis-level validation that cardano-node performs at every
startup does not happen in the Amaru node, because the node runs on a reduced integer parameter
model derived once by external tooling. This is not an exploit — but it is a real, source-confirmed
difference in how the two implementations trust and model the protocol's foundational parameters,
and it is the kind of thing worth the Amaru team's attention.

## Status

All three findings are now **confirmed**: the cardano-node crash (Finding 1, via a 417-mutation
sweep) and the Amaru config-trust/parameter-model gap (Findings 2 & 3, via Amaru's source code).
No open threads remain on this line of work. Raw data, the mutation battery, and the sweep are in
`reports/consensus-genesis-config-evidence/`.
