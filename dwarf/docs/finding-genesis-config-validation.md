# Finding note — genesis/config validation (cardano-node + Amaru)

**From:** DWARF genesis/config differential testing (Cyber-Castellum) · **Date:** 2026-07-18
**Scope:** feed cardano-node 10.7.1 and Amaru the *same* mutated `shelley-genesis.json`
(testnet_42 baseline: f=0.2, k=5, epoch 125) and compare acceptance. One confirmed
robustness bug in cardano-node, one well-supported architectural asymmetry in Amaru,
and one preliminary lead held back pending cleaner evidence.

## Finding 1 (cardano-node) — uncaught crash on unguarded time-parameter division

A systematic sweep of **417 single-field genesis mutations** (every numeric top-level and
`protocolParams` field × a 15-value edge palette), classified by timing (clean reject ~20 ms;
crash ~140 ms; accept = not-rejected-in-3 s), gives: **339 clean rejects, 75 accepts, 3 crashes.**
The 3 crashes are all a division reaching zero on a time parameter:

- `epochLength = 0` → `divide by zero`
- `slotLength = 0` → `Ratio has zero denominator`
- `slotLength = 1e-300` (underflows to 0) → `Ratio has zero denominator`

Each exits fast (~0.14 s) — not a hang or DoS — but the node *crashes* rather than reporting
"invalid genesis." **Recommendation:** range-check `epochLength > 0` and `slotLength > 0` during
genesis decode, alongside the existing `activeSlotsCoeff`/`securityParam` checks.

**Also (weaker, hedged):** the sweep shows genesis validation is largely type/parse-level, not
semantic — cardano-node *accepts* `slotLength=-1` (while `slotLength=0` crashes), zero
`maxKESEvolutions`, zero `updateQuorum`, and enormous `securityParam`/`epochLength`. Some may be
intentionally unconstrained at genesis; the negative-vs-zero `slotLength` inconsistency on one
field is the concrete part.

## Finding 2 (Amaru) — the node never reads the raw genesis at run time (confirmed from source)

cardano-node reads and validates the raw `shelley-genesis.json` **directly at every node
startup** (~20 ms clean reject). The **Amaru node does not** — the `amaru`, `amaru-consensus`,
and `amaru-ledger` crates contain *zero* references to `shelley-genesis`/`activeSlotsCoeff`.
Amaru's consensus parameters come from a **hardcoded per-network preset**
(`TESTNET_/PREPROD_/MAINNET_GLOBAL_PARAMETERS`, resolved by `match network`) or an
`AMARU_GLOBAL_*` env override — computed *once, at setup*, by separate tooling
(`bootstrap-producer`), which we observed to be slow, to intermittently hang, and to derive
non-deterministically on invalid input.

Different **config-trust models**: cardano-node validates the genesis as untrusted input at every
startup; the Amaru node trusts a pre-set/derived parameter artifact and never re-checks the source
genesis. **Recommendation:** validate the genesis (bounds/type checks matching cardano-node's) at
the point Amaru's parameters are established, and make the derivation deterministic and non-hanging
on malformed input.

## Finding 3 (Amaru) — parameter model omits fields cardano-node validates (confirmed from source; upgraded from a held-back lead)

The runtime hints were flaky and were held back; Amaru's `GlobalParameters` struct confirms them
deterministically:

- **`slotLength` is not in Amaru's parameter model.** A malformed `slotLength` (`0`, negative,
  `1e-300`) that **crashes cardano-node** is simply **ignored** by Amaru — it accepts and runs.
- **The active-slot coefficient is stored only as `active_slot_coeff_inverse: usize`** (integer
  1/f). Amaru cannot faithfully represent an arbitrary-precision or out-of-range genesis
  `activeSlotsCoeff`: `f=0` divides by zero; `f>1` truncates to a nonsense integer; a non-`1/n`
  value is rounded.

**Recommendation:** model `slotLength` and a full-precision active-slot coefficient (or explicitly
validate/reject genesis values outside the representable set) rather than silently ignoring or
truncating them.

## Reusable harness

The mutation battery + both-node runners are in
`reports/consensus-genesis-config-evidence/` and re-run against any devnet genesis. It is a
new differential *class* — config-acceptance parity — distinct from the message/block
fuzzing DWARF already does.
