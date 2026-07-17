# Consensus differential evidence

Persisted summaries + heartbeats + harness backing the two consensus differential
campaign reports in `../campaign-reports/`:

- `dwarf-consensus-chain-selection-differential-campaign.html`
- `dwarf-consensus-long-range-attack-differential.html`

These test the highest-value consensus question node-agnostically: **does the
Haskell `cardano-node` ever disagree with the Rust `Amaru` node on which chain is
canonical, or on the security-parameter `k` rollback bound?** A disagreement is a
network split — the highest-value failure. Substrate: the real upstream
`cardano_amaru` mesh (k=5, epoch 125 slots, active-slot-coeff 0.2, Conway at
epoch 0).

## `logs/` — per-run summaries + heartbeats (from `consensus_4h_runner.py`)

4-hour soak per scenario; each `*.summary.json` is the final tally, each
`*.heartbeat.jsonl` the periodic progress log.

| Scenario | iters | pass | fail |
|---|---|---|---|
| `run4h-chainhold-upstream` (induced fork + reorg) | 80 | 80 | 0 |
| `run4h-epoch-boundary` (epoch transition) | 94 | 94 | 0 |
| `run4h-rollback` (within-k partition recovery) | 47 | 47 | 0 |
| `run4h-stage1` (native Amaru relay, VRF tiebreak) | 154 | 145 | 9&dagger; |
| `run4h-threshold` (private-fork-under-delay, cardano-only) | 91 | 91 | 0 |
| **Total** | **466** | **457** | **9&dagger;** |

&dagger; The 9 stage1 flags are **benign, not divergences**: the two Amaru relays
were 1 slot apart from *each other* at the sampling instant (single-slot
propagation jitter) — cardano-node and Amaru still agreed. Fixed for future runs
by targeting a single native Amaru relay; the conclusion is unchanged. **Zero
genuine cardano-node↔Amaru divergences across all 466 iterations.**

- `longrange-deep-rollback-soak.log` — the long-range deep-rollback differential
  soak (~3 h): the adversary repeatedly injects an exact 10-block (> k) rollback
  once each eclipsed node is caught up; both `cardano-node` and `Amaru` **refuse
  every time** (tip never regresses > k). 91 injections into cardano-node, 92 into
  Amaru, **0 regressions, 0 divergences**.

## `scenario-reports/` — per-scenario write-ups

`consensus-report-{chainhold,epoch-boundary,stage1-tiebreak,threshold-delay-probe,
long-range}.md` — method + result + Antithesis-worthiness for each.

## `scripts/` — harness

- `consensus_4h_runner.py` — loops a DWARF scenario for a wall-clock duration
  (JSONL heartbeat + final summary; bounded disk — passing bundles pruned, failing
  kept).
- `docker-compose.lr-eclipse.yaml`, `relay-lr-topology.json` — the additive
  eclipse override for the long-range harness (a target node's only peer is the
  chain-serving adversary).
- `DwarfAdversary.ChainSync.Server.deep-rollback.hs` — the chain-serving
  adversary's `deepRollbackChainSyncServer`: after the eclipsed node catches up,
  inject one `MsgRollBackward` `--rollback-depth` blocks (> k) behind the served
  head; `--rollback-repeat-secs` re-arms it for the soak.
- `watch_rb2.sh`, `watch_amaru_rb.sh` — the differential oracles (tip never
  regresses > k, per implementation).

Also bundled as `consensus-differential-evidence.tar.gz`.
