# SP3b — Txsubmission Adversary Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `dwarf-adversary` offer a captured, sub-field-mutated transaction over tx-submission (mini-protocol #4) so a DWARF scenario targeting `cardano-node-cbor-decode-{tx-body,certificate,auxiliary-data}` generates a native Antithesis test that exercises the node's tx-submission/mempool tx decoder.

**Architecture:** The adversary must act as a tx **provider** — a tx-submission2 **initiator client** on #4 (it currently runs responder-only mini-protocols via `withServerNode`). **Task 1 is a gating spike** that resolves whether the adversary's inbound-connection server can run an initiator mini-protocol; if not within reasonable effort, **STOP and pivot to A2** (embed the mutated tx in a block-fetch-served block — pure responder, reuses SP3a). Downstream tasks add sub-field-targeted mutation (`mutateTxField`), tx capture (reuse `BlockSource`), generator wiring, and a live Antithesis run.

**Tech Stack:** Haskell (ghc-9.6.7 + CHaP, cabal) on cardano-box; Python 3 stdlib generator; Docker + Moog for the live run.

**Spike-first discipline (Phase 3b precedent):** the genuinely unknown part is initiator-on-inbound-connection N2N wiring. Do **Task 1** before any downstream A1 wiring. Its deliverable is *compiling initiator-#4 code + local evidence a real tx-submission consumer decodes an offered tx*. If the spike shows the server stack will not run an initiator mini-protocol within reasonable effort, STOP, report, and switch to the A2 fallback (a separate, smaller plan).

**Grounding facts (verified in code/REPL):**
- tx-submission2 provider API (`Ouroboros.Network.Protocol.TxSubmission2.Client`): `TxSubmissionClient { runTxSubmissionClient :: m (ClientStIdle txid tx m a) }`; `ClientStIdle { recvMsgRequestTxIds, recvMsgRequestTxs :: [txid] -> m (ClientStTxs ...) }`; `ClientStTxIds`: `SendMsgReplyTxIds (BlockingReplyList blocking (txid,SizeInBytes)) (ClientStIdle ...)` | `SendMsgDone a`; `ClientStTxs`: `SendMsgReplyTxs [tx] (ClientStIdle ...)`; `txSubmissionClientPeer`. `BlockingReplyList`: `BlockingReply (NonEmpty (txid,size))` | `NonBlockingReply [(txid,size)]`.
- The codec already exists: `Connection.hs` uses `codecTxSubmission2 encTxId decTxId encTx decTx` for the existing responder #4 stub `txSubmissionResponder`; `Codec.hs` exports `encTx`/`decTx`/`encTxId`/`decTxId`/`GenTx`/`GenTxId`. `GenTx Block` / `GenTxId Block` are the tx / txid types.
- SP3a left `runChainSyncServer magic port onAccept csCodec csServer bfCodec bfServer` (responder-only, `withServerNode` + `SomeResponderApplication`). tx-submission needs a different (initiator-responder) server — built in Task 1.
- `BlockSource.getBaseBlock :: (String->IO()) -> NetworkMagic -> (String,Int) -> IO Block` (SP3a) — reused for capture.
- `Fuzz.mutateTerm :: StdGen -> Double -> Term -> (Term, MutationInfo)`; `MutationInfo {miKind, miDepth}`.
- Adversary image is `dwarf-adversary:0.2.0` (SP3a); SP3b → `0.3.0`. Build/run on cardano-box (`export PATH=$HOME/.ghcup/bin:$PATH`; existing `dist-newstyle` cache; selftest port 3999 to avoid conflicts).

---

## File Structure

- Create: `src/DwarfAdversary/TxSubmission/Client.hs` — tx-submission2 provider client (offer txid, serve tx).
- Create: `src/DwarfAdversary/TxSubmission/Target.hs` — `TxField` + `mutateTxField` (Conway-tx-layout sub-field targeting).
- Create: `src/DwarfAdversary/TxSource.hs` — `getBaseTx` (lift a tx `Term` from a captured block).
- Modify: `src/DwarfAdversary/ChainSync/Connection.hs` — initiator-responder server for tx-submission mode (built/validated in Task 1).
- Modify: `app/Main.hs` — `--protocol txsubmission`, `--cbor-shape`, serve + selftest, SDK asserts.
- Modify: `dwarf-adversary.cabal` — add the three modules.
- Modify: `test/FuzzSpec.hs` — `mutateTxField` navigation properties.
- Modify: `dwarf/profile_manager/antithesis_generator.py` — 3 tx shapes `built: True`, image `0.3.0`.
- Modify: `tools/test_sp2_generator.py` — tx-shape build tests + regression.
- Create: `tools/sp3b_selftest.sh`, `tools/sp3b_roundtrip.sh`.

