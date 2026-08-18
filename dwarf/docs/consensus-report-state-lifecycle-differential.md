# State-lifecycle / recovery differential (cardano-node vs Amaru)

**Source analysis** (Amaru `crates/amaru*`, main @ 060001dd) **+ a live DWARF run** that
fresh-bootstrapped a real Amaru node and drove it across epoch boundaries · **Date:** 2026-07-18

> **Read first — what the live run changed.** The source analysis predicted a post-bootstrap
> "warmup window" in which a *valid* block could fail at the first epoch boundary. **Running it
> refuted the strong form of that claim:** under a *normal* bootstrap (the shipped bundle, which
> contains exactly `MIN_LEDGER_SNAPSHOTS = 3` snapshots), a freshly bootstrapped Amaru crosses
> epoch boundaries **cleanly** — 49+ consecutive transitions observed, **zero** panics/errors. The
> `N-2` stake-distribution dependency is **real and load-bearing** (visible in the logs), but the
> bundle satisfies it, so there is no crash on the normal path. The failure mode requires a
> bootstrap that *lacks* the aligned snapshot history. The recovery/lifecycle differences below
> stand; the "valid block fails at first boundary" claim is corrected accordingly. See
> `reports/consensus-state-lifecycle-evidence/logs/live-run-2026-07-18.txt`.

## What it tests

DWARF's existing oracles watch the two nodes' chain **tips**. They do not test what happens
across a node's own *life events*: **restart, crash-recovery, and cold-start right after
bootstrap**. Two nodes that agree on the tip can, after a restart, come back in different
states — or one can crash on a life event the other survives. This report maps how each
implementation persists and recovers its ledger state, and identifies where they diverge — a
difference a tip-only oracle cannot see, but that decides whether a node survives an ordinary
restart or a fresh bootstrap.

## The two lifecycle models (from source)

### cardano-node — self-contained state, self-healing recovery

- **Storage: `ImmutableDB` (finalized, > k) + `VolatileDB` (recent, < k) + `LedgerDB`**
  (on-disk ledger-state snapshots + in-memory). The **stake distribution** (Shelley
  `mark`/`set`/`go` snapshots) is held *inside* the ledger state — the ledger state is
  self-contained.
- **Startup = replay + validate.** The ChainDB opens, replays blocks from the most recent
  usable ledger snapshot to rebuild the ledger/volatile state, and falls back to an older
  snapshot — ultimately to **genesis** — if the newest is missing or unusable. The VolatileDB
  is scanned and a corrupt/truncated tail is dropped. This is **self-healing**: a lost or
  stale snapshot costs replay time, not correctness.
- **Consequence:** cardano-node is fully operational the instant it is running. There is no
  "not enough history yet" state.

### Amaru — four stores, cross-consistency required, no from-genesis rebuild

Reading `crates/amaru-ledger/src/state.rs` and `crates/amaru/src/stages/build_node.rs`:

- **State is split across four stores that must stay cross-consistent:** a stable **RocksDB**
  store (state ≥ k old), an in-memory **volatile** diff sequence, a **separate per-epoch
  snapshot store**, and a **separate stake-distribution deque**.
- **It needs history to operate:** `MIN_LEDGER_SNAPSHOTS = 3`. Crossing an epoch boundary
  (`epoch_transition`) reads the **`N-2` snapshot** *and* the **`N-2` stake distribution**. This
  dependency is **real and load-bearing** — confirmed live: the fresh node's logs show
  `stake_distribution_epoch = current_epoch − 3` and snapshot pruning to a functional minimum at
  every boundary. **A bootstrap that supplies the required 3-snapshot history crosses boundaries
  cleanly** (the shipped bundle does exactly this — see the live run). A bootstrap that *lacks*
  that aligned history is the failure case.
- **The failure mode (when history is missing).** If the `N-2` stake distribution is absent,
  `StateError::NoSuitableStakeDistribution` bubbles up as a **roll-forward error on an
  otherwise-valid block** ("Failed to roll forward the ledger state") — or, in an older build, a
  crash. This does **not** occur on the normal bundle path (empirically verified); it is the
  hazard for non-standard / incomplete bootstraps.
- **Restart requires pre-populated, tip-aligned stores.** `amaru node run` reopens the
  existing stores and demands the ledger tip equal the chain-store tip
  (`build_node.rs`: *"Failed to create ledger. Have you bootstrapped your node?"* /
  *"ledger tip header not found"*). There is **no path that rebuilds the ledger by replaying
  from block 0** (ties to the bootstrap trust-source finding).
