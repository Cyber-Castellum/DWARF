# SP3b — Txsubmission Adversary Mode (cardano-node tx CBOR)

> Design spec. Status: approved (brainstorming gate). Date: 2026-06-11.
> Predecessors: SP2 (header path), SP3a (block path). Completes SP3 Track A
> (cardano-node native adversary modes). Successor: SP3 Track B (amaru +
> differential, local-devnet only).

## Goal

Extend `dwarf-adversary` with a **txsubmission** mode so DWARF scenarios
targeting the standalone **transaction** decode path
(`cardano-node-cbor-decode-tx-body`, `-certificate`, `-auxiliary-data`)
generate native Antithesis tests that exercise the node's
tx-submission/mempool tx decoder on adversarial CBOR — completing native
coverage of all five cardano-node CBOR shapes.

**Done bar:** through a live Antithesis run via Moog (build → spike/selftest →
round-trip → image push → `create-test` → confirm the tx decoder fired
on-platform, no false-green).

## Why this is harder than block-fetch (and needs a spike)

For chain-sync (#2) and block-fetch (#3) the node *pulls* and the adversary
serves as a **responder** — the adversary stays responder-only. tx-submission
(#4) is inverted: to make the node *decode* an adversarial transaction, the
adversary must **offer** txs, i.e. run a tx-submission **initiator client**
(the adversary's current responder #4 is a *consumer* that pulls txids *from*
the node). This is a new mux role for the adversary (today its listen
application is responder-only). Per the Phase 3b precedent (the chain-sync
**server** role was the unknown that gated everything), **the first
implementation task is a gating spike**: prove the adversary's initiator #4
offers a tx that a real ouroboros consumer — then relay2 — accepts and decodes
locally. If it will not peer within reasonable effort, **STOP and fall back to
A2** (below), reporting the pivot.

Note on coverage overlap: SP3a's mutated *block* already makes the node decode
the tx-bodies/certs/aux-data *inside* it. SP3b's distinct value is the
**standalone tx-submission/mempool decode path** — a different decoder entry
point with its own validation — plus **sub-field-targeted** mutation so a
`certificate`/`auxiliary-data` scenario stresses that specific sub-decoder.

## Approaches (decided)

- **A1 (chosen):** real tx-submission initiator client — the adversary offers a
  captured, sub-field-mutated tx; relay2's responder #4 (consumer) decodes it.
  Faithful to the mempool decode path. Gated by the spike.
- **A2 (fallback, only if the spike fails):** embed the mutated tx inside a
  block-fetch-served block (reuse SP3a). Exercises the tx decoder via the
  *block* path, not mempool. Documented fallback; not built unless forced.
- **B1 (chosen):** capture a real tx by reusing `BlockSource.getBaseBlock`,
  decoding the block `Term`, and lifting one transaction's `Term` out.
- **C (chosen):** sub-field-targeted mutation via `mutateTxField`.

## Components (Haskell, `antithesis/components/dwarf-adversary`)

- **`DwarfAdversary/TxSubmission/Client.hs`** — a tx-submission2 client/provider:
  answers the node's `RequestTxIds` with the offered txid and `RequestTxs` with
  the (mutated) tx body. Mirrors the ouroboros tx-submission client; the exact
  client peer/constructor symbols are pinned against the installed
  `ouroboros-network-protocols` during the spike. The codec is the existing
  `codecTxSubmission2 encTxId decTxId encTx decTx` (already used by the
  responder #4), with the **encode (provide) side** carrying the mutation.
- **`DwarfAdversary/TxSubmission/Target.hs`** —
  `data TxField = WholeTx | Certificate | AuxData` and
  `mutateTxField :: TxField -> StdGen -> Double -> Term -> (Term, MutationInfo)`.
  Navigates the Conway tx `Term`: `WholeTx` mutates the whole `tx_body`;
  `Certificate` navigates to `tx_body` map key `4` (certificates); `AuxData`
  navigates to the tx's auxiliary-data element. Applies `Fuzz.mutateTerm` to the
  located sub-`Term` and splices it back — structural engine unchanged, only the
  target selected. On a navigation miss (field absent), falls back to `WholeTx`
  and records the fallback in `MutationInfo`.
- **`DwarfAdversary/TxSource.hs`** — `getBaseTx`: reuse
  `BlockSource.getBaseBlock` to capture a real block hermetically, decode the
  block `Term`, and lift one transaction's `Term`. Errors after retries; never
  serves a placeholder tx.
- **`ChainSync/Connection.hs`** (extend) — add an initiator-side tx-submission
  client on #4 for tx-submission mode, alongside the existing responders;
  generalize the listen application so the mode selects chain-sync / block-fetch
  / tx-submission wiring (extends the SP3a mode-parameterized `runChainSyncServer`).
- **`app/Main.hs`** — accept `--protocol txsubmission` and
  `--cbor-shape {tx-body|certificate|auxiliary-data}`; add `runServeTxSubmission`
  + `runTxSubmissionSelftest`; emit SDK `dwarf_tx_decoder_reachable` (Reachable)
  and `dwarf_served_mutated_tx` (Sometimes). No `Always`.

## Data flow

```
adversary --txsubmission(#4, initiator/provider)--> node (relay2 responder/consumer)
  node:  RequestTxIds        adversary: ReplyTxIds [txid]
  node:  RequestTxs [txid]   adversary: ReplyTxs  [mutateTxField(shape, captured tx)]
  node:  decode tx (+ certificate / auxiliary-data sub-decoders)
         -> clean reject or clean decode error  (tx decoder EXERCISED)
```

No topology change: `relay-dwarf-topology.json` already roots relay2 at the
adversary; the adversary opens #4 toward it.

## Generator (Python, `profile_manager/antithesis_generator.py`)

- Flip `cardano-node-cbor-decode-tx-body`, `-certificate`, `-auxiliary-data` to
  `{"protocol": "txsubmission", "shape": <shape>, "built": True}` in
  `ADVERSARY_MODES`. (`derive_adversary` already emits `--protocol`/`--cbor-shape`.)
- Bump the adversary image to `dwarf-adversary:0.3.0`.
- The three tx scenarios then generate native bundles end-to-end.

## Error handling

- `TxSource`/`getBaseTx` capture failure after retries → hard error; never a
  placeholder tx.
- Unknown `--cbor-shape` under `txsubmission` → clear error.
- amaru/differential still refused (Track B). The header/block paths are
  unchanged (back-compat verified by re-running their round-trips).

## Testing / done-bar (live run)

1. **Spike selftest** — the adversary's initiator #4 offers a tx; a real
   ouroboros tx-submission consumer drives against it and decodes-or-clean-errors,
   **0 crashes**. Gates everything else.
2. **FuzzSpec** — `mutateTxField` navigates to the correct sub-`Term` for each of
   `WholeTx`/`Certificate`/`AuxData` on a representative Conway-tx-shaped `Term`,
   mutates without crashing, and stays encodable; the fallback path is covered.
3. **Round-trip (cardano-box)** — the 3 tx bundles generate, `verify_generated_bundle`
   green, `docker compose config` parses; SP2 header + SP3a block round-trips stay green.
4. **Live Antithesis** — push `dwarf-adversary:0.3.0` + the testnet commit to
   `Cyber-Castellum/DWARF`, `moog create-test`, confirm on-platform the node's tx
   decoder is exercised (tx-submission offer + decode in tracer logs;
   `dwarf_tx_decoder_reachable` / `dwarf_served_mutated_tx` fire), no false-green.

## Out of scope (SP3b)

- Track B (amaru + differential) and extending the behavioral gate to
  runtime-substrate / mini-protocol devnet scenarios.
- The A2 fallback unless the spike forces it.
- Any mini-protocol other than tx-submission.