---

## Task 1: SPIKE — initiator-#4 tx provider on the inbound connection (GATING)

**Goal:** prove the adversary can run a tx-submission2 **initiator client** (provider) on mini-protocol #4 toward a connected node, and that a real ouroboros tx-submission **consumer** receives + decodes an offered tx. This resolves the one hard unknown before any further work.

**Files:**
- Create: `src/DwarfAdversary/TxSubmission/Client.hs`
- Modify: `src/DwarfAdversary/ChainSync/Connection.hs` (add an initiator-responder server entry point)
- Modify: `app/Main.hs` (a temporary `--protocol txsubmission --selftest` path)

- [ ] **Step 1: Write the provider client (`TxSubmission/Client.hs`)**

```haskell
{-# LANGUAGE OverloadedStrings #-}

-- | A tx-submission2 client (PROVIDER): offers one txid, then serves the
-- supplied tx body on request. The tx is supplied already-encoded/decoded as
-- a GenTx; mutation is applied by the caller before constructing the client.
module DwarfAdversary.TxSubmission.Client
    ( txProviderClient
    ) where

import Data.List.NonEmpty (NonEmpty ((:|)))
import DwarfAdversary.ChainSync.Codec (Block, GenTx, GenTxId)
import Ouroboros.Network.Protocol.TxSubmission2.Client
    ( ClientStIdle (..)
    , ClientStTxIds (SendMsgReplyTxIds)
    , ClientStTxs (SendMsgReplyTxs)
    , TxSubmissionClient (TxSubmissionClient)
    )
import Ouroboros.Network.Protocol.TxSubmission2.Type
    ( BlockingReplyList (BlockingReply, NonBlockingReply)
    , SizeInBytes
    )

-- | Offer exactly one tx (txid + body). After it is requested + sent, reply
-- empty to further txid requests so the protocol stays well-formed.
txProviderClient
    :: (String -> IO ())
    -> GenTxId Block
    -> SizeInBytes
    -> GenTx Block
    -> TxSubmissionClient (GenTxId Block) (GenTx Block) IO ()
txProviderClient log_ txid size tx = TxSubmissionClient (pure (idle True))
  where
    idle offer = ClientStIdle
        { recvMsgRequestTxIds = \_blocking _ack _req ->
            if offer
                then do
                    log_ "offering 1 txid"
                    pure (replyIds (BlockingReply ((txid, size) :| [])))
                else pure (replyIds (NonBlockingReply []))
        , recvMsgRequestTxs = \_requested -> do
            log_ "serving tx body"
            pure (SendMsgReplyTxs [tx] (idle False))
        }
    replyIds rl = SendMsgReplyTxIds rl (idle False)
```

> Confirm against the installed module during the spike: the exact arity of `recvMsgRequestTxIds` (blocking-style GADT), `SizeInBytes` location, and whether `SendMsgReplyTxIds` needs the blocking/non-blocking witness. Adjust to the real signatures — the REPL browse in this plan's grounding is the reference. The `idle`/`offer` loop shape may need tweaking so the blocking vs non-blocking reply matches the requested style.

- [ ] **Step 2: Add an initiator-responder server entry point to `Connection.hs`**

The current `runChainSyncServer` uses `withServerNode` + `SomeResponderApplication` (responder-only). Investigate, in order, the lowest-effort way to also run the initiator #4 client on inbound connections:

