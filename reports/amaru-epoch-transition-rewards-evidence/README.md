# Evidence — Amaru panics at the epoch transition on a total-rewards discrepancy

Supporting evidence for `dwarf/docs/finding-amaru-epoch-transition-rewards-discrepancy.md`.
Amaru `v10.11.0` (image `cf657b91…`, k=20 / PR #186 params) **deterministically panics** crossing a
dormant epoch boundary on an internal expected-vs-actual total-rewards mismatch. Found by DWARF's
adversarial mixed-net soak (`antithesis/cardano_amaru_adversarial/`); the crash is on the **honest**
relay, not the adversarial one.

## The panic

```
epoch_transition ... from=3 into=4 ... ratification.summarize is_dormant_epoch=true
panicked at crates/amaru-ledger/src/store/epoch_transition.rs:71:13:
discrepancy between expected total rewards (=1415076923074) and actual total rewards (=1414056923074)
```
Delta = **1,020,000,000 lovelace (1,020 ADA)**, identical every crash.

## Contents

- **`relay2-panic.log`** — the honest Amaru relay's log: the `rewards.summarize` pot values
  (`effective_rewards=1415076923074`, `total_rewards=…`, `pots_reserves=…`), the epoch-transition
  trace (`from=3 into=4`, `is_dormant_epoch=true`), and the panic.
- **`relay2-state.txt`** — container restart count / status (crash-loop; 600+ restarts observed).
- **`CLIENT-RUN-CONFIRMATION.txt`** — the **same panic** pulled from the client's own
  `cardano-foundation/cardano-node-antithesis` `testnets/cardano_amaru` Antithesis run
  (`run_id 0ed9a9d12a80add4184d8c32777ddfd5-56-17`, via `moog antithesis events`): identical message,
  **same 1,020,000,000 delta**, at epoch **2→3** (vs 3→4 locally) → recurs at every dormant boundary,
  and is the crash behind their "amaru-relay exit code 1" findings.
- **`6h-soak-timeline.log`** — the 6-hour adversarial soak: every 15 min, honest/adversary block
  heights, `ORACLE_FAILS`, decode-rejections. Shows **`ORACLE_FAILS=0` across the full 6 h** with
  **512** forged-block rejections (Amaru never adopted a mutated block), and the honest relay height
  plateauing at the epoch boundary (crash-loop).
- **`compose-params.txt`** / **`global-parameters.json`** — the params in use (relays run with
  `AMARU_GLOBAL_*` k=20 env + bootstrap era-history; the k=5 `global-parameters.json` is **not mounted
  into the relays** — vestigial).
- **`version.txt`** — image / version.

## What is proven

- **Deterministic crash** at a dormant epoch boundary (600+ identical restarts).
- **Not misconfiguration** — the identical panic (same 1,020 ADA delta) appears in the **client's own**
  Antithesis run at a different epoch.
- **Internal inconsistency** — Amaru's own *expected* vs *actual* total rewards disagree; the fix is to
  route unassigned rewards to reserves/treasury (as cardano-node does) instead of asserting/panicking.
- **PR #186 did not fix it** — still present at k=20 on v10.11 (a new symptom on the same dormant-epoch
  reward path as findings #4/#5).