- **Invariant violations panic** rather than recover:
  `panic!("no tip found in stable db")`, `unreachable!("epoch transition reset did not
  succeed after first block!")`, `unreachable!("pre-condition: volatile.len() >= k")`.

## The differential

| | cardano-node | Amaru |
|---|---|---|
| State layout | one self-contained ledger state (stake dist. inside it) + Imm/Vol DBs | **four** stores (stable RocksDB + volatile diffs + epoch snapshots + stake-dist deque) that must stay consistent |
| Operational readiness | immediate | needs `MIN_LEDGER_SNAPSHOTS = 3` (shipped in the bundle) |
| Cross an epoch boundary right after bootstrap | fine | **clean** on the normal bundle path (verified live); fails only if the aligned `N-2` snapshot history is missing |
| Restart recovery | **replay** from newest usable snapshot → older → genesis; drop corrupt volatile tail | reopen existing stores; **must** be bootstrapped and tip-aligned; **no from-genesis rebuild** |
| Store inconsistency | ChainDB validates & truncates, self-heals | **panics** (`no tip found`, `unreachable!` ×2) |

**So the two implementations have a different operational-readiness lifecycle** — different
recovery guarantees and a bootstrap warmup window on one side only.

## Why it matters (no exploit — a model gap DWARF should test)

None of this is a vulnerability in itself; splitting state into snapshot/stake stores and
requiring a bootstrap snapshot are legitimate design choices. The finding is that **the two
nodes recover and warm up differently, and DWARF's convergence oracles cannot see it.**
Concretely:

- The `N-2` stake-distribution dependency is real and load-bearing. When the aligned snapshot
  history is present (the normal bundle path) Amaru crosses boundaries cleanly — **verified live,
  49+ consecutive transitions, zero failures.** When it is *absent* (a non-standard/incomplete
  bootstrap), a **valid block fails to apply** — a liveness failure caused by bootstrap state,
  not the block — which a tip oracle sees only as "Amaru stopped advancing."
- Amaru **cannot self-heal from a lost/corrupt snapshot** by replaying from genesis the way
  cardano-node can; a damaged store surfaces as a **panic**, not a recovery.
- These behaviours appear only around **restart / bootstrap / epoch-boundary** events, which
  the current tip-convergence tests never exercise.

## Empirical result (live DWARF run, 2026-07-18)

Scenario `consensus-state-lifecycle-bootstrap-differential` (schema- + registry-valid). On the
`cardano_amaru` mesh we fresh-bootstrapped a real Amaru node (`amaru-relay-2`): stopped it, wiped
its RocksDB stores, restarted it. Its startup re-imported the trusted **bundle** — which ships
exactly `MIN_LEDGER_SNAPSHOTS = 3` snapshots (points at slots 124/246/366) — then began forward
sync from ~epoch 2.

- **Clean crossings:** the node crossed epoch boundaries **cleanly** while replaying (e.g.
  `epoch_transition … from=23 into=24`, `stake_distribution_epoch = current_epoch − 3`, snapshot +
  prune each boundary). **49+ consecutive transitions, 0 panics, 0 `NoSuitableStakeDistribution`,
  0 roll-forward errors, `restarts=0`.**
- **Stall (environmental):** full replay-to-tip did not complete — the post-reboot mesh was
  degraded (chain frozen ~1.5 h behind wall-clock; upstream relay dropped chainsync), so the node
  stalled at ~block 1234 on `no connections available to fetch blocks`. Not a lifecycle defect;
  control `amaru-relay-1` (intact stores) also sat at the frozen tip.

**Net:** the normal bootstrap path is robust; the recovery-model and no-from-genesis differences
below are the durable findings. Raw log excerpts:
`reports/consensus-state-lifecycle-evidence/logs/live-run-2026-07-18.txt`.

## What DWARF should add

A **lifecycle dimension** to the differential harness: for each node, drive
**restart mid-epoch**, **restart across an epoch boundary immediately after bootstrap**,
**`kill -9` mid-write then reopen**, and (later) **on-disk-format upgrade**; assert the node
returns on the *same* state and does not crash, and compare the two implementations. Amaru's
no-from-genesis recovery and cardano-node's replay-recovery are the first two behaviours to
encode. To probe the `N-2` failure mode directly, bootstrap Amaru from an **incomplete** snapshot
set (fewer than `MIN_LEDGER_SNAPSHOTS`) and assert it degrades cleanly rather than failing a valid
block. The live fresh-bootstrap run above is the seed test case.

Source citations and the observed-crash note are in
`reports/consensus-state-lifecycle-evidence/`.
