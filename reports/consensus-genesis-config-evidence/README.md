# Genesis/config-acceptance differential evidence

Backs `../campaign-reports/dwarf-consensus-genesis-config-differential.html`,
`../../dwarf/docs/consensus-report-genesis-config-differential.md`, and the finding note
`../../dwarf/docs/finding-genesis-config-validation.md`.

## The test

A new differential *class* for DWARF: the **genesis both nodes ingest at startup** (the
"stored" input) rather than the messages/blocks that existing fuzzing targets. Mutate one
`shelley-genesis.json` field at a time (edge/invalid values) and compare whether cardano-node
and Amaru **accept or reject** — a config one accepts and the other rejects/crashes on is a
semantic divergence at the configuration layer, before any block.

## Results (see `logs/differential-table.txt`)

- **Finding 1 (confirmed):** cardano-node crashes with an uncaught arithmetic exception on
  `epochLength=0` (`divide by zero`) and `slotLength=0` (`Ratio has zero denominator`) instead
  of cleanly rejecting. It rejects every other malformed field cleanly in **~20 ms**.
- **Finding 2 (confirmed, from source):** the Amaru node **never reads the raw genesis at runtime**
  — the `amaru`/`amaru-consensus`/`amaru-ledger` crates have zero references to
  `shelley-genesis`/`activeSlotsCoeff`. Params come from a hardcoded per-network preset
  (`TESTNET_GLOBAL_PARAMETERS`) or `AMARU_GLOBAL_*` env, computed once at setup. Different
  config-trust models.
- **Finding 3 (confirmed, from source — upgraded from a held-back lead):** Amaru's
  `GlobalParameters` struct **omits `slotLength`** (so a `slotLength` that crashes cardano-node is
  silently ignored) and stores the active-slot coefficient only as an integer inverse
  (`active_slot_coeff_inverse: usize`), so it can't faithfully represent arbitrary-precision or
  out-of-range `f`. Confirmed via source after the runtime (`bootstrap-producer`) proved too flaky
  — the disciplined path (withhold-while-flaky, then confirm deterministically).

## The systematic sweep (417 mutations)

Beyond the hand-picked battery, `scripts/gen_matrix.py` + `scripts/sweep_cardano_node.sh` run a
**417-mutation** sweep (every numeric top-level and `protocolParams` field × a 15-value edge
palette) through cardano-node, classified by **timing** (clean REJECT ~20 ms; CRASH ~140 ms;
ACCEPT = not-rejected within a 3 s cap — the reliable discriminator, since the node takes
seconds to fully start even on a valid genesis). Result in `logs/sweep-417-classified.tsv`:
**339 REJECT, 75 ACCEPT, 3 CRASH.** The 3 crashes are the only ones — all division-reaching-zero
on time parameters (`epochLength=0`, `slotLength=0`, `slotLength=1e-300`). Note: this is
cardano-node-only (Amaru's path is too slow/flaky to sweep — see Finding 3).

## `scripts/`

- `gen_muts.py` — the hand-picked 15-mutation battery.
- `gen_matrix.py` — the systematic 417-mutation matrix generator.
- `sweep_cardano_node.sh` — the timing-classified sweep runner (cap 3 s; CRASH/REJECT/ACCEPT).
- `run_cardano_node.sh` / `run_amaru.sh` — the per-mutation both-node runners (Amaru unreliable).

## `logs/`

- `differential-table.txt` — the consolidated cardano-node vs Amaru table with confidence levels.
- `sweep-417-classified.tsv` — the full systematic sweep output (`name  class  rc  dur  msg`).

## Setup notes

- Devnet = testnet_42 (f=0.2, k=5, epoch 125). Baseline genesis + config from the
  `cardano_amaru_p1-configs` docker volume; `config.node.json` = `config.json` + `DijkstraGenesisFile`
  (node 10.7.1 requires it). cardano-node at `/home/dwarf/.local/bin/cardano-node`.
- Amaru derivation needs a chain-db (`/tmp/fl2/db_honest2`) + `protocolMagicId`/`lock` markers.

No credentials present.
