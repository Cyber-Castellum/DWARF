# Observed: Amaru epoch-boundary crash during forward-sync from bootstrap

> **UPDATE 2026-07-18 — corrected by the live run (`live-run-2026-07-18.txt`).** A fresh bootstrap
> from the *normal* shipped bundle crosses epoch boundaries **cleanly** (49+ consecutive, 0 errors),
> because the bundle ships the required `MIN_LEDGER_SNAPSHOTS = 3`. The crash below was under a
> **non-standard forged setup** that did not supply the aligned `N-2` snapshot history — i.e. the
> failure mode is an *incomplete* bootstrap, not normal operation. Kept here as the origin datapoint.


**When:** during the false-leadership (forged-VRF) campaign setup, on the box.
**Setup:** a freshly bootstrapped Amaru (single imported snapshot) was forward-synced from
a seeded chain. When the synced chain **crossed an epoch boundary**, Amaru crashed. The box
build reported the failure at `state.rs:514`; that exact line differs in the current main
checkout (the file has been refactored) — the **mechanism** is the one mapped in
`../source-citations.txt`: crossing an epoch boundary calls
`snapshots.for_epoch(next_epoch - 2)` and `stake_distribution(next_epoch - 2)`, which a node
that has only just bootstrapped from one snapshot does not yet hold (it has not accrued
`MIN_LEDGER_SNAPSHOTS = 3` epochs of history). Depending on version this surfaces as a
`NoSuitableStakeDistribution` roll-forward error on an otherwise-valid block, or as a crash.

**Workaround used in that campaign:** keep the forged/served blocks **within a single
epoch** so no epoch boundary is crossed before the snapshot history exists. This let the
false-leadership differential proceed and is itself evidence of the warmup window.

**cardano-node under the same conditions:** no equivalent failure. Its stake distribution is
part of a self-contained ledger state, reconstructed by replay on startup; there is no
"bootstrap then immediately cross an epoch boundary" hazard.

**Why this matters for DWARF:** the crash/roll-forward-error is triggered by **bootstrap
state**, not by anything wrong with the block. A tip-convergence oracle sees only "Amaru
stopped advancing" — it cannot attribute it to the lifecycle cause. This is exactly the
class of behaviour a lifecycle test dimension would catch and a tip oracle cannot.

**Runtime reproduction:** deferred — the box was unreachable at write time. The mechanism is
established from source; a scripted reproduction (bootstrap at epoch E, drive sync to the
E->E+1 boundary before 3 epochs of snapshots exist, assert clean handling) is the natural
runtime follow-up when the box is back.