1. Try an `OuroborosApplicationWithMinimalCtx Mx.InitiatorResponderMode` application that registers the existing responders (#2/#3/#8 + responder #4) **plus** an initiator #4 running `txSubmissionClientPeer (txProviderClient ...)`, passed to `withServerNode` (check whether `withServerNode`/`SomeResponderApplication` can carry a duplex app, or whether a sibling constructor exists).
2. If `withServerNode` is responder-only, evaluate the connection-manager / `Ouroboros.Network.Socket` server API used elsewhere in ouroboros for a duplex inbound app.

Add `runAdversaryServerIR` mirroring `runChainSyncServer` but with the InitiatorResponder application. Keep `runChainSyncServer` unchanged (chainsync/blockfetch paths).

- [ ] **Step 3: Wire a temporary spike selftest in `Main.hs`**

Add a `--protocol txsubmission --selftest` path: start `runAdversaryServerIR` offering a trivially-constructed or block-captured tx, then run a real tx-submission **consumer** (`txSubmissionServerPeerPipelined` driven as a client, or the ouroboros consumer) against `127.0.0.1`, and log whether it received + decoded the tx. (A fabricated `GenTx` may be hard to build — prefer capturing one via `TxSource` from Task 3 if needed; for the spike a block-captured tx is acceptable, or defer the real tx to Task 3 and offer a minimal valid tx if constructible.)

- [ ] **Step 4: Build + run the spike on cardano-box**

```bash
ssh cardano-box 'cd <repo>/antithesis/components/dwarf-adversary && export PATH=$HOME/.ghcup/bin:$PATH && cabal build exe:dwarf-adversary'
ssh cardano-box 'cd <repo> && BIN=$(cd antithesis/components/dwarf-adversary && cabal list-bin dwarf-adversary) && timeout 60 "$BIN" --selftest --protocol txsubmission --listen-port 3999 --seed 0x1 2>&1 | tee /tmp/sp3b-spike.log'
```
Expected: handshake completes, the consumer logs "received/decoded a tx", **no crash**.

**GATE / STOP criterion:** if after reasonable effort the inbound-connection initiator #4 does not work (e.g. `withServerNode` cannot run a duplex app and the connection-manager path is disproportionately large), **STOP**. Record the finding, mark SP3b A1 blocked, and write a short A2 plan (embed the `mutateTxField`-mutated tx inside the SP3a block-fetch-served block — pure responder, no new mux role). Do not proceed to Tasks 4–9 on the A1 path.

- [ ] **Step 5: Commit the spike**

```bash
git add antithesis/components/dwarf-adversary/src/DwarfAdversary/TxSubmission/Client.hs \
        antithesis/components/dwarf-adversary/src/DwarfAdversary/ChainSync/Connection.hs \
        antithesis/components/dwarf-adversary/app/Main.hs \
        antithesis/components/dwarf-adversary/dwarf-adversary.cabal
git commit -m "spike(sp3b): tx-submission initiator #4 provider + inbound IR server; consumer decodes offered tx"
```

---

## Task 2: `mutateTxField` + Target.hs + FuzzSpec (spike-independent)

This is reusable by both A1 and A2, and is pure/testable — safe to build right after the spike gate.

**Files:**
- Create: `src/DwarfAdversary/TxSubmission/Target.hs`
- Modify: `dwarf-adversary.cabal`, `test/FuzzSpec.hs`

- [ ] **Step 1: Confirm the decoded Conway tx Term layout**

Before coding the navigation, dump a real decoded tx `Term` to confirm indices (the tx is an array `[tx_body, witness_set, is_valid, auxiliary_data]`; `tx_body` is a map with integer keys, certificates at key `4`; auxiliary-data is the tx array's element 3). Use a captured tx (Task 3) or a REPL `decodeTerm` of a known tx. Record the confirmed layout in a comment in `Target.hs`.

- [ ] **Step 2: Write the failing FuzzSpec properties**

```haskell
    describe "mutateTxField targeting" $ do
        it "WholeTx mutates and stays encodable" $ property $ \(s :: Int) ->
            let (t,_) = mutateTxField WholeTx (mkStdGen s) 1.0 sampleTx
            in LBS.length (toLazyByteString (encodeTerm t)) `seq` True
        it "Certificate targets the certs sub-term (or records fallback)" $ do
            let (t, info) = mutateTxField Certificate (mkStdGen 1) 1.0 sampleTx
            t `shouldNotBe` sampleTx
            miKind info `shouldSatisfy` (not . null)
        it "AuxData targets the aux-data element (or records fallback)" $ do
            let (t, _) = mutateTxField AuxData (mkStdGen 1) 1.0 sampleTx
            t `shouldNotBe` sampleTx
```

where `sampleTx :: Term` is a Conway-tx-shaped literal: `TList [ TMap [(TInt 0, TListI [...]), (TInt 4, TListI [TList [TInt 0, TBytes "pool"]])], TMap [...], TBool True, TMap [(TInt 0, TBytes "meta")] ]`.

- [ ] **Step 3: Implement `Target.hs`**

```haskell
{-# LANGUAGE OverloadedStrings #-}

-- | Sub-field-targeted CBOR mutation over the Conway transaction Term layout.
-- A tx Term is TList [tx_body(TMap), witness_set, is_valid, auxiliary_data].
-- Certificates live at tx_body map key (TInt 4); auxiliary-data is the tx
-- array's element 3. Navigation reuses Fuzz.mutateTerm on the located
-- sub-Term and splices it back; on a navigation miss it falls back to
-- mutating the whole tx_body and records that in MutationInfo.
module DwarfAdversary.TxSubmission.Target
    ( TxField (..)
    , mutateTxField
    ) where

import Codec.CBOR.Term (Term (..))
import DwarfAdversary.Fuzz (MutationInfo (..), mutateTerm)
import System.Random (StdGen)

data TxField = WholeTx | Certificate | AuxData
    deriving (Eq, Show)

mutateTxField :: TxField -> StdGen -> Double -> Term -> (Term, MutationInfo)
mutateTxField field g rate tx = case (field, tx) of
    (WholeTx, TList (body : rest)) ->
        let (b', info) = mutateTerm g rate body
        in (TList (b' : rest), info)
    (Certificate, TList (TMap kvs : rest)) ->
        case lookupKey (TInt 4) kvs of
            Just certs ->
                let (c', info) = mutateTerm g rate certs
                    kvs' = setKey (TInt 4) c' kvs
                in (TList (TMap kvs' : rest), info { miKind = "cert:" <> miKind info })
            Nothing -> fallback
    (AuxData, TList xs) | length xs >= 4 ->
        let aux = xs !! 3
            (a', info) = mutateTerm g rate aux
            xs' = take 3 xs ++ [a'] ++ drop 4 xs
        in (TList xs', info { miKind = "aux:" <> miKind info })
    _ -> fallback
  where
    fallback =
        let (t', info) = mutateTerm g rate tx
        in (t', info { miKind = "fallback:" <> miKind info })

lookupKey :: Term -> [(Term, Term)] -> Maybe Term
lookupKey k = foldr (\(k', v) acc -> if k' == k then Just v else acc) Nothing

setKey :: Term -> Term -> [(Term, Term)] -> [(Term, Term)]
setKey k v = map (\(k', v') -> if k' == k then (k', v) else (k', v'))
```

> Handle `TMapI` as well as `TMap` if the decoded body uses the indefinite map encoding — add a `TMapI` arm mirroring the `TMap` one. Confirm against the Step-1 dump.

- [ ] **Step 4: cabal + build + test**

Add `DwarfAdversary.TxSubmission.Target` to `exposed-modules`. Then:
```bash
ssh cardano-box 'cd <repo>/antithesis/components/dwarf-adversary && export PATH=$HOME/.ghcup/bin:$PATH && cabal test 2>&1 | tail -20'
```
Expected: new properties pass.

- [ ] **Step 5: Commit**

```bash
git add .../TxSubmission/Target.hs .../dwarf-adversary.cabal .../test/FuzzSpec.hs
git commit -m "feat(sp3b): mutateTxField — sub-field-targeted Conway-tx mutation + FuzzSpec"
```

---

## Task 3: `TxSource` — lift a real tx from a captured block

**Files:**
- Create: `src/DwarfAdversary/TxSource.hs`; Modify: `dwarf-adversary.cabal`

- [ ] **Step 1: Implement `getBaseTx`**

```haskell
{-# LANGUAGE OverloadedStrings #-}

-- | Capture one real transaction to mutate + offer. Reuses BlockSource to grab
-- a real block hermetically, decodes the block Term, and lifts one tx Term out.
-- The block Term layout is confirmed against a real decoded block (a Conway
-- block is TList [header, TList tx_bodies, witness_sets, aux_data, invalid]);
-- the first tx_body + its witness/aux are recombined into a standalone tx Term
-- matching the encTx wire shape.
module DwarfAdversary.TxSource ( getBaseTx ) where

import Codec.CBOR.Term (Term)
-- ... (BlockSource.getBaseBlock, encBlock, decode-to-Term, navigate to first tx)
```

> The exact block→standalone-tx reconstruction is confirmed empirically (dump a decoded block Term on cardano-box, find the tx components). If a faithful standalone `GenTx` cannot be reconstructed from a block tx within reasonable effort, capture the tx by other means (e.g. a small N2C `LocalTxMonitor` query against p1) — record the chosen method. Deliverable: `getBaseTx :: (String->IO()) -> NetworkMagic -> (String,Int) -> IO (GenTxId Block, SizeInBytes, GenTx Block)` (txid + size + tx, ready for the provider client).

- [ ] **Step 2: build + commit**

```bash
ssh cardano-box 'cd <repo>/antithesis/components/dwarf-adversary && export PATH=$HOME/.ghcup/bin:$PATH && cabal build lib:dwarf-adversary'
git add .../TxSource.hs .../dwarf-adversary.cabal
git commit -m "feat(sp3b): TxSource — capture a real tx from an in-bundle block"
```

---

## Task 4: serve the mutated tx (wire mutation into the provider) + tx-submission server mode

**Files:** Modify `src/DwarfAdversary/TxSubmission/Client.hs`, `src/DwarfAdversary/ChainSync/Connection.hs`

- [ ] **Step 1:** apply `mutateTxField` to the captured tx before offering. Decode the captured `GenTx` to a `Term` via `decTx`-roundtrip is not possible directly (it's a typed value); instead mutate at the **wire encode** boundary like the header/block codecs: provide a `mutatingEncTx field seed rate :: GenTx Block -> Encoding` (mirroring `mutEncBlock`) and build a mutating tx-submission codec `codecTxSubmission2 encTxId decTxId (mutatingEncTx ...) decTx`. The provider client serves the real `tx`; the **codec** mutates its bytes on the wire. This matches the SP3a pattern exactly and avoids reconstructing typed txs.

> This means `TxSource` only needs to return the real `GenTx` (+ txid/size); the mutation is in the codec, consistent with chain-sync/block-fetch. Revisit Task 2/3: `mutateTxField` operates on the `Term` decoded from the tx's CBOR inside `mutatingEncTx` (decode tx bytes → Term → `mutateTxField` → re-encode), exactly like `mutEncBlock` but with field targeting. Adjust `Target.hs` usage accordingly (it stays a pure Term function; the codec wrapper calls it).

- [ ] **Step 2:** add `runAdversaryServerIR` use for tx-submission mode wiring the mutating codec + provider client on initiator #4 and the existing responders. Build lib + exe on cardano-box. Commit.

---

## Task 5: CLI `--protocol txsubmission` + dispatch + SDK + selftest

**Files:** Modify `app/Main.hs`

- [ ] **Step 1:** the `--protocol`/`--cbor-shape` args already exist (SP3a). Map `--cbor-shape` → `TxField` (`tx-body`→`WholeTx`, `certificate`→`Certificate`, `auxiliary-data`→`AuxData`). Add `runServeTxSubmission` (capture tx via `TxSource`, build mutating tx codec for the chosen `TxField`, run `runAdversaryServerIR`) and finalize `runBlockFetchSelftest`-style `runTxSubmissionSelftest`. Emit `SDK.reachable "dwarf_tx_decoder_reachable"` + `SDK.sometimes True "dwarf_served_mutated_tx"` (no Always). Dispatch in `main` on `argProtocol == "txsubmission"`.
- [ ] **Step 2:** build exe; commit.

---

## Task 6: tx-submission selftest on cardano-box (0-crash)

- [ ] Create `tools/sp3b_selftest.sh` (mirror `sp3b`/`sp3a_selftest.sh`): build, run `--selftest --protocol txsubmission --cbor-shape tx-body --listen-port 3999`, assert a clean consumer result + no `panic|<<loop>>|MuxError|segfault`. Run on cardano-box. Commit.

---

## Task 7: generator changes + regression

**Files:** Modify `dwarf/profile_manager/antithesis_generator.py`, `tools/test_sp2_generator.py`

- [ ] **Step 1 (failing tests):** assert the 3 tx scenarios now build via `derive_adversary` (protocol `txsubmission`, correct shape, `--protocol`/`--cbor-shape` in args) and `gen.ADVERSARY_IMAGE` is `:0.3.0`; header/block still build.

```python
TXBODY = ROOT / "dwarf/scenarios/cardano-node-cbor-tx-body-fuzz-structured.yaml"
def test_txbody_scenario_now_builds():
    adv = gen.derive_adversary(_load(TXBODY))
    assert adv["protocol"] == "txsubmission" and adv["shape"] == "tx-body"
    assert "txsubmission" in adv["command_args"]
```

- [ ] **Step 2 (implement):** in `ADVERSARY_MODES` set `built: True` for `cardano-node-cbor-decode-{tx-body,certificate,auxiliary-data}`; bump `ADVERSARY_IMAGE` → `:0.3.0`. Run full `tools/test_sp2_generator.py` (expect all green, header/block regression-clean). Commit.

---

## Task 8: round-trip the 3 tx bundles on cardano-box

- [ ] Create `tools/sp3b_roundtrip.sh`: generate the 3 tx scenarios + (regression) the block + header scenarios with `--backend antithesis`, each `verify_generated_bundle` green + `INTERNAL_NETWORK=true docker compose config` OK. Stage the updated generator to the box, run, expect all `OK`. Commit.

---

## Task 9: live Antithesis run (done bar)

- [ ] **Step 1:** build + push public `ghcr.io/j-gainsec/dwarf-adversary:0.3.0` (login via a `write:packages` token piped over ssh stdin — never in argv/history; logout after; confirm anonymous pull = public).
- [ ] **Step 2:** PAT-clone `Cyber-Castellum/DWARF`, switch the `cardano_node_dwarf` adversary to `0.3.0` + `--protocol txsubmission --cbor-shape tx-body` (or run three tests, one per shape — decide at submit time), commit + push via the repo PAT (`-c credential.helper`, token never in URL/config), capture the SHA.
- [ ] **Step 3:** `moog create-test-plan` → confirm `ready`; `moog create-test --approve` (J-GainSec, 1h, no-faults) for the SHA. Record txHash + testRunId.
- [ ] **Step 4:** confirm on-platform the node's tx decoder was exercised (tx-submission offer + decode in tracer logs; `dwarf_tx_decoder_reachable`/`dwarf_served_mutated_tx` fire), no false-green. Record in AGENTS.md + the SP3b workbench note (`obj_1dd0b2f7b0f34e86b9c54d7d`). SP3b done; SP3 Track A complete.

---

## Self-Review

- **Spec coverage:** initiator-client provider (T1, T4) ✓; gating spike + A2 STOP criterion (T1) ✓; sub-field-targeted `mutateTxField` (T2) ✓; tx capture via BlockSource (T3) ✓; `--protocol txsubmission`/`--cbor-shape`→`TxField` + SDK (T5) ✓; selftest (T6) ✓; generator 3-shapes-built + image 0.3.0 + regression (T7) ✓; round-trip (T8) ✓; live run (T9) ✓; amaru/Track B out of scope ✓.
- **Key refinement vs spec (recorded):** mutation is applied at the **codec wire-encode boundary** (`mutatingEncTx` wrapping `mutateTxField`), mirroring `mutEncBlock`, rather than mutating a typed `GenTx` — this avoids reconstructing typed txs and keeps `Target.hs` a pure `Term` function. `TxSource` returns the real `GenTx`/txid/size; the codec mutates.
- **Spike honesty:** T1 is exploratory by necessity (initiator-on-inbound N2N is the unknown); it has a concrete deliverable + explicit STOP→A2 criterion, matching the Phase 3b spike pattern. Tasks 4–9 are contingent on T1 success.
- **Type/name consistency:** `txProviderClient`, `TxField`/`mutateTxField`, `getBaseTx`, `mutatingEncTx`, `runAdversaryServerIR`, `ADVERSARY_IMAGE=:0.3.0`, `--protocol txsubmission` used consistently.
- **Open verifications (at execution):** exact `TxSubmission2.Client` blocking-style signatures; whether `withServerNode` runs a duplex (IR) app or the connection-manager path is needed (T1); the decoded Conway tx/block Term indices (T2 Step 1, T3 Step 1).
