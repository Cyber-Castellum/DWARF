# SP3a Block-Adoption Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a fresh node rooting only at the adversary adopt the chain, advance, and block-fetch bodies, so it decodes adversarially-mutated block CBOR (`dwarf_served_mutated_block` fires) — fixing SP3a's partial pass. Verified by the local `docker compose up` repro before any re-submit.

**Architecture:** Three repro-confirmed serve-path defects (wrong fixed body per request; only 5 headers; `cycle hs` invalid roll-forward). Fix: capture a **longer, genesis-contiguous chain + a point→Block map**; serve headers **once, no cycle** (then `AwaitReply`); serve the **correct body per requested point**, mutated at rate < 1. Header path (SP2) and tx path (SP3b) untouched in behavior.

**Tech Stack:** Haskell (ghc-9.6.7 + CHaP) on cardano-box; Docker for the local testnet repro; Moog for the live re-submit.

**Grounding facts (verified in code):**
- `DwarfAdversary.ChainSync.Server.chainSyncServer` (Server.hs:45): `stream hs = cycle hs` (line 51) is the cycling; the empty-list branch already parks via `pure (Right (forever (threadDelay …)))`. So **no-cycle = `stream hs = hs`** (after the list, it parks — exactly `AwaitReply`).
- `chainSyncServer`'s `recvMsgFindIntersect` already `SendMsgIntersectFound` at the client's first offered point — correct for a fresh relay offering origin against a genesis-contiguous list.
- `servingBlockFetchResponder onServe blk` (Connection.hs) serves the **one** `blk` for every range — the wrong-body bug.
- `fetchBlock magic host port (point :: Network.Point Block) :: IO (Either SomeException (Maybe Block))` (Connection.hs) — the block-fetch client.
- `getBaseBlock log_ magic (host,port) :: IO Block` and `getBaseHeaders log_ magic (host,port) want :: IO [Header]` (sync from origin, oldest-first).
- `Network.blockPoint`/`Network.castPoint` give a header's `Point Block` (used in BlockSource).
- Build/run on cardano-box (`export PATH=$HOME/.ghcup/bin:$PATH`, incremental dist-newstyle). Image is `dwarf-adversary:0.3.0`; this fix → `0.4.0`.
- The local testnet subset (configurator/tracer/tracer-sidecar/p1/p2/p3/relay2/dwarf-adversary) is currently up on cardano-box for repro; `relay2-state` was reset to genesis.

---

## File Structure

- Modify: `src/DwarfAdversary/ChainSync/Server.hs` — add a `cyclic :: Bool` parameter to `chainSyncServer` (block mode passes `False`).
- Modify: `src/DwarfAdversary/BlockSource.hs` — add `getBaseChain` (capture N headers + a `Map (Point Block) Block`).
- Modify: `src/DwarfAdversary/ChainSync/Connection.hs` — add `servingBlockFetchResponderMap` (serve the correct body per requested point from the map); export it.
- Modify: `app/Main.hs` — `runServeBlockFetch` rewire (capture chain+map, no-cycle real server, map responder); update the other `chainSyncServer` call sites for the new `cyclic` arg.
- Modify: `test/FuzzSpec.hs` — (optional) a small map-lookup/no-cycle unit check if practical; the real gate is the repro.
- Modify: `tools/sp3a_selftest.sh` / add `tools/sp3a_repro.sh` — the local testnet repro gate.
- Modify: `dwarf/profile_manager/antithesis_generator.py` — image → `0.4.0`.

---

## Task 1: no-cycle chain-sync server

**Files:** `src/DwarfAdversary/ChainSync/Server.hs`

- [ ] **Step 1: add a `cyclic` parameter**

```haskell
chainSyncServer
    :: (String -> IO ())
    -> (Header -> IO ())
    -> Bool                  -- ^ cyclic: True = cycle headers (header-fuzz, max decode coverage);
                             --   False = serve once then await (block/tx: stable adoptable chain)
    -> [Header]
    -> Tip
    -> ChainSyncServer Header Point Tip IO ()
chainSyncServer log_ onServe cyclic headers tip =
    ChainSyncServer (pure (idle (stream headers)))
  where
    stream [] = []
    stream hs = if cyclic then cycle hs else hs
    -- ... idle/recvMsgRequestNext/recvMsgFindIntersect unchanged ...
```

The non-cyclic path: after the finite list is exhausted, `stream` yields `[]`, so `recvMsgRequestNext` hits the existing `[] ->` branch and parks (`SendMsgAwaitReply`-equivalent) — relay2 keeps the stable chain.

- [ ] **Step 2: update all call sites for the new arg** (compile will list them): in `Main.hs` `runServe` → `True`; `runSelftest` → `True`; `runServeBlockFetch` → `False`; `runBlockFetchSelftest`/`runTxSubmissionSelftest` (empty headers) → `True` (irrelevant); `runServeTxSubmission` → `False`.

- [ ] **Step 3: build the library on cardano-box**

