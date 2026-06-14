# SP3b Fix — Tx-Adversary Stability (stop the exit-1 crash-loop)

> Design spec. Status: approved (brainstorming gate). Date: 2026-06-11.
> Fixes a finding from SP3b's live run. Predecessor: SP3b
> (`2026-06-11-sp3b-txsubmission-adversary-design.md`). Block (SP3a-fix) and
> header (SP2) paths untouched.

## Problem (from the SP3b live run)

The SP3b tx run met its done-bar — `dwarf_served_mutated_tx` fired 126× (the node
decoded 126 mutated transactions). But the report also showed a real finding
that is **ours, not the node's**: `container: dwarf-adversary, exit code: 1`,
failing **12,300×**. The tx adversary **crash-loops**: it exits 1, `restart:
always` brings it back, it serves a few txs each life, repeats. This also drives
the `No Antithesis errors` (9) finding. The block-mode adversary (SP3a-fix,
responder-only `withServerNode`) is stable by contrast (local `docker inspect`
`RestartCount=0`), so the fault is specific to the tx path.

Local repro note: a testnet subset *without* the tx-generator cannot trigger the
crash — `getBaseTx` just loops `"captured block had no transactions; retrying"`
on empty blocks. The crash is **downstream of a successful serve**, so
reproducing it requires the tx-generator producing real txs.

Likely cause (tx path only; to be confirmed by the verification repro): the
provider does `SendMsgDone ()` after offering its tx → the **initiator #4
mini-protocol completes**, and on completion / peer disconnect
`withServerNode`'s `wait serverAsync` re-raises, propagating to `main` → exit 1.
There is no top-level exception handling, so any single peer disconnect is fatal.

## Goal

Keep the tx adversary process **alive across protocol completion and peer
disconnects** (no exit-1 crash-loop), while **preserving fuzz coverage** (serve
multiple mutated txs from one long-lived process, not regress to one).

## Approach (A: keep-alive + resilient)

Three changes, all in the tx path:

1. **Capture a batch of txs.** `BlockSource.getBaseTxs` (reuses `getBaseChain`'s
   captured blocks; `extractTxs` each; collects up to N
   `(GenTxId Block, SizeInBytes, GenTx Block)`). One-shot at startup. Bound the
   empty-block retry with a logged cap so it cannot spin silently.
2. **Provider offers the batch and never completes.** `txProviderClient` takes
   the list: each `RequestTxIds` offers the next not-yet-offered txid; once
   exhausted it **parks** — blocks on a `SingBlocking` request, replies
   `NonBlockingReply []` on a `SingNonBlocking` request — and **never sends
   `SendMsgDone`**, so the initiator #4 mini-protocol stays open. The node
   consumes + decodes each codec-mutated tx (`dwarf_served_mutated_tx` fires N×).
3. **Resilient server.** Wrap the `runServeTxSubmission` server run in a
   catch-and-retry loop (`try`/`catch` around `runAdversaryServerIR`, log, re-run)
   so any per-connection exception restarts the server **in-process** instead of
   propagating to `main`. The process never exits 1; docker `restart: always`
   becomes a safety net, not the crash mechanism.

## Components

- Modify: `src/DwarfAdversary/BlockSource.hs` — add `getBaseTxs` (batch of N txs
  from the captured chain's blocks). Keep `getBaseTx` (or reimplement it atop
  `getBaseTxs`). Bound/log the empty-block retry.
- Modify: `src/DwarfAdversary/TxSubmission/Client.hs` — `txProviderClient` takes a
  `[(GenTxId Block, SizeInBytes, GenTx Block)]`, offers sequentially, parks when
  exhausted, no `SendMsgDone`.
- Modify: `app/Main.hs` — `runServeTxSubmission` captures the batch via
  `getBaseTxs`, builds the list-provider, and wraps the server run in the
  catch-and-retry loop.
- Image → `dwarf-adversary:0.5.0`; generator `ADVERSARY_IMAGE` → `0.5.0`.

## Data flow

```
startup: getBaseTxs -> [tx1..txN]   (bounded retry until the chain has txs)
relay2 --txsubmission(#4)--> RequestTxIds   adversary: ReplyTxIds [txid_k]   (k advances)
relay2 --txsubmission(#4)--> RequestTxs      adversary: ReplyTxs  [mutate(tx_k)]   -> node decodes
... after N served: RequestTxIds (blocking) -> adversary parks (no Done) ; connection stays open
peer disconnect / mux exception -> caught by the resilient wrapper -> server re-run in-process (no exit 1)
```

## Testing / verification

1. **Local repro WITH the tx-generator (the gate).** Bring up the testnet
   including `tx-generator` (so the chain carries real txs), run the fixed tx
   adversary (`0.5.0`), and confirm **`docker inspect dwarf-adversary`
   `RestartCount` stays 0** over several minutes while it serves multiple txs
   (relay2 consumes them; the adversary log shows repeated serves, no exit). This
   is the cheap, full-visibility check the first run lacked.
2. **Selftest** (`tools/sp3b_selftest.sh`) still green (0 crash).
3. **Round-trip** — block + tx + header bundles generate + `docker compose
   config` parses; unit tests green.
4. **Re-submit live** (`0.5.0`): confirm `dwarf_served_mutated_tx` fires AND the
   `container: dwarf-adversary, exit code: 1` finding is gone (`No Antithesis
   errors` clears of the adversary exits).

## Out of scope

- Block (SP3a-fix `0.4.0`) and header (SP2) paths.
- The CF asteria-game / `Fault Injector` (no-faults) findings (not ours).
- Increasing tx fuzz *depth* beyond serving the captured batch (a follow-on if
  coverage warrants; not needed to clear the crash-loop finding).
