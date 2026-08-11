# Evidence — Amaru restart-recovery crash (`RollbackPointInFuture` / "Consensus died")

Supporting evidence for `dwarf/docs/finding-amaru-restart-rollback-in-future-crash.md`.

Amaru `10.11.0` (testnet_42, k=5) on the `cardano_amaru_dwarf` mixed devnet. An Amaru relay that was
**synced to the live cardano chain** (adopted tip slot 8961) was restarted and **died on startup** with
`attempted roll back in the future` → `Consensus died`, then crash-looped. Still present in
`v10.11.20260807` (source-verified); 807 adds only a *manual* `amaru node rollback` remediation.

## Contents

- **`relay1-restart-crash.log`** — the crash: on restart the ledger's `volatile_tip = immutable_tip = 8870`
  while the chain store / peer offers a rollback to `8961` (~91 slots ahead) → `RollbackPointInFuture`
  (`amaru-ledger/src/state.rs:958`) → fatal (`cmd/node/run.rs:436`).

## Root cause (source)

Chain store and ledger store are **separate** DBs. Unclean stop → chain store persists ahead of the
ledger. Restart reconciliation asks the ledger to move to the chain tip; `rollback_to` rejects a target
`> volatile_tip` as "in the future" instead of truncating the chain store back to the ledger's immutable
tip. cardano-node self-heals (replay-from-snapshot/truncate); Amaru asserts and dies. Same family as the
state-lifecycle differential (Gap #4).

## What is proven

- **Deterministic crash on restart** of a synced node (restarts climbed to 9 in ~1 min); recovery needed a
  state wipe + re-bootstrap.
- **Known failure mode, manual-only fix in 807** — the new `amaru node rollback` ("Roll the chain store
  back to the ledger's immutable tip") targets exactly this divergence but must be run by hand; the node
  does not self-heal.

## Related (separate, not root-caused)

Same relay also **silently hung** during live sync (reproducibly ~slot 8961 / epoch 70→71), `restarts=0`,
no error, no new log lines, while its cardano peer kept producing (peer served up to slot 11533+). A
distinct symptom from the crash (stall, not panic); possibly the open mixed-net peer-sync issue (upstream
#736). Logged in the finding doc's "Open" section.
