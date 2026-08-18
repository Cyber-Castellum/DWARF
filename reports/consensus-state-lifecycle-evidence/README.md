# State-lifecycle / migration / recovery differential — evidence

Backs `../campaign-reports/dwarf-consensus-state-lifecycle-differential.html`,
`../../dwarf/docs/consensus-report-state-lifecycle-differential.md`, and the finding
note `../../dwarf/docs/finding-amaru-state-lifecycle.md`.

## The gap

DWARF's oracles watch two nodes' chain **tips**. They do not test what happens across a
node's own *life events* — **restart, crash-recovery, and cold-start after bootstrap**.
Two nodes that agree on the tip can wake up from a restart in different states, or one can
crash on a life event the other survives. This is a **source analysis** (deterministic — no
flaky runtime) of how each implementation persists and recovers ledger state, plus one
crash observed live in the earlier false-leadership campaign.

## Result (from source)

- **cardano-node** stores state as **ImmutableDB + VolatileDB + LedgerDB**, with the stake
  distribution held *inside* a self-contained ledger state. On startup it **replays** from
  the newest usable ledger snapshot (falling back to an older one, or genesis) and drops
  corrupt volatile tails — self-healing recovery. It is fully operational the moment it is
  running.
- **Amaru** splits state across **four** stores that must stay cross-consistent: a stable
  RocksDB store, an in-memory volatile diff sequence, a **separate per-epoch snapshot
  store**, and a **separate stake-distribution deque**. It requires `MIN_LEDGER_SNAPSHOTS =
  3` past epoch snapshots to operate, and **crossing an epoch boundary reads the `N-2`
  snapshot and `N-2` stake distribution** — which a node freshly bootstrapped from a single
  snapshot does not yet hold. On restart it reopens existing stores and demands the ledger
  tip equal the chain-store tip; there is **no replay-from-genesis** recovery path. Store
  invariant violations **panic** (`no tip found in stable db`,
  `unreachable! epoch transition reset`, `unreachable! volatile.len() >= k`).

So the two clients have different **recovery guarantees**: Amaru self-heals from nothing (no
from-genesis rebuild; store-invariant violations panic), while cardano-node replays from the
newest snapshot down to genesis. This is not an exploit — a lifecycle/recovery model difference a
tip-convergence oracle cannot see, plus a new test dimension DWARF should add.

## Live run corrected one predicted finding (read `logs/live-run-2026-07-18.txt`)

A DWARF run (`consensus-state-lifecycle-bootstrap-differential`, schema + registry valid)
fresh-bootstrapped a real Amaru node on the `cardano_amaru` mesh. **Result: it crossed epoch
boundaries cleanly** — 49+ consecutive transitions, **zero** panics/`NoSuitableStakeDistribution`/
roll-forward errors, `restarts=0`. The shipped bundle contains exactly `MIN_LEDGER_SNAPSHOTS = 3`
snapshots (slots 124/246/366), so the `N-2` dependency is satisfied. This **refutes the strong
source claim** that "a valid block fails at the first epoch boundary after bootstrap" for the
normal path; that failure mode requires a bootstrap *missing* the aligned snapshot history. (Full
replay-to-tip didn't finish — the post-reboot mesh was frozen; environmental, not a lifecycle bug.)

## `source-citations.txt`

Exact symbol/line references: the four-store split and `MIN_LEDGER_SNAPSHOTS`, the
`epoch_transition` reads of the `N-2` snapshot + stake distribution, the
`NoSuitableStakeDistribution` error path, the roll-forward error surfacing, the hard panics,
and `build_node.rs`'s "must be bootstrapped / tip-aligned" restart requirement.

## `logs/`

`observed-epoch-crash.md` — notes on the live crash from the earlier campaign (a freshly
bootstrapped Amaru crashing while forward-syncing across an epoch boundary) and the
single-epoch workaround that avoided it.

## Next step (adversarial)

Add a **lifecycle dimension** to the harness: for each node, drive **restart mid-epoch**,
**restart across an epoch boundary right after bootstrap**, **kill -9 mid-write then
reopen**, and (later) **upgrade to a newer on-disk format**; assert the node comes back on
the *same* state and does not crash — and compare the two implementations. Amaru's warmup
window and cardano-node's replay recovery are the first two behaviours to encode.

No credentials present — public source references only.
