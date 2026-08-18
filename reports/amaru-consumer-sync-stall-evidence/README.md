# Evidence — Amaru consumer sync stalls against a cardano-node peer (mixed-net; likely #736)

Supporting evidence for `dwarf/docs/finding-amaru-consumer-sync-stall.md`.

Amaru `10.11.0` (testnet_42, k=5) on the `cardano_amaru_dwarf` mixed devnet. An Amaru relay consuming the
honest cardano chain **catches up, then silently stops adopting blocks** — `restarts=0`, `status=running`,
CPU ~0%, no panic — while the cardano peer keeps producing. Reproduced twice at the same logical point
(~slot 8961 / epoch 70→71).

## Contents

- **`sync-stall-debug.log`** — `AMARU_LOG=debug` capture at the stall. Healthy mux traffic on the peer
  connection, then `ReceiveError: Connection reset by peer (os error 104)` → `connection died / removing
  peer` → a re-connect (`ConnectionId(2)`) that exchanges only keepalive-sized frames and **never resumes
  block delivery**. Last adopted tip stays at slot 8961; peer meanwhile at 12,197+.
- **`convergence-watch.log`** — 25-minute watch: cardano advances ~1,520 slots (11,275 → 12,799) while both
  Amaru relays stay pinned and never converge. `RESULT: timeout after 25min`.

## What is proven

- **Deterministic liveness stall** (not a crash): the Amaru consumer stops following the chain after a peer
  connection reset and does not resume, with no error surfaced to an operator — the container still reads
  "running".
- **Concrete trigger:** an everyday `Connection reset by peer (os error 104)` on the peer link (Amaru's
  `role=responder` read fails — consistent with a cardano-node mini-protocol timeout), after which the
  chain-sync consumer wedges.
- **Consistent with open upstream `#736`** (Amaru ↔ cardano-node mixed-net peer sync); DWARF adds the
  reproducible trigger.

## Distinct from the restart crash

This is the live-sync stall (silent, no restart). The restart-recovery crash (`RollbackPointInFuture` →
"Consensus died") is separate — see `reports/amaru-restart-rollback-evidence/`. They compound: a stalled
node that is then restarted hits the restart crash.
