# SP3b-fix — Tx-Adversary Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the tx-submission adversary crash-looping (`container: dwarf-adversary, exit code: 1` ×12,300) — keep the process alive across protocol completion + peer disconnects, while preserving fuzz coverage (serve a batch of mutated txs from one long-lived process).

**Architecture:** Three tx-path changes: capture a batch of txs (`getBaseTxs`); make `txProviderClient` offer the batch and **park instead of `SendMsgDone`** (the confirmed crash trigger, `Client.hs:57`); wrap the server run in a **catch-and-retry** loop so peer disconnects restart it in-process. Block (SP3a-fix `0.4.0`) and header paths untouched.

**Tech Stack:** Haskell (ghc-9.6.7 + CHaP) on cardano-box; Docker (testnet incl. tx-generator) for the verification gate; Moog for the live re-submit.

**Grounding facts (verified in code):**
- `TxSubmission/Client.hs:42` `txProviderClient log_ txid size tx` — single tx; line 57 `(False, SingBlocking) -> pure (SendMsgDone ())` ends the initiator #4 mini-protocol (the crash trigger). Constructors imported: `SendMsgReplyTxIds`, `SendMsgReplyTxs`, `SendMsgDone`; `BlockingReply (NonEmpty)`, `NonBlockingReply [..]`; `SingBlocking`/`SingNonBlocking`.
- `TxSource.getBaseTx :: (String->IO()) -> NetworkMagic -> (String,Int) -> IO (GenTxId Block, SizeInBytes, GenTx Block)` (uses `extractTxs`/`txId`, computed `SizeInBytes`). `BlockSource.getBaseChain` returns `([Header], Map (Point Block) Block)`.
- `Main.hs runServeTxSubmission` (~line 415): `(txid,size,tx) <- getBaseTx …`; `provider = txProviderClient logMsg txid size tx`; `runAdversaryServerIR magic port onAccept codecChainSync csServer plainBlockFetchCodec blockFetchResponder txCodec provider` (IO Void, blocks forever).
- Image `dwarf-adversary:0.4.0` (block); this fix → `0.5.0`. Build/run on cardano-box (`export PATH=$HOME/.ghcup/bin:$PATH`).
- The testnet `tx-generator` service exists in `antithesis/cardano_node_dwarf/docker-compose.yaml` (needs `utxo-keys` + `relay1` + configurator) — required so the chain carries real txs for the verification gate.

---

## Task 1: `getBaseTxs` — capture a batch of txs

**Files:** `src/DwarfAdversary/TxSource.hs`