```bash
ssh cardano-box 'cd <repo>/antithesis/components/dwarf-adversary && export PATH=$HOME/.ghcup/bin:$PATH && cabal build lib:dwarf-adversary 2>&1 | tail -5'
```
Expected: compiles (exe will fail until Main call sites are updated — do those in Task 4; build `lib:` only here).

- [ ] **Step 4: commit**

```bash
git commit -am "feat(sp3a-fix): chainSyncServer cyclic flag (no-cycle = serve once then await)"
```

---

## Task 2: `getBaseChain` — capture a longer chain + point→Block map

**Files:** `src/DwarfAdversary/BlockSource.hs`, `dwarf-adversary.cabal` (add `containers` to build-depends if absent)

- [ ] **Step 1: implement `getBaseChain`**

```haskell
import Data.Map.Strict (Map)
import Data.Map.Strict qualified as Map
import Ouroboros.Network.Block (blockPoint, castPoint)

-- | Capture the first @want@ headers from genesis AND each one's block body,
-- returning the ordered headers plus a point->block map keyed by the block-fetch
-- point. Bodies are fetched once at startup via the block-fetch client.
getBaseChain
    :: (String -> IO ())
    -> NetworkMagic
    -> (String, Int)
    -> Int
    -> IO ([Header], Map (Network.Point Block) Block)
getBaseChain log_ magic (host, port) want = do
    hs <- getBaseHeaders log_ magic (host, port) want
    pairs <- fmap concat $ forM hs $ \h -> do
        let pt = castPoint (blockPoint h)
        r <- fetchBlock magic host (fromIntegral port) pt
        case r of
            Right (Just b) -> pure [(pt, b)]
            _              -> log_ "getBaseChain: body fetch miss" >> pure []
    log_ ("getBaseChain: " <> show (length hs) <> " headers, " <> show (length pairs) <> " bodies")
    pure (hs, Map.fromList pairs)
```

> `getBaseHeaders` must be raised to capture many headers — call it with a large `want` (e.g. 200). Confirm `getBaseHeaders` honors a large limit (it syncs from origin up to `Limit want`); if the chain is shorter than `want`, it returns what exists. Add `forM` import (`Control.Monad`). Add `fetchBlock` import from `DwarfAdversary.ChainSync.Connection`. Add `containers` to the library `build-depends` if not already present.

- [ ] **Step 2: export `getBaseChain`; build lib; commit**

```bash
ssh cardano-box '… cabal build lib:dwarf-adversary'
git commit -am "feat(sp3a-fix): getBaseChain — capture longer chain + point->block map"
```

---

## Task 3: point-aware mutating block-fetch responder

**Files:** `src/DwarfAdversary/ChainSync/Connection.hs`

- [ ] **Step 1: add `servingBlockFetchResponderMap`**

```haskell
import Data.Map.Strict (Map)
import Data.Map.Strict qualified as Map

-- | Serve the CORRECT body for each requested point from the captured map (the
-- block-fetch codec mutates bodies on the wire). Walk the requested range's
-- bounds; serve each block found in the map, in order; no-blocks for misses.
-- Replaces the fixed-block responder for block-fetch mode.
servingBlockFetchResponderMap
    :: (Block -> IO ())
    -> Map (Network.Point Block) Block
    -> [Network.Point Block]               -- ^ ordered chain points (for range expansion)
    -> BlockFetchServer Block (Network.Point Block) IO ()
servingBlockFetchResponderMap onServe blocks orderedPts = server
  where
    server = BlockFetchServer handleRange ()
    handleRange (ChainRange lo hi) = do
        let inRange = takeWhile (<= hi) (dropWhile (< lo) orderedPts)
            blks = [b | p <- inRange, Just b <- [Map.lookup p blocks]]
        case blks of
            [] -> pure (SendMsgNoBlocks (pure server))
            (b0 : rest) -> do
                onServe b0
                pure (SendMsgStartBatch (sendAll b0 rest))
    sendAll b more = pure (SendMsgBlock b (afterEach more))
    afterEach []        = pure (SendMsgBatchDone (pure server))
    afterEach (b : bs)  = onServe b >> pure (SendMsgBlock b (afterEach bs))
```

> Confirm `Point Block` has an `Ord` instance for `<=`/`takeWhile`/`Map` keys (it should — points are ordered by slot). If `ChainRange`'s bounds are not directly comparable, expand the range using `orderedPts` index positions of `lo`/`hi` instead. Confirm the `BlockFetchSendBlocks` continuation types against the installed library (mirror the existing `servingBlockFetchResponder`). Keep the old fixed-block `servingBlockFetchResponder` for now (unused after Task 4) or remove it.

- [ ] **Step 2: export `servingBlockFetchResponderMap`; build lib; commit**

---

## Task 4: rewire `runServeBlockFetch`

**Files:** `app/Main.hs`

- [ ] **Step 1: capture the chain+map and wire the no-cycle server + map responder**

