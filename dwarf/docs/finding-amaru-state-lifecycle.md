# Finding note — Amaru state lifecycle / recovery (for the Amaru team)

**From:** DWARF state-lifecycle differential (Cyber-Castellum) · **Date:** 2026-07-18
**Scope:** source analysis (main @ 060001dd) of how `cardano-node` and `Amaru` persist and
recover ledger state across restart, crash, and cold-start-after-bootstrap, **plus a live DWARF
run** that fresh-bootstrapped a real Amaru node and drove it across epoch boundaries. No exploit —
a lifecycle/recovery model difference, and a new test dimension for the differential suite.

> **Correction from the live run (please note):** an earlier draft claimed a freshly bootstrapped
> Amaru would fail a *valid* block at the first epoch boundary. **The live run refuted that for the
> normal path:** with the shipped bundle (which contains exactly `MIN_LEDGER_SNAPSHOTS = 3`
> snapshots), Amaru crossed epoch boundaries cleanly — 49+ consecutive, zero errors. Finding 1 below
> is corrected: the `N-2` dependency is real, but it is a hazard only for a bootstrap that *lacks*
> the aligned snapshot history, not for normal operation.

## Summary

cardano-node keeps a **self-contained ledger state** (the stake distribution lives inside it)
and **replays from the newest usable snapshot — falling back to genesis** — on startup, so it
self-heals and is operational immediately. Amaru splits state across **four stores** that must
stay cross-consistent, **requires ≥ 3 epochs of snapshot history to operate**, and has **no
from-genesis rebuild**. The two clients therefore have different recovery guarantees. The
`N-2` snapshot dependency is real, but the shipped bundle satisfies it — a fresh bootstrap crosses
epoch boundaries cleanly in practice (live-verified). The durable difference is the recovery model.

## Finding 1 — the `N-2` snapshot dependency is real and load-bearing (but the normal path is clean)

`MIN_LEDGER_SNAPSHOTS = 3` (`state.rs`). `epoch_transition` reads
`snapshots.for_epoch(next_epoch - 2)` and `stake_distribution(next_epoch - 2)`; if the deque
lacks that epoch, `StakeDistributionView::new` returns `NoSuitableStakeDistribution(epoch)`,
surfacing as a roll-forward **error on an otherwise-valid block** ("Failed to roll forward the
ledger state"). **Live-verified both ways:** a fresh bootstrap from the shipped bundle (which
contains exactly the 3 required snapshots, at slots 124/246/366) crossed epoch boundaries
**cleanly** — 49+ consecutive transitions, `stake_distribution_epoch = current_epoch − 3` visible
in the logs, **zero** errors, `restarts=0`. So the dependency is satisfied on the normal path.
The failure surfaces only for a bootstrap that *lacks* the aligned snapshot history (the earlier
false-leadership-campaign crash was under a non-standard forged setup, not normal bootstrap).

## Finding 2 — no from-genesis / replay recovery; restart demands consistent stores

`amaru node run` reopens the existing stores and requires the ledger tip to equal the
chain-store tip (`build_node.rs`: *"Have you bootstrapped your node?"* /
*"ledger tip header not found"*). There is no path that rebuilds the ledger by replaying from
block 0, so a lost or stale snapshot cannot be recovered by replay the way cardano-node
recovers (newest snapshot → older → genesis).

## Finding 3 — store-invariant violations panic instead of recovering

`panic!("no tip found in stable db")`, `unreachable!("epoch transition reset did not succeed
after first block!")`, `unreachable!("pre-condition: volatile.len() >= k")`. A store
inconsistency that cardano-node would validate-and-truncate through becomes a hard crash.

## Recommendations

1. **Guard the incomplete-history case** — if a bootstrap supplies fewer than
   `MIN_LEDGER_SNAPSHOTS` aligned snapshots, detect it and surface a defined, non-fatal state
   (wait / fetch more history) rather than a valid-block roll-forward failure or crash. (Normal
   bundle bootstrap already ships the required 3 and is clean.)
2. **Consider a replay-based recovery path** (even a slow one) so a lost/corrupt snapshot is
   recoverable rather than fatal — complements the from-genesis bootstrap suggestion in the
   bootstrap trust-source note.
3. **Downgrade store-invariant `panic!`/`unreachable!` to recoverable errors** where a restart
   could legitimately re-establish the invariant.
4. The DWARF harness can be extended with a **lifecycle dimension**: restart mid-epoch,
   restart-across-epoch-boundary-after-bootstrap, kill-9-then-reopen, and on-disk-format
   upgrade — asserting same-state return and no crash, compared against cardano-node. Offered
   as a joint next step; the observed epoch-boundary crash is the seed case.
