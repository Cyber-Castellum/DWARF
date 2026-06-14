# SP3a — Blockfetch Adversary Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `dwarf-adversary` serve a Term-mutated block body over blockfetch (mini-protocol #3) so a DWARF scenario targeting `cardano-node-cbor-decode-block` generates a native Antithesis test that exercises the node's block-body decoder.

**Architecture:** The full N2N responder stack already exists (`ChainSync/Connection.hs`: #2 chainsync fuzzing, #3 blockfetch as a `SendMsgNoBlocks` stub, #4 txsubmission keep-alive, #8 keepalive), and `encBlock`/`decBlock`/`codecBlockFetch` are already wired. SP3a (a) turns the blockfetch stub into a server that serves a captured real block, mutated via `Fuzz.mutateTerm`; (b) adds a `BlockSource` that captures one real block from the in-bundle node via a blockfetch *client*; (c) adds `--protocol`/`--cbor-shape` and dispatches `runServe`; (d) bumps the image to `0.2.0` and flips the generator's `block` mode to `built`. In blockfetch mode chainsync serves the **real** header (so the node requests the body) and blockfetch serves the **mutated** body.

**Tech Stack:** Haskell (ghc-9.6.7 + CHaP, cabal) for the adversary; Python 3 stdlib for the generator; Docker + Moog on cardano-box for image build, round-trip, and the live Antithesis run.

**Refinement discovered during planning (fidelity vs spec):** the spec listed a new `BlockFetch/Codec.hs` "mirroring ChainSync/Codec". That is **not needed** — the blockfetch protocol codec is already available via `Ouroboros.Network.Protocol.BlockFetch.Codec.codecBlockFetch` applied to the existing `encBlock`/`decBlock`/`encBlockPoint`/`decBlockPoint` exports of `ChainSync/Codec.hs`. The only new codec module is the *mutating* one. This reduces scope; all spec behavior (serve a mutated block over blockfetch) is still delivered.

**Build/run host:** all `cabal`/image/Moog steps run on **cardano-box** (x86_64-linux, ghcup ghc-9.6.7 + cabal 3.16 + CHaP). Local edits are made in the repo working tree; the adversary source lives at `antithesis/components/dwarf-adversary/`.

---

## File Structure

- Create: `antithesis/components/dwarf-adversary/src/DwarfAdversary/BlockFetch/MutatingCodec.hs` — `mutatingCodecBlockFetch` (mutates the block encode side via `mutateTerm`), `mutEncBlock`, `describeBlockMutation`.
- Create: `antithesis/components/dwarf-adversary/src/DwarfAdversary/BlockSource.hs` — `getBaseBlock` (capture one real `Block` from the in-bundle node via a blockfetch client).
- Modify: `antithesis/components/dwarf-adversary/src/DwarfAdversary/Application.hs` — add `fetchBlock` (blockfetch initiator client) + a `runBlockFetchApplication` mux helper.
- Modify: `antithesis/components/dwarf-adversary/src/DwarfAdversary/ChainSync/Connection.hs` — parameterize the responder builder so blockfetch mode passes a real block-serving `BlockFetchServer` + the mutating blockfetch codec; chainsync mode keeps the `SendMsgNoBlocks` stub. Add `runAdversaryServer` taking a mode.
- Modify: `antithesis/components/dwarf-adversary/app/Main.hs` — add `argProtocol`/`argShape`; dispatch `runServe` and `runSelftest` per protocol.
- Modify: `antithesis/components/dwarf-adversary/dwarf-adversary.cabal` — add the two new modules to `exposed-modules`.
- Modify: `antithesis/components/dwarf-adversary/test/FuzzSpec.hs` — add a block-Term mutation property.
- Modify: `dwarf/profile_manager/antithesis_generator.py` — `ADVERSARY_MODES['cardano-node-cbor-decode-block'].built = True`; `derive_adversary` emits `--protocol`/`--cbor-shape`; image → `dwarf-adversary:0.2.0`.
- Modify: `tools/test_sp2_generator.py` — assert the block scenario now generates + the new flags.
- Create: `tools/sp3a_selftest.sh` — build + blockfetch selftest on cardano-box.

---

## Task 1: mutating block codec + FuzzSpec property

**Files:**
- Create: `src/DwarfAdversary/BlockFetch/MutatingCodec.hs`
- Modify: `dwarf-adversary.cabal` (exposed-modules)
- Modify: `test/FuzzSpec.hs`

- [ ] **Step 1: Write the module (mirror `ChainSync/MutatingCodec.hs`)**

```haskell
{-# LANGUAGE OverloadedStrings #-}

-- | A block-fetch codec identical to the plain one except the block
-- /encode/ path is fuzzed: each block is encoded, its CBOR decoded to a
-- 'Term', structurally mutated (DwarfAdversary.Fuzz), and re-encoded.
-- Determinism: the mutation is a pure function of (seed, blockBytes) —
-- no IORef/clock/entropy — so any finding is seed-reproducible.
module DwarfAdversary.BlockFetch.MutatingCodec
    ( mutatingCodecBlockFetch
    , mutEncBlock
    , describeBlockMutation
    ) where

import Codec.CBOR.Read (deserialiseFromBytes)
import Codec.CBOR.Term (decodeTerm, encodeTerm)
import Codec.CBOR.Write (toLazyByteString)
import Codec.Serialise (DeserialiseFailure)
import Codec.Serialise.Encoding (Encoding)
import Data.Bits (xor)
import Data.ByteString.Lazy qualified as LBS
import Data.Word (Word64)
import DwarfAdversary.ChainSync.Codec
    ( Block, encBlock, decBlock, encBlockPoint, decBlockPoint )
import DwarfAdversary.Fuzz (MutationInfo (..), mutateTerm)
import Network.TypedProtocol.Codec (Codec)
import Ouroboros.Network.Protocol.BlockFetch.Codec (codecBlockFetch)
import Ouroboros.Network.Protocol.BlockFetch.Type (BlockFetch)
import qualified Ouroboros.Network.Block as Network
import System.Random (mkStdGen)

mutatingCodecBlockFetch
    :: Word64 -> Double
    -> Codec (BlockFetch Block (Network.Point Block)) DeserialiseFailure IO LBS.ByteString
mutatingCodecBlockFetch seed rate =
    codecBlockFetch (mutEncBlock seed rate) decBlock encBlockPoint decBlockPoint

-- | Encode a block, then structurally mutate its CBOR before emitting.
-- On the (should-not-happen) failure to decode the block's own CBOR as a
-- single Term, emit the original encoding unchanged.
mutEncBlock :: Word64 -> Double -> Block -> Encoding
mutEncBlock seed rate b =
    let original = encBlock b
        bytes = toLazyByteString original
        g = mkStdGen (fromIntegral (seed `xor` fromIntegral (LBS.length bytes)))
    in case deserialiseFromBytes decodeTerm bytes of
        Right (rest, term) | LBS.null rest ->
            let (term', _info) = mutateTerm g rate term
            in encodeTerm term'
        _ -> original

describeBlockMutation :: Word64 -> Double -> Block -> MutationInfo
describeBlockMutation seed rate b =
    let bytes = toLazyByteString (encBlock b)
        g = mkStdGen (fromIntegral (seed `xor` fromIntegral (LBS.length bytes)))
    in case deserialiseFromBytes decodeTerm bytes of
        Right (rest, term) | LBS.null rest -> snd (mutateTerm g rate term)
        _ -> MutationInfo { miKind = "none", miDepth = 0 }
```

> Before building, confirm the exact `mutateTerm` signature and `codecBlockFetch` arg order against `src/DwarfAdversary/Fuzz.hs` and the installed `Ouroboros.Network.Protocol.BlockFetch.Codec`. `mutEncHeader` in `ChainSync/MutatingCodec.hs` is the reference for the `(seed xor len)` keying and the `deserialiseFromBytes decodeTerm` pattern — match it exactly.

- [ ] **Step 2: Add the module to the cabal `exposed-modules`**

In `dwarf-adversary.cabal`, under `library` → `exposed-modules:`, add:

```
    DwarfAdversary.BlockFetch.MutatingCodec
```

- [ ] **Step 3: Add a FuzzSpec property for block Terms**

In `test/FuzzSpec.hs`, add a property that a representative nested-array Term (block-shaped) survives each mutation kind without a Haskell exception and stays a well-formed `Term` (re-encodes):

```haskell
    it "mutates block-shaped Terms without crashing and stays encodable" $
        property $ \(seed :: Word64) ->
            let g = mkStdGen (fromIntegral seed)
                term = TList [ TList [TInt 1, TBytes "abc"], TBytes "body", TInteger 7 ]
                (term', _) = mutateTerm g 1.0 term
            in LBS.length (toLazyByteString (encodeTerm term')) `seq` True
```

> `mutateTerm` operates on `Codec.CBOR.Term.Term`, which is type-agnostic, so the existing header tests already cover the engine; this adds a block-shaped witness. Match the import list already used in `FuzzSpec.hs` (it imports `mutateTerm`, `mutationKinds`).

- [ ] **Step 4: Build + test on cardano-box**

```bash
ssh cardano-box 'cd <repo>/antithesis/components/dwarf-adversary && cabal build lib:dwarf-adversary && cabal test'
```
Expected: library compiles with the new module; hspec suite passes including the new property.

- [ ] **Step 5: Commit**

```bash
git add antithesis/components/dwarf-adversary/src/DwarfAdversary/BlockFetch/MutatingCodec.hs \
        antithesis/components/dwarf-adversary/dwarf-adversary.cabal \
        antithesis/components/dwarf-adversary/test/FuzzSpec.hs
git commit -m "feat(sp3a): mutating block-fetch codec + block-Term fuzz property"
```

---

## Task 2: BlockSource — capture one real block via a blockfetch client

**Files:**
- Modify: `src/DwarfAdversary/Application.hs` (add `fetchBlock` initiator)
- Create: `src/DwarfAdversary/BlockSource.hs`
- Modify: `dwarf-adversary.cabal`

- [ ] **Step 1: Add a blockfetch initiator to `Application.hs`**

Mirror `chainSyncToOuroboros`/`runChainSyncApplication` (chainsync initiator on #2) for blockfetch (#3). The client requests the range `[point, point]` and returns the received `Block`.

```haskell
-- | Fetch the block at @point@ from an in-bundle node via block-fetch
-- (mini-protocol #3). Returns the real, unmutated block. Used only to
-- capture a base block to later serve mutated. Hermetic (in-bundle host).
fetchBlock
    :: NetworkMagic -> String -> PortNumber -> Point -> IO (Either String Block)
fetchBlock magic host port point = ...
    -- InitiatorProtocolOnly on MiniProtocolNum 3 using
    -- codecBlockFetch encBlock decBlock encBlockPoint decBlockPoint and
    -- blockFetchClientPeer of a client that sends MsgRequestRange (point,point),
    -- collects one MsgBlock, then MsgClientDone. Reuse runChainSyncApplication's
    -- withServerNode/withIOManager scaffolding (extract the shared connect path
    -- if convenient; otherwise copy the initiator setup used at lines ~146-176).
```

> Implementation note: the initiator connect scaffolding already exists in `runChainSyncApplication` (`Connection.hs` ~146-176, `simpleSingletonVersions NodeToNodeV_14 ...`). Add a sibling `runBlockFetchApplication` that swaps the mini-protocol to #3 with `blockFetchClientPeer`. Confirm `blockFetchClientPeer` + `BlockFetchClient` constructors against the installed `ouroboros-network-protocols` `Ouroboros.Network.Protocol.BlockFetch.Client`.

- [ ] **Step 2: Write `BlockSource.hs` (mirror `HeaderSource.hs`)**

```haskell
{-# LANGUAGE NumericUnderscores #-}
{-# LANGUAGE OverloadedStrings #-}

-- | Obtain one decodable base 'Block' to mutate and serve. Hermetic:
-- captured from an in-bundle node (chain-sync a header to learn a point,
-- then block-fetch that point's body), never an external node.
module DwarfAdversary.BlockSource ( getBaseBlock ) where

import Control.Concurrent (threadDelay)
import DwarfAdversary (blockPoint)            -- header -> its Point (add if absent)
import DwarfAdversary.Application (Limit (..), syncHeaders, fetchBlock)
import DwarfAdversary.ChainSync.Codec (Block, Header)
import Ouroboros.Network.Magic (NetworkMagic)

getBaseBlock
    :: (String -> IO ()) -> NetworkMagic -> (String, Int) -> IO Block
getBaseBlock log_ magic (host, port) = go (40 :: Int)
  where
    go 0 = error "BlockSource: gave up capturing a base block from upstream"
    go n = do
        hs <- syncHeaders magic host (fromIntegral port) <originPoint> (Limit 5)
        case hs of
            Right (h:_) -> do
                r <- fetchBlock magic host (fromIntegral port) (blockPoint h)
                case r of
                    Right b  -> log_ "captured base block" >> pure b
                    Left e   -> retry n ("body fetch failed: " <> e)
            _ -> retry n "no header yet"
    retry n why = log_ ("retry: " <> why) >> threadDelay 1_500_000 >> go (n - 1)
```

> Resolve the real names for `originPoint` and a `header -> Point` accessor from `DwarfAdversary` (`HeaderSource.hs` imports `originPoint` from `DwarfAdversary`; check whether a `blockPoint`/`headerPoint` helper exists and add a tiny one if not). Keep the retry/40 pattern identical to `HeaderSource`.

- [ ] **Step 3: Add `DwarfAdversary.BlockSource` to cabal `exposed-modules`; export `fetchBlock`/`runBlockFetchApplication` from `Application`.**

- [ ] **Step 4: Build on cardano-box**

```bash
ssh cardano-box 'cd <repo>/antithesis/components/dwarf-adversary && cabal build lib:dwarf-adversary'
```
Expected: compiles.

- [ ] **Step 5: Commit**

```bash
git add antithesis/components/dwarf-adversary/src/DwarfAdversary/BlockSource.hs \
        antithesis/components/dwarf-adversary/src/DwarfAdversary/Application.hs \
        antithesis/components/dwarf-adversary/dwarf-adversary.cabal
git commit -m "feat(sp3a): BlockSource — capture a real base block via block-fetch client"
```

---

## Task 3: serve the mutated block (blockfetch responder + responder generalization)

**Files:**
- Modify: `src/DwarfAdversary/ChainSync/Connection.hs`

- [ ] **Step 1: Add a block-serving responder**

Replace the `SendMsgNoBlocks` stub with a builder that serves the captured block (the codec mutates the bytes), then reverts to no-blocks for subsequent ranges:

```haskell
-- | A block-fetch responder that serves the captured base block for the
-- first requested range, then reports no-blocks. The mutation is applied
-- by mutatingCodecBlockFetch on the encode side, so this serves the real
-- Block value and the wire bytes come out structurally mutated.
servingBlockFetchResponder
    :: (Block -> IO ()) -> Block -> BlockFetchServer Block (Network.Point Block) IO ()
servingBlockFetchResponder onServe blk =
    BlockFetchServer
        (\_range -> do
            onServe blk
            pure (SendMsgStartBatch (pure (SendMsgBlock blk
                     (pure (SendMsgBatchDone (pure noBlocks)))))))
        ()
  where
    noBlocks = BlockFetchServer (\_ -> pure (SendMsgNoBlocks (pure noBlocks))) ()
```

> Confirm the `BlockFetchBlockSender`/`BlockFetchSendBlocks` constructors (`SendMsgStartBatch`, `SendMsgBlock`, `SendMsgBatchDone`, `SendMsgNoBlocks`) and their arities against the installed `Ouroboros.Network.Protocol.BlockFetch.Server` (the module is already imported in `Connection.hs`). Adjust nesting to match the real types.

- [ ] **Step 2: Parameterize the responder + server by mode**

Generalize `chainSyncToResponder` (and `runChainSyncServer`) so the #2 codec/server and the #3 codec/server are supplied. Add an `AdversaryMode` to choose:

```haskell
data AdversaryMode
    = ChainSyncFuzz                 -- #2 mutating header, #3 no-blocks (current)
    | BlockFetchFuzz Block          -- #2 real header, #3 mutating block server

-- responder builder now takes the chain-sync codec+server AND the
-- block-fetch codec+server; runAdversaryServer wires them from the mode.
```

In `BlockFetchFuzz blk` mode: #2 uses the **plain** `codecChainSync` + a chain-sync server that advertises the **real** captured header(s) (unmutated — so the node accepts the chain and requests the body); #3 uses `mutatingCodecBlockFetch seed rate` + `servingBlockFetchResponder onServe blk`. In `ChainSyncFuzz` mode the behavior is byte-for-byte the current path.

> Keep `chainSyncToResponder`'s existing #4/#8 responders unchanged. The minimal change is to make the #2 and #3 `(codec, peer)` pairs parameters of the builder, then construct them from the mode in `runServe`.

- [ ] **Step 3: Build on cardano-box**

```bash
ssh cardano-box 'cd <repo>/antithesis/components/dwarf-adversary && cabal build lib:dwarf-adversary'
```
Expected: compiles.

- [ ] **Step 4: Commit**

```bash
git add antithesis/components/dwarf-adversary/src/DwarfAdversary/ChainSync/Connection.hs
git commit -m "feat(sp3a): serve mutated block over block-fetch; mode-parameterized responder"
```

---

## Task 4: CLI `--protocol`/`--cbor-shape` + runServe dispatch + selftest + SDK

**Files:**
- Modify: `app/Main.hs`

- [ ] **Step 1: Extend the args record + parser**

```haskell
data Args = Args
    { argMagic :: Word32, argPort :: Int, argRate :: Double, argSeed :: Word64
    , argUpstream :: Maybe (String, Int), argSelftest :: Bool
    , argProtocol :: String        -- "chainsync" (default) | "blockfetch"
    , argShape :: String           -- "block-header" (default) | "block"
    }
```

Add to `argsParser`:

```haskell
        <*> option str ( long "protocol" <> metavar "P" <> value "chainsync"
                <> help "Mini-protocol to fuzz: chainsync (default) | blockfetch." )
        <*> option str ( long "cbor-shape" <> metavar "S" <> value "block-header"
                <> help "Target CBOR shape: block-header (default) | block." )
```

- [ ] **Step 2: Dispatch `runServe`/`runSelftest` by protocol**

```haskell
    if argSelftest args
        then case argProtocol args of
               "blockfetch" -> runBlockFetchSelftest logMsg magic port
               _            -> runSelftest logMsg magic port
        else case argProtocol args of
               "blockfetch" -> runServeBlockFetch logMsg args magic port
               _            -> runServe logMsg args magic port
```

`runServeBlockFetch` mirrors `runServe` but: (1) require `--upstream`; (2) `blk <- getBaseBlock logMsg magic hp`; (3) capture the matching real header(s) via `getBaseHeaders` for the chain-sync advertisement; (4) build the server in `BlockFetchFuzz blk` mode (real header on #2, `mutatingCodecBlockFetch seed rate` on #3); (5) emit SDK assertions:

```haskell
    SDK.reachable "dwarf_block_decoder_reachable"
        (object ["seed" .= argSeed args, "shape" .= argShape args])
    -- per served block:
    SDK.sometimes True "dwarf_served_mutated_block"
        (object ["kind" .= miKind inf, "depth" .= miDepth inf, "seed" .= argSeed args])
```

`runBlockFetchSelftest`: start the block-fetch-serving server, then drive `fetchBlock` (our own client) against `127.0.0.1` and log decode success/clean-error.

- [ ] **Step 3: Build the executable on cardano-box**

```bash
ssh cardano-box 'cd <repo>/antithesis/components/dwarf-adversary && cabal build exe:dwarf-adversary'
```
Expected: compiles.

- [ ] **Step 4: Commit**

```bash
git add antithesis/components/dwarf-adversary/app/Main.hs
git commit -m "feat(sp3a): --protocol/--cbor-shape, block-fetch serve + selftest, block SDK asserts"
```

---

## Task 5: blockfetch selftest on cardano-box (local proof, 0 crashes)

**Files:**
- Create: `tools/sp3a_selftest.sh`

- [ ] **Step 1: Write the selftest harness**

```bash
#!/usr/bin/env bash
# Build the adversary and run the block-fetch selftest: the server serves a
# (mutated) block and our own block-fetch client decodes it or clean-errors.
# Run on cardano-box. Expect: handshake completes, block decoded or clean
# error, NO crash/panic.
set -uo pipefail
cd "$(dirname "$0")/../antithesis/components/dwarf-adversary"
cabal build exe:dwarf-adversary || { echo "FAIL build"; exit 1; }
BIN=$(cabal list-bin dwarf-adversary)
timeout 60 "$BIN" --selftest --protocol blockfetch --seed 0x1 --mutation-rate 0.5 2>&1 | tee /tmp/sp3a-selftest.log
grep -qiE "client result|decoded|clean error" /tmp/sp3a-selftest.log && echo "OK selftest" || { echo "FAIL selftest"; exit 1; }
grep -qiE "panic|<<loop>>|internal error|segfault" /tmp/sp3a-selftest.log && { echo "FAIL crash detected"; exit 1; } || true
echo "sp3a selftest done"
```

- [ ] **Step 2: Run it**

```bash
ssh cardano-box 'cd <repo> && chmod +x tools/sp3a_selftest.sh && tools/sp3a_selftest.sh'
```
Expected: `OK selftest`, no crash, `sp3a selftest done`.

> The blockfetch selftest does not need a base block from an external node — the selftest can construct/serve a minimal real block or reuse a fixture; if `getBaseBlock` needs an upstream, point the selftest at a throwaway in-process server or skip capture in selftest mode (serve a fixture block). Decide in Task 4's `runBlockFetchSelftest`; keep it hermetic and crash-free.

- [ ] **Step 3: Commit**

```bash
git add tools/sp3a_selftest.sh
git commit -m "test(sp3a): block-fetch selftest harness (0-crash gate on cardano-box)"
```

---

## Task 6: generator changes + SP2 regression

**Files:**
- Modify: `dwarf/profile_manager/antithesis_generator.py`
- Modify: `tools/test_sp2_generator.py`

- [ ] **Step 1: Write the failing test**

```python
# tools/test_sp2_generator.py
BLOCK = ROOT / "dwarf/scenarios/cardano-node-cbor-block-fuzz-structured.yaml"

def test_block_scenario_now_builds():
    s = _load(BLOCK)
    adv = gen.derive_adversary(s)            # no longer raises
    assert adv["protocol"] == "blockfetch"
    assert adv["shape"] == "block"
    assert "--protocol" in adv["command_args"] and "blockfetch" in adv["command_args"]
    assert "--cbor-shape" in adv["command_args"] and "block" in adv["command_args"]

def test_header_scenario_still_emits_protocol_flags():
    s = _load(HEADER)
    adv = gen.derive_adversary(s)
    assert "--protocol" in adv["command_args"] and "chainsync" in adv["command_args"]
    assert adv["image"] == gen.ADVERSARY_IMAGE   # bumped to 0.2.0
```

- [ ] **Step 2: Run it, verify failure**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && python3 -m pytest tools/test_sp2_generator.py -k "block_scenario or protocol_flags" -q`
Expected: FAIL — `block` still raises; no `--protocol` in args.

- [ ] **Step 3: Implement**

In `antithesis_generator.py`:

```python
ADVERSARY_IMAGE = "ghcr.io/j-gainsec/dwarf-adversary:0.2.0"   # was 0.1.0
# ...
    "cardano-node-cbor-decode-block": {"protocol": "blockfetch", "shape": "block", "built": True},
```

In `derive_adversary`, append the mode flags to `command_args`:

```python
        "command_args": [
            "--network-magic", str(NETWORK_MAGIC),
            "--listen-port", str(ADVERSARY_LISTEN_PORT),
            "--mutation-rate", _fmt_rate(fs["mutation_rate"]),
            "--upstream", ADVERSARY_UPSTREAM,
            "--seed", SEED_LAUNCH_PLACEHOLDER,
            "--protocol", mode["protocol"],
            "--cbor-shape", mode["shape"],
        ],
```

- [ ] **Step 4: Run the full generator suite**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && python3 -m pytest tools/test_sp2_generator.py -q`
Expected: all pass (existing header tests still green — they assert `--mutation-rate`/`--seed`/etc. presence, which is unchanged; the image-ref assertion uses `gen.ADVERSARY_IMAGE` so it tracks the bump).

> If any existing test hard-codes `0.1.0` or asserts an exact arg list length, update it to the new image/flags — do not weaken the negative tests (txsubmission shapes must still raise).

- [ ] **Step 5: Commit**

```bash
git add dwarf/profile_manager/antithesis_generator.py tools/test_sp2_generator.py
git commit -m "feat(sp3a): generator emits --protocol/--cbor-shape; block mode built; image 0.2.0"
```

---

## Task 7: block-bundle round-trip on cardano-box

**Files:**
- Modify: `tools/sp2_roundtrip.sh` (parameterize the scenario) or add `tools/sp3a_roundtrip.sh`

- [ ] **Step 1: Add the block round-trip**

```bash
#!/usr/bin/env bash
# SP3a round-trip: generate the block bundle, Stage-2 gate, compose lint,
# moog asset validate; then re-run the SP2 header round-trip to prove no regression.
set -uo pipefail
cd "$(dirname "$0")/.."
for SCEN in dwarf/scenarios/cardano-node-cbor-block-fuzz-structured.yaml \
            dwarf/scenarios/cardano-node-cbor-block-header-fuzz-structured.yaml; do
  OUT=/tmp/sp3a-$(basename "$SCEN" .yaml)
  rm -rf "$OUT"
  PYTHONPATH=dwarf python3 dwarf/cardano-profile scenario run "$SCEN" \
    --backend antithesis --out "$OUT" --registry reg.example/x || { echo "FAIL gen $SCEN"; exit 1; }
  INTERNAL_NETWORK=true docker compose -f "$OUT/config/docker-compose.yaml" config >/dev/null \
    || { echo "FAIL compose $SCEN"; exit 1; }
  echo "OK $(basename "$SCEN")"
done
echo "sp3a round-trip done"
```

- [ ] **Step 2: Stage the changed files + run on cardano-box**

```bash
# stage updated antithesis_generator.py + the scenarios + script (as in SP2 staging)
scp dwarf/profile_manager/antithesis_generator.py cardano-box:/home/nigel/dwarf-v4/dwarf/profile_manager/
scp tools/sp3a_roundtrip.sh cardano-box:/home/nigel/dwarf-v4/tools/
ssh cardano-box 'cd /home/nigel/dwarf-v4 && export ADA2_PROFILE_MANAGER_CONFIG=/home/nigel/dwarf-v4/var/state/config.yaml && chmod +x tools/sp3a_roundtrip.sh && tools/sp3a_roundtrip.sh'
```
Expected: `OK cardano-node-cbor-block-fuzz-structured`, `OK cardano-node-cbor-block-header-fuzz-structured`, `sp3a round-trip done` — block bundle generates and the header path stays green.

- [ ] **Step 3: Commit**

```bash
git add tools/sp3a_roundtrip.sh
git commit -m "test(sp3a): block + header bundle round-trip on cardano-box"
```

---

## Task 8: live Antithesis run (the done bar)

**Files:** none (operational)

- [ ] **Step 1: Build + push `dwarf-adversary:0.2.0`**

```bash
ssh cardano-box 'cd <repo>/antithesis/components/dwarf-adversary && cabal build exe:dwarf-adversary && \
  ./build-image.sh ghcr.io/j-gainsec/dwarf-adversary:0.2.0'
# push requires the GHCR token (j-gainsec namespace). Use the configured PAT; never echo it.
ssh cardano-box 'docker push ghcr.io/j-gainsec/dwarf-adversary:0.2.0'
```

> The image must be **public** (Antithesis is hermetic — images are pulled only at launch). Confirm `dwarf-adversary:0.2.0` is public in the `j-gainsec` namespace, as `0.1.0` was for Phase 3b.

- [ ] **Step 2: Generate the block bundle into the Moog asset layout**

```bash
ssh cardano-box 'cd /home/nigel/dwarf-v4 && PYTHONPATH=dwarf python3 dwarf/cardano-profile scenario run \
  dwarf/scenarios/cardano-node-cbor-block-fuzz-structured.yaml \
  --backend antithesis --out <moog-asset-dir> --registry ghcr.io/j-gainsec --tag 0.2.0'
```

- [ ] **Step 3: Submit via Moog (requires real repo/user/commit — surface, don't invent)**

Use the planner first (read-only), then submit only with explicit real parameters (per AGENTS.md — do not invent org/repo/user/commit):

```bash
cardano-profile moog create-test-plan --repo <org/repo> --github-user <real-user> \
  --directory <repo-rel-dir> --commit <sha> --asset-dir <moog-asset-dir> --json
cardano-profile moog preflight --asset-dir <moog-asset-dir> --repo <org/repo> \
  --github-user <real-user> --directory <repo-rel-dir> --commit <sha> --json
# then, only on explicit go:
cardano-profile moog create-test --repo <org/repo> --github-user <real-user> \
  --directory <repo-rel-dir> --commit <sha> --duration 1 --approve --json
```

- [ ] **Step 4: Confirm the block decoder was exercised (no false-green)**

After the run lands (oracle queue + Antithesis console; on-chain facts lag), check the tracer/SDK output for: an inbound connection, a blockfetch range request, `MsgBlock` served, and the node decoding the block body — plus the `dwarf_block_decoder_reachable` / `dwarf_served_mutated_block` assertions firing. A pass with the block decoder never reached is a false-green and must be treated as a failure (the Phase 3b "passed readiness but didn't fuzz" lesson).

- [ ] **Step 5: Record the result**

Update the SP3a workbench note (`obj_7eca2c4ab83a44cea0caa788`) and `AGENTS.md` with the run id and outcome. If green, SP3a is done; mark SP3b (txsubmission) as next.

---

## Self-Review

- **Spec coverage:** blockfetch serves mutated block (Tasks 1,3) ✓; advertise real header so node requests body (Task 3 mode + Task 4 runServeBlockFetch) ✓; hermetic base-block capture (Task 2) ✓; `--protocol`/`--cbor-shape` (Task 4) ✓; generator block-mode built + uniform flags + image 0.2.0 (Task 6) ✓; SDK Sometimes/Reachable only (Task 4) ✓; FuzzSpec (Task 1) ✓; selftest (Task 5) ✓; round-trip + SP2 regression (Task 7) ✓; live Antithesis run (Task 8) ✓; txsubmission still refused (Task 6 note) ✓.
- **Fidelity note:** `BlockFetch/Codec.hs` from the spec is intentionally dropped (the protocol codec pre-exists via `codecBlockFetch` + `encBlock`/`decBlock`); documented at the top of this plan and in File Structure.
- **Type/name consistency:** `mutatingCodecBlockFetch`, `mutEncBlock`, `getBaseBlock`, `fetchBlock`, `servingBlockFetchResponder`, `AdversaryMode`/`BlockFetchFuzz`, `ADVERSARY_IMAGE=…:0.2.0`, `--protocol`/`--cbor-shape` used consistently across tasks.
- **Open verifications (do at execution, flagged inline):** exact arities of `codecBlockFetch`, `blockFetchClientPeer`, and the `BlockFetchServer`/`SendMsg*` constructors against the installed `ouroboros-network-protocols`; the `header -> Point` accessor name in `DwarfAdversary`; whether the selftest serves a fixture block or captures one. These are signature confirmations, not design gaps.