```haskell
runServeBlockFetch logMsg args magic port = do
    SDK.reachable "dwarf_block_fuzz_server_started" (object [ … ])
    hp <- maybe (error "block-fetch mode requires --upstream") pure (argUpstream args)
    (headers, blocks) <- getBaseChain logMsg magic hp 200
    SDK.sometimes (not (null headers)) "dwarf_base_header_obtained" (object ["count" .= length headers])
    let tip       = tipFromHeaders headers
        csServer  = chainSyncServer logMsg (\_ -> pure ()) False headers tip   -- real headers, NO cycle
        orderedPts= map (castPoint . blockPoint) headers
        bfCodec   = mutatingCodecBlockFetch (argSeed args) (argRate args)
        onServeBlk b = do
            let inf = describeBlockMutation (argSeed args) (argRate args) b
            SDK.sometimes True "dwarf_served_mutated_block"
                (object ["kind" .= miKind inf, "depth" .= miDepth inf, "seed" .= argSeed args])
        bfServer  = servingBlockFetchResponderMap onServeBlk blocks orderedPts
    SDK.reachable "dwarf_block_decoder_reachable" (object ["seed" .= argSeed args])
    let onAccept p = logMsg ("inbound connection accepted from " <> p)
                       >> SDK.reachable "dwarf_node_connected" (object ["peer" .= p])
    _ <- runChainSyncServer magic port onAccept codecChainSync csServer bfCodec bfServer
    pure ()
```

- [ ] **Step 2:** update the other `chainSyncServer` call sites for the `cyclic` arg (Task 1 Step 2). Add `castPoint`/`blockPoint` + `getBaseChain` imports to Main.

- [ ] **Step 3: build the executable on cardano-box; commit**

```bash
ssh cardano-box '… cabal build exe:dwarf-adversary 2>&1 | tail -8'
git commit -am "feat(sp3a-fix): runServeBlockFetch serves real no-cycle chain + correct mutated bodies"
```

---

## Task 5: rebuild image + unit selftest

- [ ] Build `dwarf-adversary:0.4.0` (`./build-image.sh ghcr.io/j-gainsec/dwarf-adversary:0.4.0`). Run `tools/sp3a_selftest.sh` (block-fetch wiring still green, 0 crash).

---

## Task 6: LOCAL TESTNET REPRO (the gate)

**Files:** `tools/sp3a_repro.sh`

- [ ] **Step 1:** write `tools/sp3a_repro.sh`: set the testnet adversary to `0.4.0` blockfetch, `docker compose rm -sf relay2 && docker volume rm <relay2-state> && docker compose up -d configurator tracer tracer-sidecar p1 p2 p3 relay2 dwarf-adversary` (fresh relay2), wait, then assert:
  - `relay2` ChainDB logs show `AddedBlockToVolatileDB`/`ValidCandidate` (relay2 **adds blocks** — it advanced), AND
  - the adversary served bodies (instrument `onServe` to also `logMsg "serving block body"`, or infer from relay2 adding blocks fetched from its only peer).
- [ ] **Step 2:** run on cardano-box; **gate**: relay2 adds ≥1 block (no longer stuck in the FindIntersect/3×RequestNext/reset loop). If still looping, iterate (more headers / inspect relay2 ChainDB rejects) before any re-submit.

---

## Task 7: generator image bump + round-trip + regression

- [ ] `antithesis_generator.py`: `ADVERSARY_IMAGE` → `:0.4.0`. Run `tools/test_sp2_generator.py` (image assertions track `gen.ADVERSARY_IMAGE`; all green). Run `tools/sp3b_roundtrip.sh` on cardano-box (block + tx + header bundles generate + compose config OK).

---

## Task 8: re-submit live + read result

- [ ] Push public `0.4.0`; PAT-clone `Cyber-Castellum/DWARF`, set the testnet adversary to `0.4.0` + `--protocol blockfetch --cbor-shape block`, push, get SHA; `moog create-test-plan` → `create-test --approve` (J-GainSec, 1h, no-faults). Record txHash/testRunId. When it lands, read via `agent-browser` and confirm `dwarf_served_mutated_block` fired (block decoder exercised on-platform, no false-green). Record in AGENTS.md + workbench.

---

## Self-Review

- **Spec coverage:** no-cycle real chain (T1) ✓; longer chain + point→Block map (T2) ✓; correct-body-per-point responder (T3) ✓; rewire (T4) ✓; image (T5/T7) ✓; **local repro gate** (T6) ✓; live re-verify (T8) ✓; header/tx untouched (T1 call-sites keep `True`/no-behavior-change; tx serves real headers no-cycle which is fine) ✓.
- **Open verifications (at execution):** `Point Block` `Ord` for range expansion; `BlockFetchSendBlocks` continuation arities (mirror existing responder); `getBaseHeaders` large-limit behavior; `containers` in build-depends.
- **Risk/iteration note:** T6 is the real gate. If a fresh relay2 still won't advance with the longer chain + correct bodies, the next suspects (inspect via relay2 ChainDB rejects) are header/body validity under the devnet genesis params or the forecast window size — iterate locally, never re-submit on a guess.
