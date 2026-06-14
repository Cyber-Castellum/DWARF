# SP3a Fix — Block-Adoption (make the node actually block-fetch the mutated block)

> Design spec. Status: approved (brainstorming gate). Date: 2026-06-11.
> Fixes a partial result in SP3a (block-fetch adversary mode). Predecessor:
> SP3a (`2026-06-11-sp3a-blockfetch-adversary-design.md`). Header path (SP2)
> and tx path (SP3b) are untouched.

## Problem (confirmed by local repro on cardano-box)

SP3a's live Antithesis run was a **partial pass**: the adversary deployed and a
node connected (`dwarf_node_connected` fired), but `dwarf_served_mutated_block`
never fired — the block-body decoder was not exercised on mutated input.

A local `docker compose up` of the `cardano_node_dwarf` testnet with relay2
**reset to genesis** reproduced the failure exactly: relay2 connects to the
adversary and enters a tight loop — `MsgFindIntersect` -> 3x `MsgRequestNext`
-> disconnect -> reconnect, repeating — and adds **zero** blocks to its ChainDB.
A fresh node cannot advance past genesis against the current adversary.

Three serve-path defects cause this (the earlier "disconnected tip headers"
hypothesis was WRONG: `getBaseHeaders` syncs from `originPoint`, so the captured
headers are genesis-contiguous):

1. **Wrong body served.** `servingBlockFetchResponder` returns **one fixed
   captured block** (the last captured, ~block 5) for *any* requested point.
   relay2 reads 3 headers (its forecast window from genesis), block-fetches
   block 1's body, receives block 5's body -> body-hash mismatch -> rejects and
   disconnects -> reconnect loop. This is the primary killer.
2. **Too few headers.** Only 5 headers are captured (`getBaseHeaders … 5`) —
   too few for relay2 to advance past the genesis forecast / stability stall
   even once bodies are correct.
3. **Cycling (latent).** `chainSyncServer` does `stream hs = cycle hs`; after
   the captured headers it re-serves header 1 as a "roll-forward" (a roll to a
   block behind the tip with no `MsgRollBackward`) — an invalid sequence that
   would break adoption if sync reached the end of the list.

For the SP2 **header** path none of this mattered: the node decodes each header
on chain-sync *receipt*, regardless of adoption or block-fetch. The **block**
path needs a stable, adoptable chain *and* correct bodies to trigger and survive
block-fetch.

## Goal

Make a fresh node that roots only at the adversary **adopt the chain, advance,
and block-fetch bodies**, decoding adversarially-mutated block CBOR — turning
SP3a's partial pass into a full one (`dwarf_served_mutated_block` fires; the
block decoder runs on mutated input on-platform), verified first by the local
repro (fresh relay2 adds blocks; the adversary serves the right mutated bodies).

## Approach (A1, refined by the repro)

Block-fetch mode serves a **longer, contiguous, genesis-anchored chain** the
node will adopt and advance along, serves the **correct body per requested
point** (mutated), and **does not cycle**.

## Components

1. **Real-relay chain-sync server** (block-fetch mode; `Server.hs`).
   - Capture **many** real headers from genesis (`getBaseHeaders` with a large
     limit, e.g. enough to clear the stability window) instead of 5.
   - Serve them contiguously via `SendMsgRollForward`, and at the end of the
     list `SendMsgAwaitReply` (park) — **no `cycle`**, so the node keeps a
     stable chain rather than seeing an invalid roll-back-to-front.
   - Intersection at the client's offered point (genesis for a fresh node) is
     already correct for a genesis-contiguous list.
   - The existing fake-intersection/cycling `chainSyncServer` stays for the SP2
     header path (`--protocol chainsync`).
2. **Point-aware mutating block-fetch responder** (`Connection.hs`).
   On `MsgRequestRange(lo, hi)`, serve the **real body for each requested
   point** (not a fixed block), mutated by the block-fetch codec at rate < 1.
   Body source: a **point->Block map captured alongside the chain** (fetch each
   captured header's block once at startup via `fetchBlock`), looked up per
   requested point. (Rate < 1 means the node adopts unmutated blocks — advancing
   the immutable tip so sync continues — and decodes mutated ones, which fail
   body-hash and are rejected post-decode, but the decoder already ran ->
   `dwarf_served_mutated_block` fires.)
3. **`runServeBlockFetch`** (rewire): capture the chain + point->Block map ->
   serve via the real-relay chain-sync server (#1) -> serve correct mutated
   bodies via the point-aware responder (#2).

## Data flow

```
relay2 --chainsync--> adversary: MsgFindIntersect [genesis, ...]
adversary: SendMsgIntersectFound (real intersection)
relay2 --chainsync--> RequestNext (xN)   adversary: real contiguous headers
relay2: adopts the chain (candidate fragment extends its tip)
relay2 --blockfetch--> MsgRequestRange (point P)
adversary: look up real block P in the captured point->Block map -> mutate body -> serve
relay2: decode block body  (DECODER EXERCISED)  -> adopt (unmutated) or reject (mutated, hash mismatch)
```

## Error handling

- Full-chain capture failure → hard error (no placeholder chain), as today.
- Proxy-fetch of a requested point's body fails → serve no-blocks for that
  range and log; do not crash.
- Header-fuzz (`--protocol chainsync`) and tx (`--protocol txsubmission`) paths
  are unchanged; their selftests/round-trips must stay green.

## Testing / verification

1. **Unit selftest** — a real block-fetch client requests a range against the
   block-mode server and receives a served (mutated) body; 0 crashes.
2. **Local testnet repro (cardano-box, the key new gate)** — `docker compose up`
   the `cardano_node_dwarf` testnet with the fixed adversary; confirm in the
   logs that **relay2 sends `MsgRequestRange` to the adversary and the adversary
   serves a (mutated) block** (i.e. the real `dwarf_served_mutated_block` path
   runs). This is the cheap, full-visibility check that was missing before any
   Antithesis submission.
3. **Round-trip** — block bundle still generates + `verify_generated_bundle`
   green + `docker compose config` parses; header + tx round-trips stay green.
4. **Re-submit live** — push the new adversary image + testnet commit, launch
   via Moog, confirm on-platform `dwarf_served_mutated_block` fires and the
   block decoder is exercised (no false-green).

## Out of scope

- Header (SP2) and tx (SP3b) paths.
- A live *bidirectional* chain-sync/block-fetch proxy that follows p1's chain as
  it grows in real time (a one-shot full-chain capture at startup is sufficient
  for a bounded run; revisit only if the repro shows the chain outgrows the
  captured snapshot within the run).
- The `--no-faults` / asteria-game / infra findings seen in the first run (not
  ours).
