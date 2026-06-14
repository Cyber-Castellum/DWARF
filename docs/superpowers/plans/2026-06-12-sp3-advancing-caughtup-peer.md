# SP3 Advancing CaughtUp Peer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dwarf-adversary present as a *CaughtUp, advancing* N2N peer so the cardano-node sustains hot block-fetch (SP3a) and starts tx-submission (SP3b), making `dwarf_served_mutated_block` / `dwarf_served_mutated_tx` fire live.

**Architecture:** A background **producer** runs the existing chain-sync client (`adversaryApplication`'s machinery) against the in-bundle upstream with a *never-terminate* control message, continuously filling a shared `StrictTVar IO (Chain Header)`. A new **advancing chain-sync server** reads that shared chain and rolls forward via `Chain.successorBlock` as it grows (blocking on STM when caught up), reporting `Chain.headTip` as the tip — so the downstream node syncs to the live, recent tip and the GSM enters/holds `CaughtUp`. Headers are mutated on the codec encode path (unchanged). Block bodies (SP3a) are fetched on-demand per requested point. Both `runServeBlockFetch` and `runServeTxSubmission` are rewired onto this foundation.

**Tech Stack:** Haskell (ghc-9.6.7, CHaP), ouroboros-network chain-sync client/server + `Ouroboros.Network.Mock.Chain`, STM (`Control.Concurrent.Class.MonadSTM.Strict`), docker-compose local testnet, cardano-cli (tx injection), Antithesis SDK assertions.

**Root cause reference:** workbench `obj_3531b3e04a624ee197a0b678`; task #93. The node enters GSM `CaughtUp` only when its selected tip is not `TooOld` vs wall-clock (`Ouroboros.Consensus.Node.GSM.durationUntilTooOld`); the current adversary serves a fixed origin-anchored chain then parks, so the tip is ancient and the node never reaches CaughtUp.

---

## File Structure

- **Modify** `src/DwarfAdversary/Application.hs` — export a reusable producer `runChainProducerInto :: StrictTVar IO (Chain Header) -> NetworkMagic -> String -> PortNumber -> IO ()` that syncs upstream forever into a *caller-supplied* `chainVar` (never-terminate control message). Refactor `adversaryApplication` to share the inner sync (no behavior change for existing callers).
- **Modify** `src/DwarfAdversary/ChainSync/Server.hs` — add `advancingChainSyncServer :: (String -> IO ()) -> (Header -> IO ()) -> StrictTVar IO (Chain Header) -> ChainSyncServer Header Point Tip IO ()` (roll-forward from a read pointer via `Chain.successorBlock`, STM-await when caught up, `Chain.headTip` as tip, `Chain.findFirstPoint` for intersection). Keep the existing static `chainSyncServer` for selftests/header-fuzz.
- **Modify** `src/DwarfAdversary/ChainSync/Connection.hs` — add `onDemandBlockFetchResponder :: (Block -> IO ()) -> NetworkMagic -> (String,Int) -> BlockFetchServer Block (Point Block) IO ()` that fetches each requested point's body from upstream via the existing `fetchBlock`, applies `onServe`, and serves it (mutation stays on the block-fetch codec encode path).
- **Modify** `app/Main.hs` — rewire `runServeBlockFetch` and `runServeTxSubmission` onto the producer + `advancingChainSyncServer`; tx capture reads from the shared growing chain.
- **Create** `tools/sp3_caughtup_repro.sh` — local verification: fresh testnet, inject a tx via cardano-cli, assert relay2 reaches CaughtUp + sends `RequestTxIds`/`RequestTxs` (tx) and block-fetch fires (block).
- **Modify** `dwarf/profile_manager/antithesis_generator.py`, `tools/test_sp2_generator.py`, `tools/sp3b_roundtrip.sh` — image bump to `0.6.0`.

---

### Task 1: Reusable never-terminate chain producer

**Files:**
- Modify: `src/DwarfAdversary/Application.hs`

- [ ] **Step 1: Add a never-terminate control source + producer that fills a supplied chainVar**

In `Application.hs`, add an always-`Continue` control source and a producer that reuses the existing `controlledProtocol` + `runChainSyncApplication` against a caller-owned `chainVar`:

```haskell
-- | Control source that NEVER terminates: the producer keeps syncing the
-- upstream chain forever, so the served chain's tip stays recent (the node
-- can reach + hold GSM CaughtUp). Contrast 'terminateAfterCount'.
neverTerminate :: ControlMessageSTM IO
neverTerminate = pure Continue

-- | Run the chain-sync CLIENT against @(host,port)@ forever, filling the
-- caller-supplied @chainVar@ with the upstream chain (roll-forward and
-- roll-back tracked). Returns only on connection error (caller restarts it).
runChainProducerInto
    :: StrictTVar IO (Chain Header)
    -> NetworkMagic
    -> String
    -> PortNumber
    -> IO (Either SomeException ())
runChainProducerInto chainVar magic host port = do
    let stateVar = State chainVar
        protocol = controlledProtocol neverTerminate
    try $ do
        _ <- runChainSyncApplication magic host port
                (adversaryApplication' stateVar originPoint protocol)
        pure ()
```

Note: match the exact argument order/types of the existing `runChainSyncApplication` and the internal application builder used by `adversaryApplication`. Factor the inner application builder (currently inline in `adversaryApplication`) into `adversaryApplication' :: State -> Point -> Protocol -> ChainSyncApplication` and have BOTH `adversaryApplication` and `runChainProducerInto` call it (DRY; no behavior change for `adversaryApplication`).

- [ ] **Step 2: Export the new symbols**

Add `runChainProducerInto` and `neverTerminate` to the module export list.

- [ ] **Step 3: Compile**

Run on cardano-box: `cd /home/nigel/dwarf-v4/antithesis/components/dwarf-adversary && export PATH=$HOME/.ghcup/bin:$PATH && cabal build lib:dwarf-adversary 2>&1 | tail -15`
Expected: compiles; `adversaryApplication`/`syncHeaders` behavior unchanged.

- [ ] **Step 4: Commit**

```bash
git add src/DwarfAdversary/Application.hs
git commit -m "SP3-foundation: reusable never-terminate chain producer (runChainProducerInto)"
```

---

### Task 2: Advancing roll-forward chain-sync server

**Files:**
- Modify: `src/DwarfAdversary/ChainSync/Server.hs`

- [ ] **Step 1: Add `advancingChainSyncServer`**

Reads the shared growing `Chain Header`. Holds a read pointer (last point served to THIS client, starting at the negotiated intersection). On `MsgRequestNext`: compute `Chain.successorBlock readPtr chain`; if `Just h` → `SendMsgRollForward h (headTip chain)` and advance readPtr to `headerPoint h`; if `Nothing` (client caught up to our tip) → `SendMsgAwaitReply` then block in STM until the chain grows past readPtr, then roll forward. On `MsgFindIntersect`: `Chain.findFirstPoint points chain` → `SendMsgIntersectFound p (headTip chain)` and set readPtr=p; else `SendMsgIntersectNotFound (headTip chain)`. Mutation stays on the codec; `onServe` fires per header rolled forward (drives `dwarf_served_mutated_header` where wired).

```haskell
advancingChainSyncServer
    :: (String -> IO ())
    -> (Header -> IO ())
    -> StrictTVar IO (Chain Header)
    -> ChainSyncServer Header Point Tip IO ()
advancingChainSyncServer log_ onServe chainVar =
    ChainSyncServer (pure (idle genesisPoint))
  where
    idle :: Point -> ServerStIdle Header Point Tip IO ()
    idle readPtr =
        ServerStIdle
            { recvMsgRequestNext = do
                chain0 <- atomically (readTVar chainVar)
                case Chain.successorBlock readPtr chain0 of
                    Just h -> do
                        onServe h
                        pure (Left (SendMsgRollForward h (Chain.headTip chain0)
                                     (ChainSyncServer (pure (idle (headerPoint h))))))
                    Nothing ->
                        -- caught up to our tip: await until the producer extends
                        -- the chain past readPtr, then roll forward.
                        pure (Right $ do
                            h <- atomically $ do
                                c <- readTVar chainVar
                                case Chain.successorBlock readPtr c of
                                    Just h' -> pure h'
                                    Nothing -> retry
                            onServe h
                            tipNow <- Chain.headTip <$> atomically (readTVar chainVar)
                            pure (SendMsgRollForward h tipNow
                                   (ChainSyncServer (pure (idle (headerPoint h))))))
            , recvMsgFindIntersect = \points -> do
                log_ "chain-sync(advancing): MsgFindIntersect"
                chain0 <- atomically (readTVar chainVar)
                case Chain.findFirstPoint points chain0 of
                    Just p  -> pure (SendMsgIntersectFound p (Chain.headTip chain0)
                                      (ChainSyncServer (pure (idle p))))
                    Nothing -> pure (SendMsgIntersectNotFound (Chain.headTip chain0)
                                      (ChainSyncServer (pure (idle readPtr))))
            , recvMsgDoneClient = log_ "chain-sync(advancing): client done"
            }
```

Add imports as needed: `Ouroboros.Network.Mock.Chain qualified as Chain`, `Chain (Chain)`, `Control.Concurrent.Class.MonadSTM.Strict (readTVar, retry, atomically, StrictTVar)`, `Ouroboros.Consensus.Block (headerPoint)`, `Ouroboros.Network.Block (genesisPoint)`, `Control.Monad (forever)`. Match the exact `ServerStIdle`/`SendMsg*` constructors already used by `chainSyncServer` in this file.

- [ ] **Step 2: Export `advancingChainSyncServer`**

Add to the module export list (keep `chainSyncServer` exported too).

- [ ] **Step 3: Compile**

Run: `cabal build lib:dwarf-adversary 2>&1 | tail -15`
Expected: compiles. Fix any `successorBlock`/`findFirstPoint` arg-order or `SendMsgAwaitReply` shape mismatches against the installed ouroboros-network-protocols version (the chain-sync `ServerStNext` uses `Either … (m …)` for await — mirror the existing `chainSyncServer` await branch exactly).

- [ ] **Step 4: Commit**

```bash
git add src/DwarfAdversary/ChainSync/Server.hs
git commit -m "SP3-foundation: advancing roll-forward chain-sync server (reads shared Chain, headTip tip)"
```

---

### Task 3: On-demand mutated block-fetch responder (SP3a)

**Files:**
- Modify: `src/DwarfAdversary/ChainSync/Connection.hs`

- [ ] **Step 1: Add `onDemandBlockFetchResponder`**

For each requested range, walk the points and fetch each body from upstream via the existing `fetchBlock magic host port pt`; on a hit, run `onServe blk` (drives `dwarf_served_mutated_block`) and serve it (mutation on the block-fetch codec). This replaces `servingBlockFetchResponderMap`'s fixed map with on-demand fetch, so it works with the advancing (unbounded) chain.

```haskell
onDemandBlockFetchResponder
    :: (String -> IO ())
    -> (Block -> IO ())
    -> NetworkMagic
    -> (String, Int)
    -> BlockFetchServer Block (Point Block) IO ()
onDemandBlockFetchResponder log_ onServe magic (host, port) = server
  where
    server = BlockFetchServer receiveReq ()
    receiveReq (ChainRange lo hi) = do
        log_ "block-fetch(on-demand): MsgRequestRange"
        -- serve each point in [lo..hi] by fetching its body from upstream
        pure (SendMsgStartBatch (stream [lo, hi]))   -- expand range -> points as the existing map path did
    stream [] = pure (SendMsgBatchDone (pure server))
    stream (pt:rest) = do
        r <- fetchBlock magic host (fromIntegral port) pt
        case r of
            Right (Just b) -> do
                onServe b
                pure (SendMsgBlock b (stream rest))
            _ -> stream rest
```

Note: reuse the EXACT range→points expansion and `SendMsgStartBatch`/`SendMsgBlock`/`SendMsgBatchDone` shapes from the existing `servingBlockFetchResponderMap` in this file (point ordering via the served chain). If on-demand per-range fetch is too chatty, fetch lazily but keep the responder's batch semantics identical to the existing one.

- [ ] **Step 2: Export it; compile**

Add to exports. Run `cabal build lib:dwarf-adversary 2>&1 | tail -15`. Expected: compiles.

- [ ] **Step 3: Commit**

```bash
git add src/DwarfAdversary/ChainSync/Connection.hs
git commit -m "SP3-foundation: on-demand mutated block-fetch responder for the advancing chain"
```

---

### Task 4: Rewire `runServeBlockFetch` (SP3a) onto the foundation

**Files:**
- Modify: `app/Main.hs` (function `runServeBlockFetch`)

- [ ] **Step 1: Replace the fixed-chain setup with producer + advancing server**

```haskell
runServeBlockFetch logMsg args magic port = do
    SDK.reachable "dwarf_block_fuzz_server_started" (object [ "port" .= argPort args, "seed" .= argSeed args, "shape" .= argShape args ])
    hp@(host, p) <- case argUpstream args of
        Just hp -> pure hp
        Nothing -> error "block-fetch mode requires --upstream (in-bundle node)"
    chainVar <- newTVarIO Chain.Genesis
    -- producer: keep the served chain's tip recent so the node reaches CaughtUp
    _ <- forkIO $ forever $ do
        _ <- runChainProducerInto chainVar magic host (fromIntegral p)
        threadDelay 1_000_000
    SDK.reachable "dwarf_block_decoder_reachable" (object ["seed" .= argSeed args, "shape" .= argShape args])
    let bfCodec = mutatingCodecBlockFetch (argSeed args) (argRate args)
        onServeBlk b = do
            let inf = describeBlockMutation (argSeed args) (argRate args) b
            SDK.sometimes True "dwarf_served_mutated_block"
                (object ["kind" .= miKind inf, "depth" .= miDepth inf, "seed" .= argSeed args])
        csServer = advancingChainSyncServer logMsg (\_ -> pure ()) chainVar
        bfServer = onDemandBlockFetchResponder logMsg onServeBlk magic hp
        onAccept peerAddr = do
            logMsg ("inbound connection accepted from " <> peerAddr)
            SDK.reachable "dwarf_node_connected" (object ["peer" .= peerAddr])
    _ <- runChainSyncServer magic port onAccept codecChainSync csServer bfCodec bfServer
    pure ()
```

Add imports to Main.hs: `Control.Concurrent.Class.MonadSTM.Strict (newTVarIO)`, `Ouroboros.Network.Mock.Chain qualified as Chain`, `DwarfAdversary.Application (runChainProducerInto)`, `DwarfAdversary.ChainSync.Server (advancingChainSyncServer)`, `DwarfAdversary.ChainSync.Connection (onDemandBlockFetchResponder)`. Keep `dwarf_base_header_obtained` by emitting it once the producer's chainVar becomes non-empty (poll `Chain.length` in the fork before serving).

- [ ] **Step 2: Compile exe**

Run: `cabal build exe:dwarf-adversary 2>&1 | tail -20`. Expected: compiles.

- [ ] **Step 3: Commit**

```bash
git add app/Main.hs
git commit -m "SP3a-fix2: runServeBlockFetch on advancing CaughtUp peer + on-demand bodies"
```

---

### Task 5: Rewire `runServeTxSubmission` (SP3b) onto the foundation

**Files:**
- Modify: `app/Main.hs` (function `runServeTxSubmission`)

- [ ] **Step 1: Use producer + advancing chain-sync server; capture txs from the growing chain**

Keep the resilient `forever`/`catch` server + the parking `txProviderClient` (0.5.1). Replace the 5-header static chain-sync with the producer + `advancingChainSyncServer`, and capture the tx batch from the shared chain (extract txs from the producer's blocks, re-capturing until non-empty as 0.5.1 does):

```haskell
runServeTxSubmission logMsg args magic port = do
    SDK.reachable "dwarf_tx_fuzz_server_started" (object [ "port" .= argPort args, "seed" .= argSeed args, "shape" .= argShape args ])
    hp@(host, p) <- case argUpstream args of
        Just hp -> pure hp
        Nothing -> error "tx-submission mode requires --upstream (in-bundle node)"
    chainVar <- newTVarIO Chain.Genesis
    _ <- forkIO $ forever $ do
        _ <- runChainProducerInto chainVar magic host (fromIntegral p)
        threadDelay 1_000_000
    let field = txFieldOfShape (argShape args)
        txCodec = mutatingCodecTxSubmission field (argSeed args) (argRate args)
        csServer = advancingChainSyncServer logMsg (\_ -> pure ()) chainVar
        onServeTx t = do
            let inf = describeTxMutation field (argSeed args) (argRate args) t
            SDK.sometimes True "dwarf_served_mutated_tx"
                (object ["kind" .= miKind inf, "depth" .= miDepth inf, "shape" .= argShape args, "seed" .= argSeed args])
        onAccept peerAddr = do
            logMsg ("inbound connection accepted from " <> peerAddr)
            SDK.reachable "dwarf_node_connected" (object ["peer" .= peerAddr])
    SDK.reachable "dwarf_tx_decoder_reachable" (object ["seed" .= argSeed args, "shape" .= argShape args])
    forever $ do
        txs <- getBaseTxsFromChain logMsg chainVar magic hp 10
        if null txs
            then logMsg "tx-submission: no txs captured yet; waiting" >> threadDelay 10_000_000
            else do
                SDK.sometimes True "dwarf_base_tx_obtained" (object ["count" .= length txs])
                let provider = txProviderClient logMsg onServeTx txs
                    runServer = runAdversaryServerIR magic port onAccept codecChainSync csServer plainBlockFetchCodec blockFetchResponder txCodec provider
                (runServer >> pure ()) `catch` \(e :: SomeException) -> do
                    logMsg ("tx server exception (re-capture + restart): " <> show e)
                    threadDelay 1_000_000
```

- [ ] **Step 2: Add `getBaseTxsFromChain` to `TxSource.hs`**

Extract txs from the producer's shared chain (block bodies fetched via the existing path) instead of a separate origin scan — so capture and the served chain share one recent source:

```haskell
getBaseTxsFromChain
    :: (String -> IO ())
    -> StrictTVar IO (Chain Header)
    -> NetworkMagic -> (String, Int) -> Int
    -> IO [(GenTxId Block, SizeInBytes, GenTx Block)]
getBaseTxsFromChain log_ chainVar magic hp want = do
    hdrs <- Chain.toOldestFirst <$> atomically (readTVar chainVar)
    let recent = reverse (take 50 (reverse hdrs))   -- prefer recent headers
    blocks <- fmap concat $ forM recent $ \h -> do
        r <- fetchBlock magic (fst hp) (fromIntegral (snd hp)) (castPoint (headerPoint h))
        pure $ case r of Right (Just b) -> [b]; _ -> []
    let txs = take want [ (txId t, SizeInBytes (fromIntegral (LBS.length (toLazyByteString (encTx t)))), t)
                        | b <- blocks, t <- extractTxs b ]
    log_ ("getBaseTxsFromChain: " <> show (length txs) <> " txs from " <> show (length blocks) <> " recent blocks")
    pure txs
```

Export it; add imports (`StrictTVar`, `readTVar`, `atomically`, `Chain`, `headerPoint`, `castPoint`, `forM`).

- [ ] **Step 3: Compile exe**

Run: `cabal build exe:dwarf-adversary 2>&1 | tail -20`. Expected: compiles.

- [ ] **Step 4: Commit**

```bash
git add app/Main.hs src/DwarfAdversary/TxSource.hs
git commit -m "SP3b-fix3: runServeTxSubmission on advancing CaughtUp peer; capture from shared chain"
```

---

### Task 6: Local verification — CaughtUp + serve fires (the gate that was missing)

**Files:**
- Create: `tools/sp3_caughtup_repro.sh`
- Build: image `0.6.0` on cardano-box

- [ ] **Step 1: Build + image `0.6.0`**

```bash
ssh cardano-box 'cd /home/nigel/dwarf-v4/antithesis/components/dwarf-adversary && export PATH=$HOME/.ghcup/bin:$PATH && cabal build exe:dwarf-adversary && ./build-image.sh ghcr.io/j-gainsec/dwarf-adversary:0.6.0'
```

- [ ] **Step 2: Write `tools/sp3_caughtup_repro.sh`**

Fresh testnet; **inject a tx** via cardano-cli to relay1's N2C socket (the local tx-generator produces none) so the chain has a tx to capture; run the tx adversary `0.6.0`; assert (a) relay2 reaches **CaughtUp** (patch relay2 trace config to enable `Consensus.GSM` Debug, grep forwarded trace for `CaughtUp`), (b) the adversary logs `tx-submission: serving` (relay2 sent `RequestTxs`), (c) `RestartCount=0`. Hard-fail if serve not observed.

```bash
#!/usr/bin/env bash
set -uo pipefail
TAG="${1:-0.6.0}"
cd /home/nigel/dwarf-v4/antithesis/cardano_node_dwarf
export INTERNAL_NETWORK=false
# enable GSM + tx-submission tracing on the shared config (configurator must not re-run after)
docker run --rm -v cardano_node_dwarf_p1-configs:/c python:3-alpine python -c "
import json;p='/c/configs/config.json';c=json.load(open(p));t=c.setdefault('TraceOptions',{})
[t.__setitem__(n,{'severity':'Debug'}) for n in ['Consensus.GSM','TxSubmission.Remote','TxSubmission.TxInbound']];json.dump(c,open(p,'w'))"
sed -i "s#dwarf-adversary:0\\.[0-9]*\\.[0-9]*#dwarf-adversary:${TAG}#" docker-compose.yaml
docker compose up -d configurator tracer tracer-sidecar p1 p2 p3 relay1 >/dev/null 2>&1
sleep 20
# inject one tx from the genesis faucet utxo via cardano-cli inside relay1
docker compose exec -T relay1 sh -lc '
  cardano-cli query utxo --testnet-magic 42 --socket-path /state/node.socket --whole-utxo --output-json > /tmp/u.json 2>/dev/null || true
  # build+sign+submit a self-transfer (faucet skey at /utxo-keys/genesis.1.skey); see runbook for full cli steps
  true'
docker compose up -d --force-recreate dwarf-adversary >/dev/null 2>&1
docker compose rm -sf relay2 >/dev/null 2>&1
docker volume rm cardano_node_dwarf_relay2-state >/dev/null 2>&1
docker compose up -d relay2 >/dev/null 2>&1
echo "observing 180s (producer warms chain, node reaches CaughtUp, 60s init delay)…"
sleep 180
SERVED=$(docker logs dwarf-adversary 2>&1 | grep -c "tx-submission: serving")
RC=$(docker inspect dwarf-adversary --format '{{.RestartCount}}')
CAUGHTUP=$(docker logs tracer-sidecar 2>&1 | grep -ic "CaughtUp")
echo "served=$SERVED RestartCount=$RC caughtUp=$CAUGHTUP"
if [ "$SERVED" -gt 0 ] && [ "$RC" -eq 0 ]; then echo "OK: tx adversary served + stable"; else echo "FAIL: no serve / crash"; exit 1; fi
```

Fill in the cardano-cli build/sign/submit steps from the `cardano-node` image's CLI (genesis faucet key `/utxo-keys/genesis.1.skey`, network-magic 42); record the exact commands in `AGENTS.md` once they work. If GSM trace isn't on stdout, read it from the tracer-sidecar/tracer log files.

- [ ] **Step 3: Run the repro; iterate until serve observed**

```bash
ssh cardano-box 'cd /home/nigel/dwarf-v4 && bash tools/sp3_caughtup_repro.sh 0.6.0 2>&1 | tail -8'
```
Expected: `served>0`, `RestartCount=0`, `caughtUp>0`. If `served=0`, this is still the gating bug — re-investigate (do NOT submit live).

- [ ] **Step 4: Commit**

```bash
git add tools/sp3_caughtup_repro.sh
git commit -m "SP3-foundation: local CaughtUp+serve verification gate (tx injection + GSM trace)"
```

---

### Task 7: Image/generator bump + regression

**Files:**
- Modify: `dwarf/profile_manager/antithesis_generator.py` (`ADVERSARY_IMAGE` → `0.6.0`)
- Modify: `tools/test_sp2_generator.py` (`endswith(":0.6.0")`)
- Modify: `tools/sp3b_roundtrip.sh` (`--tag 0.6.0`)

- [ ] **Step 1: Bump image refs to 0.6.0** (the three files above).

- [ ] **Step 2: Unit tests**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && PYTHONPATH=dwarf python3 -m pytest tools/test_sp2_generator.py -q`
Expected: 20 passed.

- [ ] **Step 3: Round-trip on cardano-box**

Run: `ssh cardano-box 'cd /home/nigel/dwarf-v4 && bash tools/sp3b_roundtrip.sh'`
Expected: all 5 bundles OK.

- [ ] **Step 4: Selftests (block + tx, 0-crash)**

Run the existing `tools/sp3a_selftest.sh` and `tools/sp3b_selftest.sh` on cardano-box; expected: green (IR wiring intact).

- [ ] **Step 5: Commit**

```bash
git add dwarf/profile_manager/antithesis_generator.py tools/test_sp2_generator.py tools/sp3b_roundtrip.sh
git commit -m "SP3-foundation: bump adversary image + generator to 0.6.0"
```

---

## After all tasks

Push `0.6.0` to ghcr; update `Cyber-Castellum/DWARF` testnet compose to `0.6.0`; submit BOTH a block (SP3a) and tx (SP3b) live run; confirm on-platform that `dwarf_served_mutated_block` AND `dwarf_served_mutated_tx` fire and the exit-1 finding stays gone. Update docs (`AGENTS.md`) + workbench. Only then mark SP3a and SP3b done.
