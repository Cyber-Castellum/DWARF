# SP3a — Blockfetch Adversary Mode (cardano-node block CBOR)

> Design spec. Status: approved (brainstorming gate). Date: 2026-06-11.
> Predecessors: SP2 (native test generator — chainsync header path). Part of SP3
> Track A (additional adversary mini-protocol modes). Successor: SP3b (txsubmission
> shapes: tx-body / certificate / auxiliary-data), then Track B (amaru + differential).

## Goal

Extend `dwarf-adversary` with a **blockfetch** mini-protocol mode so a DWARF
cardano-node scenario targeting the **block** CBOR decoder
(`cardano-node-cbor-decode-block`) generates a native Antithesis test
end-to-end and exercises the node's block-body decoder on adversarial input —
the direct generalization of the proven chainsync header path.

**Done bar:** through a live Antithesis run via Moog (build → selftest →
generator round-trip → image push → `moog create-test` → confirm the block
decoder is exercised on-platform, no false-green).

## Scope

- **blockfetch / block shape only.** txsubmission (tx-body, certificate,
  auxiliary-data) is SP3b. amaru/differential is Track B.
- Reuses the proven architecture: `Fuzz.mutateTerm` (same mutation engine),
  the `ChainSync/MutatingCodec` pattern, the `HeaderSource` hermetic-capture
  pattern, the SDK assertion conventions.

## Why blockfetch needs the chainsync machinery too

In the node-to-node protocol, **chainsync** delivers headers (mini-protocol #2)
and **blockfetch** delivers block bodies (mini-protocol #3) over **one
multiplexed connection** (`NodeToNodeV_14`). The node will not blockfetch a body
it has no header for. So to make the node run its block-body decoder on
adversarial bytes, the adversary must:

1. advertise a **real, unmutated** header via chainsync (so the node accepts the
   chain and requests the body), and
2. serve a **Term-level structurally-mutated** block body via blockfetch when the
   node requests it.

The node must CBOR-decode the body **before** it can compute and check the body
hash, so a mutated body genuinely engages the decoder; it then fails the
body-hash check or fails to decode — both are clean (no crash). `Connection.hs`
already imports `blockFetchServerPeer` and registers chainsync as responder #2
via `chainSyncToResponder`, so this is "register a second responder (#3) on the
same mux."

## Components (Haskell, `antithesis/components/dwarf-adversary`)

- **`DwarfAdversary/BlockFetch/Codec.hs`** — blockfetch protocol codec (block
  (de)serialization), mirroring `ChainSync/Codec`.
- **`DwarfAdversary/BlockFetch/MutatingCodec.hs`** — wraps the encode side to
  apply `Fuzz.mutateTerm` to the block CBOR on the wire before `MsgBlock`,
  mirroring `ChainSync/MutatingCodec`. Single source of fuzz nondeterminism:
  seeded `StdGen` only (no clock / entropy), so findings are seed-reproducible.
- **`DwarfAdversary/BlockFetch/Server.hs`** — blockfetch responder: on a range
  request reply `MsgStartBatch` → `MsgBlock`(mutated body) → `MsgBatchDone`.
- **`DwarfAdversary/BlockSource.hs`** — hermetically capture one real base block
  from the in-bundle node (chain-sync the header, then blockfetch the real body
  from `p1`), retrying like `HeaderSource`. Errors after retries; never serves an
  empty/placeholder block.
- **`ChainSync/Connection.hs`** — generalize `chainSyncToResponder` into a
  responder that registers **#2 chainsync** (advertising the captured real
  header) **and #3 blockfetch** (serving the mutated body) on the same
  `SomeResponderApplication`.
- **`app/Main.hs`** — add `--protocol {chainsync|blockfetch}` (default
  `chainsync`, back-compat) and `--cbor-shape {block-header|block}`; in
  blockfetch mode wire the combined responder + `BlockSource`.
- **`DwarfAdversary/SDK.hs`** — reuse; emit `Reachable` ("node block decoder ran
  on an adversarial block") + `Sometimes` ("node cleanly rejected a
  structurally-mutated block"). No `Always` (harness can chaos-kill the rig).

## Data flow

```
node --chainsync(#2)--> adversary: MsgRollForward(real header for point P)
node --blockfetch(#3)--> adversary: MsgRequestRange(P,P)
adversary --blockfetch--> node: MsgStartBatch, MsgBlock(mutateTerm(real block)), MsgBatchDone
node: CBOR-decode block body  (decoder EXERCISED)
   -> decode ok then body-hash mismatch (clean reject), or decode clean-error
```

No topology change: `relay-dwarf-topology.json` already roots relay2 at the
adversary, so the node both chain-syncs and blockfetches from it.

## Generator side (Python, `profile_manager/antithesis_generator.py`)

- Flip `cardano-node-cbor-decode-block` to `{"protocol": "blockfetch",
  "shape": "block", "built": True}` in `ADVERSARY_MODES`.
- Extend `derive_adversary` to emit `--protocol <p>` and `--cbor-shape <s>` from
  the mode entry, **uniformly for every mode** (including chainsync/block-header).
- Bump the adversary image to `dwarf-adversary:0.2.0` for **all** modes. `0.2.0`
  accepts `--protocol` (default `chainsync`) and `--cbor-shape`, so emitting the
  flags uniformly is safe; `0.1.0` did not accept them, which is why the image
  bump and the flag emission ship together. Back-compat is verified behaviorally,
  not by byte-identical command: SP2's header round-trip is re-run against `0.2.0`
  + the new flags and must stay green (the header path's served bytes are
  unchanged).
- The block scenario `cardano-node-cbor-block-fuzz-structured.yaml` then
  generates a native bundle end-to-end (its `target_id` is
  `cardano-node-cbor-decode-block`).

## Error handling

- `BlockSource` capture failure after retries → hard error; never serve an empty
  or placeholder block.
- Back-compat: `--protocol chainsync` (default) preserves the header path's served
  bytes; the SP2 header round-trip is re-run against `0.2.0` and must stay green.
- Generator still refuses txsubmission shapes (tx-body/certificate/auxiliary-data
  remain `built: False`) with the named follow-on-build error.

## Testing

1. **`test/FuzzSpec.hs`** — extend for block-Term mutation: each mutation kind on
   a decoded base block yields output that is decodable-or-clean-fail (no
   Haskell-side crash), parallel to the header tests.
2. **`--selftest`** — a real Ouroboros blockfetch client drives the adversary:
   requests a range, receives mutated blocks, the client decoder
   decodes-or-clean-errors, **0 crashes**. Proves the combined chainsync+blockfetch
   responder completes the N2N handshake and protocol locally before Antithesis.
3. **Generator round-trip (cardano-box)** — block bundle generates,
   `verify_generated_bundle` green, `docker compose config` parses,
   `moog asset validate` OK; SP2 header round-trip re-run stays green.
4. **Live Antithesis run** — build/push `dwarf-adversary:0.2.0`, generate the
   block bundle, `moog create-test`, confirm on-platform the node's block decoder
   is exercised (blockfetch + decode in tracer logs), no false-green.

## Out of scope (SP3a)

- txsubmission shapes (tx-body / certificate / auxiliary-data) — SP3b.
- amaru + differential scenarios — Track B.
- Any change to the chainsync header path beyond the back-compat-preserving
  responder generalization.