- [ ] **Step 1: implement `getBaseTxs`** (reuse `getBaseChain`'s blocks; collect up to N txs)

```haskell
import DwarfAdversary.BlockSource (getBaseBlock, getBaseChain)
import Ouroboros.Consensus.Ledger.SupportsMempool (extractTxs, txId)
import Codec.CBOR.Write (toLazyByteString)
import qualified Data.ByteString.Lazy as LBS
import qualified Data.Map.Strict as Map

-- | Capture up to @want@ real transactions from the in-bundle chain's blocks.
-- Reuses getBaseChain (headers + point->block map), flattens extractTxs over the
-- captured blocks, and returns the first @want@ as (txid, size, tx). Errors only
-- if the whole capture yields zero txs after the chain capture (chain genuinely
-- has no txs — should not happen with the tx-generator running).
getBaseTxs
    :: (String -> IO ())
    -> NetworkMagic
    -> (String, Int)
    -> Int                       -- ^ how many chain blocks to scan
    -> Int                       -- ^ max txs to return
    -> IO [(GenTxId Block, SizeInBytes, GenTx Block)]
getBaseTxs log_ magic hp chainLen want = do
    (_headers, blocks) <- getBaseChain log_ magic hp chainLen
    let txs = take want
            [ (txId t, SizeInBytes (fromIntegral (LBS.length (toLazyByteString (encTx t)))), t)
            | b <- Map.elems blocks, t <- extractTxs b ]
    log_ ("getBaseTxs: " <> show (length txs) <> " txs from " <> show (Map.size blocks) <> " blocks")
    pure txs
```

> Add `encTx` to the `DwarfAdversary.ChainSync.Codec` import in TxSource. Export `getBaseTxs`. (`getBaseTx` may stay or be reimplemented as `head <$> getBaseTxs … 1`; keep it for the existing selftest path.) `Map` import already needed — confirm `containers` (added in SP3a-fix). If `Map.elems blocks` is unordered, that's fine — any N real txs work.

- [ ] **Step 2: build lib on cardano-box; commit**

```bash
ssh cardano-box '… cabal build lib:dwarf-adversary'
git commit -am "feat(sp3b-fix): getBaseTxs — capture a batch of real txs"
```

---

## Task 2: `txProviderClient` — offer the batch, park (no `SendMsgDone`)

**Files:** `src/DwarfAdversary/TxSubmission/Client.hs`

- [ ] **Step 1: rewrite `txProviderClient` to take a list and never complete**

```haskell
import Control.Concurrent (threadDelay)
import Control.Monad (forever)
-- (keep the existing Client constructor imports)

txProviderClient
    :: (String -> IO ())
    -> [(GenTxId Block, SizeInBytes, GenTx Block)]
    -> TxSubmissionClient (GenTxId Block) (GenTx Block) IO ()
txProviderClient log_ txs = TxSubmissionClient (pure (idle txs))
  where
    byId = [(tid, tx) | (tid, _, tx) <- txs]

    idle :: [(GenTxId Block, SizeInBytes, GenTx Block)]
         -> ClientStIdle (GenTxId Block) (GenTx Block) IO ()
    idle remaining =
        ClientStIdle
            { recvMsgRequestTxIds = \blocking _ack req -> do
                let n = max 1 (fromIntegral req)
                    (offer, rest) = splitAt n remaining
                    ids = [(tid, sz) | (tid, sz, _) <- offer]
                case (offer, blocking) of
                    (_ : _, SingBlocking) -> do
                        log_ ("tx-submission: offering " <> show (length ids) <> " txid(s)")
                        pure (SendMsgReplyTxIds (BlockingReply (NE.fromList ids)) (idle rest))
                    (_ : _, SingNonBlocking) ->
                        pure (SendMsgReplyTxIds (NonBlockingReply ids) (idle rest))
                    ([], SingBlocking) -> do
                        -- batch exhausted: PARK (never SendMsgDone — completing the
                        -- initiator is what crashed the process). Keeps the
                        -- connection + process alive.
                        log_ "tx-submission: batch exhausted; parking (alive)"
                        forever (threadDelay 1_000_000)
                    ([], SingNonBlocking) ->
                        pure (SendMsgReplyTxIds (NonBlockingReply []) (idle []))
            , recvMsgRequestTxs = \requested -> do
                let served = [tx | tid <- requested, Just tx <- [lookup tid byId]]
                log_ ("tx-submission: serving " <> show (length served) <> " tx(s)")
                pure (SendMsgReplyTxs served (idle remaining))
            }
```

> `forever (threadDelay …)` has type `m a`, unifying with the required `m (ClientStTxIds SingBlocking …)`. `NE.fromList` from `Data.List.NonEmpty` (already imported as `(:|)`; add `fromList` or `import qualified Data.List.NonEmpty as NE`). Confirm `recvMsgRequestTxs`'s continuation must be `idle` (not advance) — `remaining` already advanced on the txid offer; serving looks up by id from the full `byId`. If the protocol's ack/window rejects re-using `idle remaining` after serving, adjust to thread the post-serve state; the selftest + repro will surface it.

- [ ] **Step 2: build lib; commit**

```bash
git commit -am "fix(sp3b-fix): tx provider offers a batch and parks instead of SendMsgDone"
```

---

## Task 3: rewire `runServeTxSubmission` + resilient server + image bump

**Files:** `app/Main.hs`, `dwarf/profile_manager/antithesis_generator.py`

- [ ] **Step 1: capture the batch + build the list provider**

In `runServeTxSubmission`, replace:
```haskell
    (txid, size, tx) <- getBaseTx logMsg magic hp
    … provider = txProviderClient logMsg txid size tx
```
with:
```haskell
    txs <- getBaseTxs logMsg magic hp 50 10   -- scan up to 50 blocks, offer up to 10 txs
    SDK.sometimes (not (null txs)) "dwarf_base_tx_obtained" (object ["count" .= length txs])
    let provider = txProviderClient logMsg txs
```
(import `getBaseTxs` from `DwarfAdversary.TxSource`; the per-tx `describeTxMutation` SDK assertion can fire on the first tx, or move into the responder — keep `dwarf_served_mutated_tx` as today.)

- [ ] **Step 2: wrap the server run in catch-and-retry**

Replace the bare `_ <- runAdversaryServerIR …; pure ()` with:
```haskell
import Control.Exception (SomeException, try, catch)
import Control.Monad (forever)

    let runServer =
            runAdversaryServerIR magic port onAccept codecChainSync csServer
                plainBlockFetchCodec blockFetchResponder txCodec provider
    forever
        ( (runServer >> pure ())
            `catch` \(e :: SomeException) -> do
                logMsg ("tx server exception (restarting in-process): " <> show e)
                threadDelay 1_000_000
        )
```
> `runAdversaryServerIR :: IO Void`; `runServer >> pure ()` is `IO ()`; on exception the handler logs + delays, and `forever` re-runs — the process never exits. Confirm `ScopedTypeVariables` is enabled in Main (it is, used for `parseSeed`). Keep the chain-sync/block-fetch params as the plain no-blocks ones (tx mode doesn't fuzz those).

- [ ] **Step 3: image + generator bump to 0.5.0**

`build-image.sh ghcr.io/j-gainsec/dwarf-adversary:0.5.0`; `antithesis_generator.py` `ADVERSARY_IMAGE` → `:0.5.0`; update the `endswith(":0.4.0")` test → `:0.5.0`. Build exe; run unit tests + `tools/sp3b_roundtrip.sh`. Commit.

---

## Task 4: local repro WITH the tx-generator (the gate)

**Files:** `tools/sp3b_fix_repro.sh`

- [ ] **Step 1:** write a repro that brings up the testnet **including tx-generator** so the chain carries real txs, sets the adversary to `0.5.0` tx mode, and checks the adversary stays alive:

```bash
#!/usr/bin/env bash
set -uo pipefail
cd /home/nigel/dwarf-v4/antithesis/cardano_node_dwarf
export INTERNAL_NETWORK=false
sed -i 's#dwarf-adversary:0\.[0-9]*\.[0-9]*#dwarf-adversary:0.5.0#; s/"blockfetch"/"txsubmission"/; s/"block"$/"tx-body"/' docker-compose.yaml
docker compose up -d configurator tracer tracer-sidecar p1 p2 p3 relay1 relay2 tx-generator >/dev/null 2>&1
docker compose up -d --force-recreate dwarf-adversary >/dev/null 2>&1
echo "waiting for tx-generator txs + adversary serve…"
sleep 120
RC=$(docker inspect dwarf-adversary --format '{{.RestartCount}}')
echo "RestartCount=$RC"
docker logs dwarf-adversary 2>&1 | grep -iE "getBaseTxs|serving|offering|parking|exception" | tail -10
sleep 60
RC2=$(docker inspect dwarf-adversary --format '{{.RestartCount}}')
echo "RestartCount after +60s=$RC2"
[ "$RC2" -eq 0 ] && echo "OK: adversary stable (no crash-loop)" || { echo "FAIL: adversary restarting ($RC2)"; exit 1; }
```

- [ ] **Step 2:** run on cardano-box. **Gate:** `RestartCount` stays 0 while the adversary serves txs (log shows `serving N tx(s)` then `parking`). The tx-generator must actually produce txs (confirm `getBaseTxs: M txs` with M>0); if blocks stay empty, give it more time / confirm tx-generator health before judging. If the adversary still restarts, inspect the caught-exception log and iterate.

- [ ] **Step 3:** selftest (`tools/sp3b_selftest.sh`) green; commit the repro.

---

## Task 5: re-submit live + confirm

- [ ] Push public `0.5.0`; PAT-clone `Cyber-Castellum/DWARF`, set the testnet adversary to `0.5.0` + `--protocol txsubmission --cbor-shape tx-body`, push, get SHA; `moog create-test-plan` → `create-test --approve` (J-GainSec, 1h, no-faults). Record txHash/testRunId. When it lands, read via `agent-browser`: confirm `dwarf_served_mutated_tx` fires AND `container: dwarf-adversary, exit code: 1` is **gone** (RestartCount/exits cleared). Record in AGENTS.md + workbench.

---

## Self-Review
- **Spec coverage:** batch capture (T1) ✓; provider offers batch + parks, no `SendMsgDone` (T2) ✓; resilient catch-retry server (T3) ✓; image/generator `0.5.0` (T3) ✓; **local repro with tx-generator gate** (T4) ✓; live re-confirm (T5) ✓; block/header untouched ✓.
- **Open verifications (at execution):** tx-submission ack/window semantics when re-using `idle remaining` after a serve (selftest/repro will surface); `tx-generator` brings real txs to the local chain; `NE.fromList`/`forever` imports.
- **Risk note:** if the crash persists after removing `SendMsgDone`, the caught-exception log (T4 Step 2) names the true cause — fix that specifically; the resilient wrapper keeps the process up meanwhile so the run is still usable.
