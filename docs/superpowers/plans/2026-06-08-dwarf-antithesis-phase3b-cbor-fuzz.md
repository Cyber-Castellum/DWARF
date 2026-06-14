# Dwarf Antithesis Phase 3b — CBOR-Fuzz Adversary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `dwarf-adversary` — a chain-sync *upstream server* the cardano-node syncs *from*, serving structurally-mutated header CBOR (seeded solely by `antithesis_random`) so the node's real header decoder runs on adversarial input, with deterministic recreation.

**Architecture:** Fork CF's `adversary` Haskell component into `components/dwarf-adversary/`. Add (1) a pure, seeded `Codec.CBOR.Term` mutation engine; (2) a chain-sync **server** (responder) that accepts the node's inbound connection and serves `MsgRollForward` headers; (3) a mutating codec that, on the encode path, decodes the header CBOR to a `Term`, mutates it, and re-encodes. The seed (`mkStdGen`) is the sole RNG source. Package as a public ghcr image, wire into the `cardano_node_dwarf` testnet, and launch via `moog create-test`.

**Tech Stack:** Haskell (GHC 9.6.6, cabal), `ouroboros-network`/`ouroboros-consensus-cardano`, `cborg` (`Codec.CBOR.Term`), `serialise`, Hspec/QuickCheck, Docker (blinklabs Haskell builder), Antithesis Fallback SDK (NDJSON), Moog.

**Spec:** `docs/superpowers/specs/2026-06-08-dwarf-antithesis-phase3b-cbor-fuzz.md`

**Reference source (read before starting):** `codebases/cardano-node-antithesis/components/adversary/` — especially `src/Adversary/ChainSync/Codec.hs` (`codecChainSync`, `encHeader`/`decHeader`), `src/Adversary/ChainSync/Connection.hs` (initiator wiring we mirror as a responder), `src/Adversary/SDK.hs` (assertion emitter we reuse verbatim), `app/Main.hs` (CLI + seed parsing), `Dockerfile`, `composer/chain-sync-client/`. Reference testnet wiring: `codebases/cardano-node-antithesis/testnets/cardano_node_adversary/`.

---

## Important context for the implementer

**This is the most exploratory phase.** The genuinely unknown part is the Ouroboros **server-role** wiring (responder handshake + `chainSyncServerPeer` + a listening snocket). The CF adversary only implements the **client** (initiator) side. The exact `ouroboros-network` server symbols cannot be pinned offline — they must be resolved against the **installed** `ouroboros-network` version during **Task 3 (the spike)**. Task 3 is therefore a research/spike task: its deliverable is *compiling server code + local evidence a real node syncs from it*. Do Task 3 before writing any mutation wiring (Tasks 4+). If the spike shows a node will not peer with our server within a reasonable effort, STOP and report — the rest of the plan depends on it.

**Build host:** all Haskell builds and Docker builds run on `cardano-box` (the blinklabs Haskell builder toolchain is there). Use `ssh cardano-box`. The local macOS checkout is for editing; sync the component to `cardano-box` for `cabal build` / `docker build`.

**Security constraints (persist throughout):** never commit or print wallet mnemonics, private keys, passphrases, PATs, or Antithesis credentials. The `write:packages` ghcr token (Task 9) is supplied by the user at that step — do not hardcode it; `docker login` reads it from stdin.

**Network/host facts:** `cardano-box` remote tree is `/home/nigel/dwarf-v4`; local is `/Users/nigel/dwarf-project/dwarf-v4`. The component lives at `dwarf-v4/antithesis/components/dwarf-adversary/` (new) so it ships inside the `Cyber-Castellum/DWARF` repo alongside the testnet at `dwarf-v4/antithesis/cardano_node_dwarf/`.

---

## File Structure

New component (`dwarf-v4/antithesis/components/dwarf-adversary/`, forked from CF `adversary`):

| File | Responsibility |
|------|----------------|
| `dwarf-adversary.cabal` | Package `dwarf-adversary`, exe `dwarf-adversary`; adds `cborg` + server deps |
| `src/DwarfAdversary/Fuzz.hs` | **Pure, seeded** `Codec.CBOR.Term` structural mutation (the core deliverable) |
| `src/DwarfAdversary/ChainSync/Codec.hs` | Forked CF `Codec.hs`: real Cardano header enc/dec + base `codecChainSync` |
| `src/DwarfAdversary/ChainSync/MutatingCodec.hs` | Wraps the encode side: header bytes → `decodeTerm` → `mutateTerm seed` → `encodeTerm` |
| `src/DwarfAdversary/ChainSync/Server.hs` | Chain-sync **server** peer (`chainSyncServerPeer`) serving a base chain of headers |
| `src/DwarfAdversary/ChainSync/Connection.hs` | **Responder** mode: accept inbound, handshake as responder, run server mini-protocol |
| `src/DwarfAdversary/HeaderSource.hs` | Obtain a decodable base header (in-environment capture, baked-fixture fallback) |
| `src/DwarfAdversary/SDK.hs` | Antithesis assertion emitter — **copied verbatim** from CF `Adversary/SDK.hs` |
| `app/Main.hs` | CLI: `--serve`/`--fuzz`, `--listen-port`, `--mutation-rate`, `--upstream`, `--network-magic`, `--seed` |
| `test/FuzzSpec.hs` | Hspec/QuickCheck for `Fuzz` (determinism, non-identity, round-trip) |
| `test/Main.hs` | `hspec-discover` entrypoint (copied from CF) |
| `composer/cbor-fuzz/finally_fuzz_summary.sh` | End-of-run coverage Sometimes marker (daemon model) |
| `Dockerfile` | Multi-stage build → `ghcr.io/cyber-castellum/dwarf-adversary` |
| `sleep.sh` | Reused entrypoint shim (copied from CF) |

Modified testnet (`dwarf-v4/antithesis/cardano_node_dwarf/`):

| File | Change |
|------|--------|
| `docker-compose.yaml` | Add `dwarf-adversary` service (public image, fault-exclusion label, hostname) |
| `relay-dwarf-topology.json` (new) | relay2's topology: adds `dwarf-adversary.example:3001` as a trustable localRoot |
| `docker-compose.yaml` (relay2) | Mount `relay-dwarf-topology.json` instead of `relay-topology.json` |

---

## Task 1: Vendor the component skeleton and confirm the stock build

**Files:**
- Create: `dwarf-v4/antithesis/components/dwarf-adversary/` (copy of CF `adversary`, renamed)
- Modify: `dwarf-v4/antithesis/components/dwarf-adversary/dwarf-adversary.cabal`

- [ ] **Step 1: Copy the CF adversary component into the DWARF tree**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4/antithesis
mkdir -p components
cp -R ../../codebases/cardano-node-antithesis/components/adversary components/dwarf-adversary
cd components/dwarf-adversary
git rm -q --cached -r . 2>/dev/null || true   # in case of stale index entries
rm -rf dist-newstyle .envrc
git mv adversary.cabal dwarf-adversary.cabal 2>/dev/null || mv adversary.cabal dwarf-adversary.cabal
```

- [ ] **Step 2: Rename the module namespace `Adversary` → `DwarfAdversary`**

Move the source tree and rewrite module headers/imports. Run from `components/dwarf-adversary/`:

```bash
mkdir -p src/DwarfAdversary
git mv src/Adversary src/DwarfAdversary/_inner 2>/dev/null || mv src/Adversary src/DwarfAdversary/_inner
# flatten: Adversary.hs -> DwarfAdversary.hs ; Adversary/* -> DwarfAdversary/*
mv src/DwarfAdversary/_inner.hs src/DwarfAdversary.hs 2>/dev/null || true
mv src/Adversary.hs src/DwarfAdversary.hs 2>/dev/null || true
mv src/DwarfAdversary/_inner/* src/DwarfAdversary/ 2>/dev/null || true
rmdir src/DwarfAdversary/_inner 2>/dev/null || true
# rewrite the namespace in every Haskell file
grep -rl 'Adversary' src app test | xargs sed -i '' 's/\bAdversary\b/DwarfAdversary/g' 2>/dev/null \
  || grep -rl 'Adversary' src app test | xargs sed -i 's/\bAdversary\b/DwarfAdversary/g'
```

Verify the layout is exactly:
```
src/DwarfAdversary.hs
src/DwarfAdversary/SDK.hs
src/DwarfAdversary/Application.hs
src/DwarfAdversary/ChainSync/Codec.hs
src/DwarfAdversary/ChainSync/Connection.hs
```

- [ ] **Step 3: Rewrite `dwarf-adversary.cabal` — package/exe names, `cborg` + server deps**

Replace the `name`, both `exposed-modules`/executable stanzas. The full `library` and `executable` stanza changes (keep `common settings` unchanged):

```cabal
cabal-version:   3.0
name:            dwarf-adversary
version:         0.1.0.0
license:         Apache-2.0
license-file:    LICENSE
author:          Dwarf / Cyber-Castellum
maintainer:      security@cyber-castellum
category:        Testing
build-type:      Simple
extra-doc-files: CHANGELOG.md

-- (keep the existing `common settings` block verbatim)

library
  import:          settings
  hs-source-dirs:  src
  build-depends:
    , aeson
    , async
    , base
    , base16-bytestring
    , bytestring
    , cardano-ledger-byron
    , cborg
    , contra-tracer
    , directory
    , filepath
    , network
    , network-mux
    , ouroboros-consensus
    , ouroboros-consensus-cardano   ^>=0.25
    , ouroboros-consensus-protocol
    , ouroboros-network
    , ouroboros-network-api
    , ouroboros-network-framework
    , ouroboros-network-mock
    , ouroboros-network-protocols
    , random
    , serialise
    , strict-stm
    , text
    , typed-protocols

  exposed-modules:
    DwarfAdversary
    DwarfAdversary.Application
    DwarfAdversary.ChainSync.Codec
    DwarfAdversary.ChainSync.Connection
    DwarfAdversary.ChainSync.MutatingCodec
    DwarfAdversary.ChainSync.Server
    DwarfAdversary.Fuzz
    DwarfAdversary.HeaderSource
    DwarfAdversary.SDK

executable dwarf-adversary
  import:         settings
  main-is:        Main.hs
  build-depends:
    , dwarf-adversary
    , aeson
    , base
    , bytestring
    , network
    , optparse-applicative
    , ouroboros-network
    , ouroboros-network-api
    , random
    , text
    , time

  hs-source-dirs: app

test-suite dwarf-adversary-test
  import:             settings
  other-modules:      FuzzSpec
  type:               exitcode-stdio-1.0
  hs-source-dirs:     test
  main-is:            Main.hs
  build-depends:
    , dwarf-adversary
    , base
    , bytestring
    , cborg
    , hspec
    , QuickCheck
    , random
    , serialise

  build-tool-depends: hspec-discover:hspec-discover
```

Delete the old `test/AdversarySpec.hs` (replaced by `FuzzSpec.hs` in Task 2):
```bash
rm -f test/AdversarySpec.hs
```

- [ ] **Step 4: Sync to `cardano-box` and confirm the stock binary still builds**

The renamed component must compile *before* adding new code (proves toolchain + rename are clean). At this point `Main.hs` still references the old `adversaryApplication` initiator path — that's fine; it compiles.

```bash
rsync -az --delete /Users/nigel/dwarf-project/dwarf-v4/antithesis/components/dwarf-adversary/ \
  cardano-box:/home/nigel/dwarf-v4/antithesis/components/dwarf-adversary/
ssh cardano-box 'cd /home/nigel/dwarf-v4/antithesis/components/dwarf-adversary && cabal build all 2>&1 | tail -20'
```
Expected: `cabal build all` succeeds (the renamed library + `dwarf-adversary` exe link). If a leftover `Adversary` reference fails to resolve, fix the offending import and rebuild.

- [ ] **Step 5: Commit**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git checkout -b phase3b-cbor-fuzz
git add antithesis/components/dwarf-adversary
git commit -m "feat(dwarf-adversary): vendor CF adversary as dwarf-adversary skeleton

Rename Adversary->DwarfAdversary, package dwarf-adversary, add cborg +
server deps to cabal. Stock binary builds on cardano-box.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `Fuzz.hs` — pure, seeded structured CBOR-Term mutation

This is the **core deliverable** and is fully testable in isolation (no network, no Ouroboros). Build it first after the skeleton.

**Files:**
- Create: `dwarf-v4/antithesis/components/dwarf-adversary/src/DwarfAdversary/Fuzz.hs`
- Test: `dwarf-v4/antithesis/components/dwarf-adversary/test/FuzzSpec.hs`

- [ ] **Step 1: Write the failing test**

`test/FuzzSpec.hs`:

```haskell
{-# LANGUAGE OverloadedStrings #-}

module FuzzSpec (spec) where

import Codec.CBOR.Term (Term (..))
import Codec.CBOR.Read (deserialiseFromBytes)
import Codec.CBOR.Term (decodeTerm, encodeTerm)
import Codec.CBOR.Write (toLazyByteString)
import Data.ByteString.Lazy qualified as LBS
import DwarfAdversary.Fuzz (MutationInfo (..), mutateTerm)
import System.Random (mkStdGen)
import Test.Hspec
import Test.QuickCheck

-- A non-trivial, decodable base Term standing in for a header body.
baseTerm :: Term
baseTerm =
    TList
        [ TInt 2
        , TList [TInt 0, TBytes "abcd"]
        , TMap [(TString "slot", TInt 12345), (TString "hash", TBytes "deadbeef")]
        , TListI [TInt 1, TInt 2, TInt 3]
        ]

spec :: Spec
spec = do
    describe "mutateTerm determinism" $ do
        it "same seed + same rate produces identical output" $
            property $ \(seed :: Int) ->
                let (a, ia) = mutateTerm (mkStdGen seed) 1.0 baseTerm
                    (b, ib) = mutateTerm (mkStdGen seed) 1.0 baseTerm
                in  a `shouldBe` b >> miKind ia `shouldBe` miKind ib

    describe "mutateTerm effect" $ do
        it "rate 0.0 is the identity" $
            let (t, info) = mutateTerm (mkStdGen 7) 0.0 baseTerm
            in  t `shouldBe` baseTerm >> miKind info `shouldBe` "none"

        it "rate 1.0 changes the Term" $
            let (t, _) = mutateTerm (mkStdGen 7) 1.0 baseTerm
            in  t `shouldNotBe` baseTerm

    describe "mutateTerm output re-encodes" $ do
        it "the mutated Term round-trips through encodeTerm" $
            let (t, _) = mutateTerm (mkStdGen 99) 1.0 baseTerm
                bytes = toLazyByteString (encodeTerm t)
                decoded = deserialiseFromBytes decodeTerm bytes
            in  case decoded of
                    Right (rest, t') -> LBS.null rest `shouldBe` True >> t' `shouldBe` t
                    Left e -> expectationFailure (show e)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
ssh cardano-box 'cd /home/nigel/dwarf-v4/antithesis/components/dwarf-adversary && cabal test 2>&1 | tail -25'
```
Expected: FAIL — `Module 'DwarfAdversary.Fuzz' not found` (or unresolved `mutateTerm`/`MutationInfo`).

- [ ] **Step 3: Write the minimal implementation**

`src/DwarfAdversary/Fuzz.hs`:

```haskell
{-# LANGUAGE OverloadedStrings #-}

-- |
-- Module: DwarfAdversary.Fuzz
--
-- Pure, seeded structural mutation of CBOR 'Term's. This is the
-- entire source of fuzz nondeterminism in dwarf-adversary: a single
-- 'StdGen' fully determines the mutation chosen and where it is
-- applied, so any finding Antithesis surfaces is reproducible from
-- the seed alone (no /dev/urandom, no clock).
--
-- The mutator walks a decodable base 'Term' (a real Cardano header,
-- decoded via 'Codec.CBOR.Term.decodeTerm') and applies ONE
-- structural perturbation at a seed-chosen position. Operating at the
-- 'Term' level (not raw bytes) keeps the output a well-formed CBOR
-- item whose *structure* is hostile (wrong lengths, swapped major
-- types, truncated/extended collections, nesting abuse) — so the
-- node's header decoder engages deeply instead of rejecting trivial
-- garbage at the framing layer.
module DwarfAdversary.Fuzz
    ( MutationInfo (..)
    , mutateTerm
    , mutationKinds
    ) where

import Codec.CBOR.Term (Term (..))
import Data.Text (Text)
import System.Random (StdGen, randomR, split)

-- | What the mutator did, for SDK assertion details.
data MutationInfo = MutationInfo
    { miKind :: Text        -- ^ e.g. "swapMajorType", "truncateList", "none"
    , miDepth :: Int        -- ^ structural depth at which it was applied
    }
    deriving (Eq, Show)

-- | The set of structural mutation kinds, by name. Stable order →
-- the seed maps to the same kind across runs.
mutationKinds :: [Text]
mutationKinds =
    [ "swapMajorType"
    , "truncateCollection"
    , "extendCollection"
    , "perturbInt"
    , "flipIndefinite"
    , "nestOnce"
    ]

-- | @mutateTerm gen rate term@ applies at most one structural mutation.
-- @rate@ in [0,1] is the probability the mutation fires; 0.0 is the
-- identity (useful for the stock-server spike). Returns the mutated
-- 'Term' and a 'MutationInfo' describing what changed.
mutateTerm :: StdGen -> Double -> Term -> (Term, MutationInfo)
mutateTerm gen rate term =
    let (roll, g1) = randomR (0.0, 1.0) gen
    in  if roll >= rate
            then (term, MutationInfo "none" 0)
            else
                let (kIx, g2) = randomR (0, length mutationKinds - 1) g1
                    kind = mutationKinds !! kIx
                in  applyKind kind g2 0 term

-- | Apply the named mutation, descending into a seed-chosen child so
-- the perturbation can land anywhere in the structure.
applyKind :: Text -> StdGen -> Int -> Term -> (Term, MutationInfo)
applyKind kind gen depth t =
    let (descend, g1) = randomR (0.0, 1.0 :: Double) gen
    in  case (descend < 0.5, children t) of
            (True, cs@(_ : _)) ->
                -- recurse into one child, keep the same kind
                let (ix, g2) = randomR (0, length cs - 1) g1
                    (child', info) = applyKind kind g2 (depth + 1) (cs !! ix)
                in  (replaceChild ix child' t, info)
            _ -> (mutateHere kind g1 t, MutationInfo kind depth)

-- | The direct CBOR children of a Term (for descent).
children :: Term -> [Term]
children (TList xs) = xs
children (TListI xs) = xs
children (TMap kvs) = concatMap (\(k, v) -> [k, v]) kvs
children (TMapI kvs) = concatMap (\(k, v) -> [k, v]) kvs
children (TTagged _ x) = [x]
children _ = []

-- | Put a mutated child back at index @ix@ (inverse of 'children').
replaceChild :: Int -> Term -> Term -> Term
replaceChild ix c (TList xs) = TList (setAt ix c xs)
replaceChild ix c (TListI xs) = TListI (setAt ix c xs)
replaceChild ix c (TMap kvs) = TMap (setKv ix c kvs)
replaceChild ix c (TMapI kvs) = TMapI (setKv ix c kvs)
replaceChild _ c (TTagged tag _) = TTagged tag c
replaceChild _ _ t = t

setAt :: Int -> a -> [a] -> [a]
setAt i x xs = [if j == i then x else y | (j, y) <- zip [0 ..] xs]

-- | Flatten index over a [(k,v)] list back into pair positions.
setKv :: Int -> Term -> [(Term, Term)] -> [(Term, Term)]
setKv flatIx c kvs =
    [ (if flatIx == 2 * j then c else k, if flatIx == 2 * j + 1 then c else v)
    | (j, (k, v)) <- zip [0 ..] kvs
    ]

-- | Apply the structural mutation to THIS term node.
mutateHere :: Text -> StdGen -> Term -> Term
mutateHere "swapMajorType" _ t = swapMajor t
mutateHere "truncateCollection" _ t = truncateColl t
mutateHere "extendCollection" g t = extendColl g t
mutateHere "perturbInt" g t = perturbInt g t
mutateHere "flipIndefinite" _ t = flipIndef t
mutateHere "nestOnce" _ t = TList [t]
mutateHere _ _ t = t

-- | Reinterpret the value under a different major type where it is
-- structurally legal but semantically wrong for the decoder.
swapMajor :: Term -> Term
swapMajor (TInt n) = TBytes (replicate (max 0 (n `mod` 8)) 0x41 `seqToBS`)
  where seqToBS = foldr (const id) mempty `asTypeOf` (const mempty) -- placeholder replaced below
swapMajor (TBytes b) = TString (decodeLatin1Safe b)
swapMajor (TString s) = TInt (length (show s))
swapMajor (TList xs) = TMap (pairUp xs)
swapMajor (TMap kvs) = TList (concatMap (\(k, v) -> [k, v]) kvs)
swapMajor t = t

-- | Drop the last element of a collection so its declared length (in
-- the definite-length encoding) over-counts what follows.
truncateColl :: Term -> Term
truncateColl (TList xs) = TList (dropLast xs)
truncateColl (TListI xs) = TListI (dropLast xs)
truncateColl (TMap kvs) = TMap (dropLast kvs)
truncateColl (TMapI kvs) = TMapI (dropLast kvs)
truncateColl (TBytes b) = TBytes (dropLastBS b)
truncateColl (TString s) = TString (dropLastT s)
truncateColl t = t

-- | Append a junk element so the collection over-runs expectations.
extendColl :: StdGen -> Term -> Term
extendColl g (TList xs) = TList (xs ++ [junk g])
extendColl g (TListI xs) = TListI (xs ++ [junk g])
extendColl g (TMap kvs) = TMap (kvs ++ [(junk g, junk g)])
extendColl g (TMapI kvs) = TMapI (kvs ++ [(junk g, junk g)])
extendColl _ t = t

perturbInt :: StdGen -> Term -> Term
perturbInt g (TInt n) = let (d, _) = randomR (minBound, maxBound) g in TInteger (fromIntegral n + fromIntegral (d :: Int) * 1000000007)
perturbInt g (TInteger n) = let (d, _) = randomR (minBound, maxBound) g in TInteger (n + fromIntegral (d :: Int))
perturbInt _ t = t

flipIndef :: Term -> Term
flipIndef (TList xs) = TListI xs
flipIndef (TListI xs) = TList xs
flipIndef (TMap kvs) = TMapI kvs
flipIndef (TMapI kvs) = TMap kvs
flipIndef (TBytes b) = TBytesI [b]
flipIndef (TString s) = TStringI [s]
flipIndef t = t

junk :: StdGen -> Term
junk g = let (n, _) = randomR (0, 3 :: Int) g
         in [TInt 0xdead, TBytes "\xff\xff", TString "junk", TList []] !! n

pairUp :: [Term] -> [(Term, Term)]
pairUp (a : b : rest) = (a, b) : pairUp rest
pairUp [a] = [(a, TNull)]
pairUp [] = []
```

> **Note for implementer:** the `swapMajor (TInt n)` line above has a deliberately broken placeholder (`seqToBS`). Replace that one equation with the clean version below before compiling — it converts a small int into a short byte string of `0x41`s:
> ```haskell
> swapMajor (TInt n) = TBytes (BS.replicate (max 0 (n `mod` 8)) 0x41)
> ```
> and add `import Data.ByteString qualified as BS`, plus helpers `decodeLatin1Safe :: BS.ByteString -> Text` (`= T.decodeLatin1`), `dropLast = reverse . drop 1 . reverse`, `dropLastBS = BS.init` (guard empty), `dropLastT = T.dropEnd 1`, with `import Data.Text qualified as T` and `import Data.Text.Encoding qualified as T`. Add these to the imports and `where`/top-level as needed so the module is warning-clean (`-Wall` is on).

- [ ] **Step 4: Run the test to verify it passes**

```bash
rsync -az /Users/nigel/dwarf-project/dwarf-v4/antithesis/components/dwarf-adversary/ \
  cardano-box:/home/nigel/dwarf-v4/antithesis/components/dwarf-adversary/
ssh cardano-box 'cd /home/nigel/dwarf-v4/antithesis/components/dwarf-adversary && cabal test 2>&1 | tail -25'
```
Expected: PASS — determinism, identity-at-0, change-at-1, and round-trip all green.

- [ ] **Step 5: Commit**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add antithesis/components/dwarf-adversary/src/DwarfAdversary/Fuzz.hs \
        antithesis/components/dwarf-adversary/test/FuzzSpec.hs \
        antithesis/components/dwarf-adversary/dwarf-adversary.cabal
git commit -m "feat(dwarf-adversary): seeded structural CBOR-Term mutation engine

Fuzz.hs: pure mutateTerm over Codec.CBOR.Term — swap major type,
truncate/extend collections, perturb ints, flip indefinite, nest.
StdGen is the sole RNG. Property tests: determinism, identity@0,
change@1, re-encode round-trip.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3 (SPIKE): Chain-sync **server** that a real node syncs from — unmutated

**Highest-risk task. Do this before any mutation wiring.** Deliverable: a stock (non-mutating) `dwarf-adversary --serve` that a real cardano-node connects to and chain-syncs headers from, proven by the node's tracer. The exact `ouroboros-network` server symbols are resolved here against the installed version.

**Files:**
- Create: `dwarf-v4/antithesis/components/dwarf-adversary/src/DwarfAdversary/ChainSync/Codec.hs` (already present from the fork — keep as-is, it provides `codecChainSync`, `Header`, `Point`, `Tip`, `Block`)
- Create: `dwarf-v4/antithesis/components/dwarf-adversary/src/DwarfAdversary/ChainSync/Server.hs`
- Create: `dwarf-v4/antithesis/components/dwarf-adversary/src/DwarfAdversary/ChainSync/Connection.hs` (replace the forked initiator file with a responder)
- Modify: `app/Main.hs` (add a minimal `--serve` path for the spike)

- [ ] **Step 1: Resolve the server-side API surface against the installed library**

On `cardano-box`, find the responder/server symbols in the installed `ouroboros-network*` packages. Record the exact names — they feed Steps 2–3.

```bash
ssh cardano-box 'cd /home/nigel/dwarf-v4/antithesis/components/dwarf-adversary && \
  cabal build all >/dev/null 2>&1; \
  echo "=== chain-sync server peer ==="; \
  ghc-pkg field ouroboros-network-protocols exposed-modules 2>/dev/null | tr " " "\n" | grep -i chainsync; \
  echo "=== server listener ==="; \
  ghc-pkg field ouroboros-network exposed-modules 2>/dev/null | tr " " "\n" | grep -iE "Socket|Server|InboundGovernor"'
```

Expected key symbols (confirm exact spelling/version):
- `Ouroboros.Network.Protocol.ChainSync.Server` exposes `ChainSyncServer (..)`, `ServerStIdle (..)`, `ServerStNext (..)`, `ServerStIntersect (..)`, and `chainSyncServerPeer :: ChainSyncServer header point tip m a -> Peer ...`.
- A listening entrypoint: prefer `Ouroboros.Network.Socket.withServerNode` (lower-level) over the full diffusion stack. Confirm its signature; it pairs with `socketSnocket`/`makeSocketBearer` (already used by the forked `Connection.hs`) and a `ResponderProtocolOnly` mini-protocol.

> If `withServerNode` is not exposed in this version, the fallback is `Ouroboros.Network.Socket.withServerNode'` or assembling `Ouroboros.Network.InboundGovernor` + `Snocket` by hand. Record whichever the installed version provides. **If no server entrypoint is reachable without pulling the entire node diffusion layer, STOP and report — this changes the feasibility of the approach.**

- [ ] **Step 2: Write `Server.hs` — a chain-sync server peer serving a fixed base chain**

`src/DwarfAdversary/ChainSync/Server.hs`. Serves `MsgRollForward` for each header in a supplied list, then `MsgAwaitReply`/done. Uses the real `Header`/`Point`/`Tip` types from the forked `Codec.hs`.

```haskell
{-# LANGUAGE OverloadedStrings #-}

module DwarfAdversary.ChainSync.Server
    ( chainSyncServer
    ) where

import DwarfAdversary.ChainSync.Codec (Header, Point, Tip)
import Ouroboros.Network.Block (getTipPoint)
import Ouroboros.Network.Protocol.ChainSync.Server
    ( ChainSyncServer (..)
    , ServerStIdle (..)
    , ServerStNext (..)
    , ServerStIntersect (..)
    )

-- | A chain-sync server that serves the given headers in order via
-- rollForward, advertising @tip@ as the chain tip. After the last
-- header it parks in await-reply (the client decides when to stop).
-- The headers are produced by the caller (Task 5: a real captured
-- header, optionally fuzzed by Task 4's mutating codec on the wire).
chainSyncServer :: [Header] -> Tip -> ChainSyncServer Header Point Tip IO ()
chainSyncServer headers tip = ChainSyncServer (pure (idle headers))
  where
    idle :: [Header] -> ServerStIdle Header Point Tip IO ()
    idle hs =
        ServerStIdle
            { recvMsgRequestNext = pure (rollNext hs)
            , recvMsgFindIntersect = \_points ->
                pure (intersect hs)
            , recvMsgDoneClient = pure ()
            }

    rollNext :: [Header] -> Either (ServerStNext Header Point Tip IO ())
                                   (IO (ServerStNext Header Point Tip IO ()))
    rollNext (h : hs) =
        Left (SendMsgRollForward h tip (ChainSyncServer (pure (idle hs))))
    rollNext [] =
        -- nothing more to serve: wait (never resolves => client drives done)
        Right (pure (SendMsgRollForward lastHeader tip
                      (ChainSyncServer (pure (idle [])))))
      where lastHeader = error "chainSyncServer: empty header list"

    intersect :: [Header] -> ServerStIntersect Header Point Tip IO ()
    intersect hs =
        -- We don't track a real chain; always report intersect-not-found
        -- and let the client sync from our rollForwards.
        SendMsgIntersectNotFound tip (ChainSyncServer (pure (idle hs)))
```

> **Implementer note:** `ServerStNext` / `recvMsgRequestNext` return shapes vary slightly by `ouroboros-network-protocols` version (some versions wrap in `Either a (m a)` for the immediate-vs-await distinction; others use `SendMsgRollForward`/`SendMsgAwaitReply` constructors directly). Adapt the constructors to the version found in Step 1. Guarantee `headers` is **non-empty** before calling (Task 5 ensures ≥1 captured header), so the `error` branch is unreachable in practice; prefer `SendMsgAwaitReply` if the version exposes it instead of re-serving the last header.

- [ ] **Step 3: Write `Connection.hs` — responder/server listener**

Replace the forked initiator `Connection.hs` with a responder. Mirror the initiator's snocket/bearer setup but bind+listen and run the chain-sync server as `ResponderProtocolOnly` on `MiniProtocolNum 2`, negotiating `NodeToNodeV_14` with the cluster's `NetworkMagic`.

```haskell
{-# LANGUAGE OverloadedStrings #-}

module DwarfAdversary.ChainSync.Connection
    ( runChainSyncServer
    ) where

import DwarfAdversary.ChainSync.Codec (Header, Point, Tip)
import Control.Tracer (nullTracer)
import Data.ByteString.Lazy (LazyByteString)
import Data.Void (Void)
import Network.Mux qualified as Mx
import Network.Socket (PortNumber)
import Network.TypedProtocol.Codec (Codec)
import Network.TypedProtocol.Peer (Peer)  -- adjust to installed module path
import Ouroboros.Network.Mux
    ( MiniProtocol (..)
    , MiniProtocolLimits (..)
    , MiniProtocolNum (MiniProtocolNum)
    , OuroborosApplication (..)
    , RunMiniProtocol (ResponderProtocolOnly)
    , StartOnDemandOrEagerly (StartOnDemand)
    , mkMiniProtocolCbFromPeer
    )
import Ouroboros.Network.Magic (NetworkMagic (..))
import Ouroboros.Network.Protocol.ChainSync.Server (chainSyncServerPeer)
import Ouroboros.Network.Protocol.ChainSync.Type (ChainSync)
import Ouroboros.Network.Snocket (makeSocketBearer, socketSnocket)
-- Server entrypoint + version-negotiation imports resolved in Step 1.

-- | Bind on @port@, accept inbound N2N connections, negotiate the
-- handshake as a responder, and run @serverCodec@ + @serverPeer@ as
-- the chain-sync mini-protocol. Blocks forever (daemon).
--
-- @serverCodec@ is either the plain 'codecChainSync' (spike) or the
-- mutating codec (Task 4). @mkServerPeer@ builds the server peer from
-- the captured headers (Task 5).
runChainSyncServer
    :: NetworkMagic
    -> PortNumber
    -> Codec (ChainSync Header Point Tip) e IO LazyByteString
    -> Peer (ChainSync Header Point Tip) pr st IO ()   -- chainSyncServerPeer (chainSyncServer ...)
    -> IO Void
runChainSyncServer = error "spike: assemble withServerNode + ResponderProtocolOnly (see Step 1 symbols)"
```

> **Implementer note (the spike's real work):** flesh out `runChainSyncServer` using the server entrypoint found in Step 1. Concretely: build the responder app with
> ```haskell
> responderApp serverCodec serverPeer =
>   OuroborosApplication
>     [ MiniProtocol
>         { miniProtocolNum = MiniProtocolNum 2
>         , miniProtocolStart = StartOnDemand
>         , miniProtocolLimits = MiniProtocolLimits { maximumIngressQueue = maxBound }
>         , miniProtocolRun =
>             ResponderProtocolOnly $
>               mkMiniProtocolCbFromPeer $ \_ctx ->
>                 (nullTracer, serverCodec, serverPeer)
>         }
>     ]
> ```
> and host it with `withServerNode` (snocket = `socketSnocket iocp`, bearer = `makeSocketBearer`, accept `NodeToNodeV_14` with the cluster `NetworkMagic`, `diffusionMode = InitiatorAndResponderDiffusionMode` or responder-only as the version allows). Use `Ouroboros.Network.Socket`'s `AcceptedConnectionsLimit`, `nullNetworkServerTracers`, and `HandshakeCallbacks { acceptCb = acceptableVersion, queryCb = queryVersion }` mirroring the forked initiator. This is iterative against the compiler — keep building until it links.

- [ ] **Step 4: Minimal `--serve` path in `Main.hs` for the spike**

Add a spike entrypoint that serves **one** hardcoded/captured header unmutated, so we isolate "does a node sync from us" from mutation and from header sourcing. Use a header obtained by the simplest means available (e.g. a single header captured once via the retained initiator code path, serialized to a file, then read back — or, for the spike only, point `--upstream` at a running devnet producer and relay its first header straight through). Keep it crude; Task 5 makes it principled.

```haskell
-- in Main.hs, dispatch on a --serve flag:
--   serveSpike magic port = do
--     hdrs <- HeaderSource.capture ...   -- or read a baked fixture
--     let tip = ... (tip built from the last header)
--     _ <- runChainSyncServer magic port codecChainSync
--            (chainSyncServerPeer (chainSyncServer hdrs tip))
--     pure ()
```

- [ ] **Step 5: Prove a real node syncs from the stock server (the spike gate)**

On `cardano-box`, stand up one cardano-node from the devnet config used by the testnet and point its topology at the running `dwarf-adversary --serve`. Run the adversary on `:3001`, magic `42`. Then inspect the node's tracer for chain-sync activity against our address.

```bash
# 1. run the stock server
ssh cardano-box 'cd /home/nigel/dwarf-v4/antithesis/components/dwarf-adversary && \
  cabal run dwarf-adversary -- --serve --network-magic 42 --listen-port 3001 &'
# 2. start a single devnet node whose topology localRoots = [{host of dwarf-adversary, 3001}]
#    (reuse cardano_node_dwarf testnet.yaml/config; minimal single-node compose or bare run)
# 3. watch the node tracer for ChainSync.* events referencing our peer
ssh cardano-box '... node tracer/log ... | grep -iE "ChainSync|RollForward|MuxError|DecoderError"'
```
**Gate (must pass to continue):** the node log shows it **connected, negotiated the handshake, sent `MsgFindIntersect`/`MsgRequestNext`, and received/decoded our `MsgRollForward`** (a `RollForward`/`AddBlock`/`DecodeError` trace referencing our peer). A bare TCP connect with no chain-sync traffic does **not** pass. If it fails, iterate on handshake version/magic and the server peer shape; if it cannot be made to peer, STOP and report.

- [ ] **Step 6: Commit the spike**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add antithesis/components/dwarf-adversary/src/DwarfAdversary/ChainSync/Server.hs \
        antithesis/components/dwarf-adversary/src/DwarfAdversary/ChainSync/Connection.hs \
        antithesis/components/dwarf-adversary/app/Main.hs \
        antithesis/components/dwarf-adversary/dwarf-adversary.cabal
git commit -m "feat(dwarf-adversary): chain-sync server — node syncs from us (spike)

Responder-mode Connection + chainSyncServerPeer serving a base chain.
Verified a real cardano-node connects, requests, and decodes our
rollForward headers. Unmutated; mutation lands in the next task.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `MutatingCodec.hs` — fuzz headers on the encode path

**Files:**
- Create: `dwarf-v4/antithesis/components/dwarf-adversary/src/DwarfAdversary/ChainSync/MutatingCodec.hs`

- [ ] **Step 1: Write `MutatingCodec.hs`**

Wrap the header *encode* side of `codecChainSync`. The base codec encodes a `Header` to CBOR; we intercept those bytes, `decodeTerm` them to a `Term`, apply `mutateTerm`, re-`encodeTerm`, and emit. Decode side is untouched (we are the server; the client decodes). Because `codecChainSync` is assembled from `encHeader`/`decHeader` (see forked `Codec.hs`), the cleanest seam is a `mutatingCodecChainSync` that re-runs `ChainSync.codecChainSync` with a mutating `encHeader`.

```haskell
{-# LANGUAGE OverloadedStrings #-}

module DwarfAdversary.ChainSync.MutatingCodec
    ( mutatingCodecChainSync
    , MutationCounter
    , newMutationCounter
    ) where

import Codec.CBOR.Read (deserialiseFromBytes)
import Codec.CBOR.Term (decodeTerm, encodeTerm)
import Codec.CBOR.Write (toBuilder)
import Codec.Serialise.Encoding (Encoding)
import Codec.CBOR.Encoding qualified as Enc
import Data.ByteString.Builder (toLazyByteString)
import Data.ByteString.Lazy qualified as LBS
import Data.IORef (IORef, atomicModifyIORef', newIORef)
import DwarfAdversary.ChainSync.Codec
    ( Block, Header, Point, Tip )
import DwarfAdversary.Fuzz (MutationInfo (..), mutateTerm)
import System.Random (StdGen, split)
-- reuse the exact enc/dec helpers from the forked Codec.hs by
-- exporting them there; see Step 2.

-- | Counts how many headers we have served (for SDK details + to vary
-- the per-header sub-seed deterministically).
type MutationCounter = IORef Int

newMutationCounter :: IO MutationCounter
newMutationCounter = newIORef 0

-- | Mutate one already-encoded header's bytes: decode to Term, mutate,
-- re-encode. If the bytes are not decodable as a single Term (should
-- not happen for valid header CBOR), pass them through unchanged.
mutateEncodedHeader :: StdGen -> Double -> LBS.ByteString -> (LBS.ByteString, MutationInfo)
mutateEncodedHeader gen rate bytes =
    case deserialiseFromBytes decodeTerm bytes of
        Right (rest, term) | LBS.null rest ->
            let (term', info) = mutateTerm gen rate term
            in  (toLazyByteString (toBuilder (encodeTerm term')), info)
        _ -> (bytes, MutationInfo "passthrough" 0)
```

> **Implementer note:** `codecChainSync` in the forked `Codec.hs` builds the codec from `encHeader :: Header -> Encoding`. The mutation must happen on the *serialized* header. Two viable seams — pick the one that types cleanly against the installed `ouroboros-network-protocols`:
> 1. **Encoding-level (preferred):** make `encHeader` produce normal `Encoding`, then post-process inside a custom `Codec` whose `encode` runs the base encoder to bytes, applies `mutateEncodedHeader`, and yields the mutated bytes. This needs access to the base codec's serialized output; if the `Codec` API only exposes typed `encode`/`decode`, build the mutating codec by hand using `ChainSync.codecChainSync` with a *mutating* `encHeader'` that does `decodeTerm . toBytes . encHeader >>= mutate >>= encodeTerm` — i.e. mutate at the `Encoding` boundary by round-tripping `encHeader`'s output through `Term`.
> 2. Export `encHeader`/`decHeader`/`encPoint`/`decPoint`/`encTip`/`decTip` from `Codec.hs` (add them to its export list) so this module can assemble `ChainSync.codecChainSync encHeader' decHeader encPoint decPoint encTip decTip` with the mutating `encHeader'`.
> Seed handling: thread `StdGen` so each served header gets a fresh sub-seed via `split` (store the evolving gen in an `IORef` next to `MutationCounter`), keeping the whole sequence a pure function of the startup seed.

- [ ] **Step 2: Export the codec helpers from `Codec.hs`**

Edit the forked `src/DwarfAdversary/ChainSync/Codec.hs` export list to also expose the building blocks the mutating codec needs:

```haskell
module DwarfAdversary.ChainSync.Codec
    ( codecChainSync
    , encHeader, decHeader
    , encPoint, decPoint
    , encTip, decTip
    , Block, Header, Tip, Point
    ) where
```

- [ ] **Step 3: Build to verify it compiles**

```bash
rsync -az /Users/nigel/dwarf-project/dwarf-v4/antithesis/components/dwarf-adversary/ \
  cardano-box:/home/nigel/dwarf-v4/antithesis/components/dwarf-adversary/
ssh cardano-box 'cd /home/nigel/dwarf-v4/antithesis/components/dwarf-adversary && cabal build all 2>&1 | tail -20'
```
Expected: builds clean. (No unit test here — the mutation logic is already tested in `FuzzSpec`; the codec seam is exercised end-to-end in Task 8's integration test.)

- [ ] **Step 4: Commit**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add antithesis/components/dwarf-adversary/src/DwarfAdversary/ChainSync/MutatingCodec.hs \
        antithesis/components/dwarf-adversary/src/DwarfAdversary/ChainSync/Codec.hs
git commit -m "feat(dwarf-adversary): mutating chain-sync codec (header encode path)

Wrap encHeader: serialized header -> decodeTerm -> mutateTerm(seed) ->
encodeTerm. Decode side untouched. Per-header sub-seed via split.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `HeaderSource.hs` — a decodable base header from inside the environment

**Hermetic:** the base header must come from inside the sealed compose. Two modes: (a) **capture** — at startup, briefly chain-sync as a client from an in-bundle node (reuse the retained initiator path) to grab ≥1 real header; (b) **baked fixture** — a header serialized to a file embedded in the image. Implement capture with fixture fallback.

**Files:**
- Create: `dwarf-v4/antithesis/components/dwarf-adversary/src/DwarfAdversary/HeaderSource.hs`
- Modify: `app/Main.hs` (call it in the serve path)
- Keep: the forked initiator code (`Application.hs` + the original initiator `runChainSyncApplication`) — used only for capture now. Rename the responder one in Task 3 to avoid a clash (it is `runChainSyncServer`).

- [ ] **Step 1: Write `HeaderSource.hs`**

```haskell
{-# LANGUAGE OverloadedStrings #-}

module DwarfAdversary.HeaderSource
    ( getBaseHeaders
    ) where

import DwarfAdversary.ChainSync.Codec (Header)
import Codec.Serialise (deserialiseOrFail, serialise)
import Control.Exception (try, SomeException)
import Data.ByteString.Lazy qualified as LBS
import System.Directory (doesFileExist)

-- | Obtain at least one decodable base 'Header' to mutate, from inside
-- the sealed environment only.
--
-- Mode (a) capture: if @mUpstream@ is given (host of an in-bundle
-- node), chain-sync briefly to grab N real headers, cache them to
-- @fixturePath@, and return them.
-- Mode (b) fixture: otherwise (or if capture fails / no network),
-- read the baked-in serialized headers at @fixturePath@.
--
-- NEVER reaches an external/public node: @mUpstream@ is only ever an
-- in-compose hostname passed via --upstream.
getBaseHeaders
    :: Maybe (String, Int)   -- ^ optional in-bundle (host, port) to capture from
    -> FilePath              -- ^ baked fixture path (also the capture cache)
    -> IO [Header]
getBaseHeaders mUpstream fixturePath = do
    captured <- case mUpstream of
        Nothing -> pure []
        Just _hp -> do
            r <- try @SomeException (captureHeaders mUpstream)
            either (const (pure [])) pure r
    if not (null captured)
        then do
            LBS.writeFile fixturePath (serialise captured)
            pure captured
        else do
            exists <- doesFileExist fixturePath
            if exists
                then do
                    raw <- LBS.readFile fixturePath
                    case deserialiseOrFail raw of
                        Right hs | not (null hs) -> pure hs
                        _ -> error "HeaderSource: fixture present but undecodable"
                else error "HeaderSource: no upstream capture and no baked fixture"

-- | Reuse the retained initiator path to sync a few headers. Returns
-- the headers accumulated in the client's mock chain.
captureHeaders :: Maybe (String, Int) -> IO [Header]
captureHeaders = error "implement via retained initiator runChainSyncApplication (Application.hs)"
```

> **Implementer note:** implement `captureHeaders` by calling the retained initiator (`DwarfAdversary.Application.adversaryApplication` / the mock `Chain Header`) against the in-bundle node with a small `Limit` (e.g. 5), then return `Chain.toOldestFirst` of the resulting chain. `Header` already has `Serialise` instances via the consensus stack used in `Codec.hs`; if `Serialise [Header]` is not directly available, serialize each header with the codec's `encHeader` and store length-prefixed (use the same enc/dec exported in Task 4). Bake a fixture into the image at build time (Task 7) so fixture-mode always works even with no `--upstream`.

- [ ] **Step 2: Build to verify it compiles**

```bash
rsync -az /Users/nigel/dwarf-project/dwarf-v4/antithesis/components/dwarf-adversary/ \
  cardano-box:/home/nigel/dwarf-v4/antithesis/components/dwarf-adversary/
ssh cardano-box 'cd /home/nigel/dwarf-v4/antithesis/components/dwarf-adversary && cabal build all 2>&1 | tail -20'
```
Expected: builds clean.

- [ ] **Step 3: Commit**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add antithesis/components/dwarf-adversary/src/DwarfAdversary/HeaderSource.hs
git commit -m "feat(dwarf-adversary): in-environment base-header source (capture + fixture)

Capture >=1 real header from an in-bundle node at startup (reusing the
initiator path), cache to a fixture, fall back to a baked fixture.
Never reaches an external node — hermetic.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `Main.hs` — full serve/fuzz CLI + SDK assertions

**Files:**
- Modify: `dwarf-v4/antithesis/components/dwarf-adversary/app/Main.hs`
- Reuse: `src/DwarfAdversary/SDK.hs` (copied verbatim from CF; `reachable`/`sometimes`, **no `always`**)

- [ ] **Step 1: Write the full `Main.hs`**

Replace the spike `Main.hs` with the production CLI: parse `--serve`, `--fuzz`, `--listen-port` (default 3001), `--mutation-rate` (default 0.5), `--upstream HOST:PORT` (optional, in-bundle only), `--network-magic` (default 42), `--seed` (HEX-or-DEC, reuse CF's `parseSeed`). Wire: capture base headers → build server peer with the mutating (or plain, if `--mutation-rate 0`) codec → run the server daemon. Emit assertions.

```haskell
{-# LANGUAGE NumericUnderscores #-}
{-# LANGUAGE OverloadedStrings #-}
module Main (main) where

import Data.Aeson (object, (.=))
import Data.Text qualified as T
import DwarfAdversary.ChainSync.Codec (codecChainSync)
import DwarfAdversary.ChainSync.Connection (runChainSyncServer)
import DwarfAdversary.ChainSync.MutatingCodec (mutatingCodecChainSync, newMutationCounter)
import DwarfAdversary.ChainSync.Server (chainSyncServer)
import DwarfAdversary.HeaderSource (getBaseHeaders)
import DwarfAdversary.SDK qualified as SDK
import Ouroboros.Network.Magic (NetworkMagic (..))
import Ouroboros.Network.Protocol.ChainSync.Server (chainSyncServerPeer)
import System.Random (mkStdGen)
-- optparse-applicative imports as in CF Main.hs

main :: IO ()
main = do
    args <- parseArgs   -- Args { magic, port, rate, mUpstream, seed }
    SDK.reachable "dwarf_fuzz_server_started"
        (object [ "port" .= argPort args, "seed" .= argSeed args
                , "mutation_rate" .= argRate args ])
    headers <- getBaseHeaders (argUpstream args) "/opt/dwarf/base-header.cbor"
    SDK.sometimes (not (null headers)) "dwarf_base_header_obtained"
        (object [ "count" .= length headers ])
    let gen = mkStdGen (fromIntegral (argSeed args))
        tip = tipFromHeaders headers
    counter <- newMutationCounter
    let codec = if argRate args <= 0
                    then codecChainSync
                    else mutatingCodecChainSync gen (argRate args) counter
        peer  = chainSyncServerPeer (chainSyncServer headers tip)
    SDK.reachable "dwarf_fuzz_server_listening" (object [ "port" .= argPort args ])
    _ <- runChainSyncServer (NetworkMagic (argMagic args)) (argPort args) codec peer
    pure ()
```

> **Implementer note:** add `SDK.sometimes True "dwarf_served_mutated_header" (object [...])` on each served header inside the server callback (thread the `MutationInfo` + counter out of the mutating codec — e.g. have the codec write the latest `MutationInfo` into an `IORef` the server reads after each `recvMsgRequestNext`). Add `SDK.reachable "dwarf_node_connected"` when the first inbound connection is accepted (hook in `Connection.hs`). `tipFromHeaders` builds a `Tip` from the last header's point + block no. **No `always` assertions** — the harness can be chaos-killed. Wrap `main` body in `try` like CF's `Main.hs` and emit `sometimes False "dwarf_fuzz_server_completed"` on transient failure, exit 0.

- [ ] **Step 2: Build + run unmutated and mutated smoke locally**

```bash
rsync -az /Users/nigel/dwarf-project/dwarf-v4/antithesis/components/dwarf-adversary/ \
  cardano-box:/home/nigel/dwarf-v4/antithesis/components/dwarf-adversary/
ssh cardano-box 'cd /home/nigel/dwarf-v4/antithesis/components/dwarf-adversary && cabal build all 2>&1 | tail -10 && \
  ANTITHESIS_OUTPUT_DIR=/tmp/dwarftest cabal run dwarf-adversary -- --serve --fuzz --network-magic 42 --listen-port 3001 --mutation-rate 1.0 --seed 0x1 & \
  sleep 5; cat /tmp/dwarftest/sdk.jsonl 2>/dev/null | head'
```
Expected: `sdk.jsonl` contains `dwarf_fuzz_server_started` (Reachable) and `dwarf_base_header_obtained` (Sometimes). (No node connected in this smoke; connection-driven assertions appear in Task 8.)

- [ ] **Step 3: Commit**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add antithesis/components/dwarf-adversary/app/Main.hs
git commit -m "feat(dwarf-adversary): full serve/fuzz CLI + SDK assertions

--serve/--fuzz/--listen-port/--mutation-rate/--upstream/--seed. Seed is
sole RNG. Sometimes/Reachable for served-header + connection coverage;
no Always (attacker may be chaos-killed).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Composer scripts + Dockerfile → ghcr image

**Files:**
- Create: `dwarf-v4/antithesis/components/dwarf-adversary/composer/cbor-fuzz/finally_fuzz_summary.sh`
- Modify: `dwarf-v4/antithesis/components/dwarf-adversary/Dockerfile`
- Delete: `composer/chain-sync-client/` (CF's per-tick driver — we are a daemon, not a per-tick exec)

- [ ] **Step 1: Remove CF's per-tick driver, add the daemon-model finally script**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4/antithesis/components/dwarf-adversary
git rm -r composer/chain-sync-client
mkdir -p composer/cbor-fuzz
```

`composer/cbor-fuzz/finally_fuzz_summary.sh` (mirror CF's `finally_adversary_summary.sh` exactly — no `set -e`, no sleep, always exit 0):

```bash
#!/usr/bin/env bash
#
# finally_fuzz_summary.sh — one end-of-run coverage marker proving the
# dwarf-adversary fuzz daemon stayed alive to end-of-test. If
# "dwarf_fuzz_run_completed" is passed in the report, the fuzz server
# survived; if absent, it was killed early and other dwarf_* Sometimes
# rows are suspect. No set -e, no sleep, always exit 0.

OUT="${ANTITHESIS_OUTPUT_DIR:-/tmp}/sdk.jsonl"
mkdir -p "$(dirname "$OUT")" 2>/dev/null

jq -nc '{
  antithesis_assert: {
    id:"dwarf_fuzz_run_completed", message:"dwarf_fuzz_run_completed",
    condition:true, display_type:"Sometimes", hit:true, must_hit:true,
    assert_type:"sometimes",
    location:{file:"",function:"",class:"",begin_line:0,begin_column:0},
    details:null }
}' >> "$OUT" 2>/dev/null || true

exit 0
```

- [ ] **Step 2: Update the Dockerfile — exe name, fixture, composer path**

In the forked `Dockerfile`, change the build-stage `cp` to the new exe path/name, copy a baked base-header fixture, and keep the composer copy. The build-stage cp line becomes:

```dockerfile
    cp -p dist-newstyle/build/$(uname -m)-linux/ghc-${GHC_VERSION}/dwarf-adversary-0.1.0.0/x/dwarf-adversary/build/dwarf-adversary/dwarf-adversary /usr/local/bin/
```
and in the `main` stage replace the adversary copy + add the fixture dir:
```dockerfile
COPY --from=build --chown=root:root /usr/local/bin/dwarf-adversary /usr/local/bin/dwarf-adversary
RUN mkdir -p /opt/dwarf
# Optional baked fixture (committed under fixtures/ if capture-mode is not used):
COPY fixtures/base-header.cbor /opt/dwarf/base-header.cbor
COPY composer /opt/antithesis/test/v1/
RUN chmod 0755 /opt/antithesis/test/v1/*/*
COPY sleep.sh .
RUN chmod 0755 ./sleep.sh
ENTRYPOINT ["./sleep.sh"]
```

> **Implementer note:** if Task 5 capture-mode is reliable against an in-bundle node, the baked fixture is a safety net; generate it once (run `dwarf-adversary` capture locally against the devnet, copy `/opt/dwarf/base-header.cbor` out, commit to `fixtures/base-header.cbor`). If you keep the daemon serving — note `sleep.sh` is CF's entrypoint shim; the actual fuzz daemon is launched by the composer or by overriding the compose `command:` to run `dwarf-adversary --serve --fuzz ...`. Decide in Task 8 whether the daemon runs as the container `command` (preferred for a long-lived server) rather than via `docker exec`; update `ENTRYPOINT`/compose `command` accordingly.

- [ ] **Step 3: Build the image locally on cardano-box (no push yet)**

```bash
rsync -az /Users/nigel/dwarf-project/dwarf-v4/antithesis/components/dwarf-adversary/ \
  cardano-box:/home/nigel/dwarf-v4/antithesis/components/dwarf-adversary/
ssh cardano-box 'cd /home/nigel/dwarf-v4/antithesis/components/dwarf-adversary && \
  docker build -t ghcr.io/cyber-castellum/dwarf-adversary:dev . 2>&1 | tail -20'
```
Expected: image builds; `docker run --rm ghcr.io/cyber-castellum/dwarf-adversary:dev ls /opt/antithesis/test/v1/cbor-fuzz` lists `finally_fuzz_summary.sh`.

- [ ] **Step 4: Commit**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add antithesis/components/dwarf-adversary/composer antithesis/components/dwarf-adversary/Dockerfile
git rm -r --cached antithesis/components/dwarf-adversary/composer/chain-sync-client 2>/dev/null || true
git commit -m "feat(dwarf-adversary): daemon composer script + ghcr Dockerfile

Drop CF per-tick driver; add finally_fuzz_summary.sh (daemon model).
Dockerfile builds dwarf-adversary, bakes base-header fixture, targets
ghcr.io/cyber-castellum/dwarf-adversary. Builds on cardano-box.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Wire `dwarf-adversary` into the `cardano_node_dwarf` testnet + local integration

**Files:**
- Create: `dwarf-v4/antithesis/cardano_node_dwarf/relay-dwarf-topology.json`
- Modify: `dwarf-v4/antithesis/cardano_node_dwarf/docker-compose.yaml`

- [ ] **Step 1: Add relay2's adversary-pointing topology**

`relay-dwarf-topology.json` — relay2 keeps the honest producers AND adds `dwarf-adversary.example:3001` as a trustable localRoot, so relay2's chain-sync client opens a session to the fuzzer and decodes its served headers:

```json
{
  "localRoots": [
    {
      "accessPoints": [
        {"address": "p1.example", "port": 3001},
        {"address": "p2.example", "port": 3001},
        {"address": "p3.example", "port": 3001},
        {"address": "dwarf-adversary.example", "port": 3001}
      ],
      "advertise": false,
      "trustable": true,
      "valency": 4
    }
  ],
  "publicRoots": [],
  "useLedgerAfterSlot": 0
}
```

- [ ] **Step 2: Add the `dwarf-adversary` service + point relay2 at the new topology**

In `docker-compose.yaml`, change relay2's topology mount:
```yaml
  relay2:
    <<: *cardano-relay
    image: ghcr.io/intersectmbo/cardano-node@sha256:45857be8d86b314a05cd46d310b74b24e5f5870469f90c6464994a7f78142271
    container_name: relay2
    hostname: relay2.example
    volumes:
      - p1-configs:/configs:ro
      - ./relay-dwarf-topology.json:/configs/configs/topology.json:ro
      - relay2-state:/state
      - tracer:/tracer
```
and add the service (place near the other workloads; the fuzz daemon runs as the container `command`, public image, fault-exclusion label):
```yaml
  # Dwarf CBOR-fuzz adversary — a chain-sync UPSTREAM SERVER. relay2's
  # topology lists it as a trustable localRoot, so relay2 syncs from
  # it and runs its header decoder on structurally-mutated header CBOR.
  # Seeded solely by --seed (sourced from antithesis_random at launch)
  # for deterministic recreation. Excluded from faults so the harness
  # itself is never the thing being chaos-tested.
  dwarf-adversary:
    image: ghcr.io/cyber-castellum/dwarf-adversary:0.1.0
    container_name: dwarf-adversary
    hostname: dwarf-adversary.example
    labels:
      com.antithesis.exclude_from_faults: "network,kill,pause,stop"
    command:
      - "--serve"
      - "--fuzz"
      - "--network-magic"
      - "42"
      - "--listen-port"
      - "3001"
      - "--mutation-rate"
      - "0.5"
      - "--upstream"
      - "p1.example:3001"
      - "--seed"
      - "0x1"
    depends_on:
      configurator:
        condition: service_completed_successfully
      p1:
        condition: service_started
    restart: always
```

> **Implementer note:** the literal `--seed 0x1` here is a placeholder for local validation only. In a real Antithesis run the seed is supplied from `antithesis_random` — either by a tiny composer wrapper that reads `antithesis_random` and restarts the daemon with it, or by leaving the daemon seeded once at boot (Antithesis varies the boot via its own scheduling). Confirm with CF which they prefer for a daemon workload; default to a fixed boot seed (simplest deterministic recreation) and document it. Also confirm `cardano-adversary`'s entrypoint vs `command` — since `ENTRYPOINT` is `./sleep.sh`, override `entrypoint: ["dwarf-adversary"]` here or change the Dockerfile `ENTRYPOINT` to `dwarf-adversary` for the server model.

- [ ] **Step 3: Local integration — node syncs from the *fuzzing* server, decoder runs on mutated headers**

```bash
rsync -az /Users/nigel/dwarf-project/dwarf-v4/antithesis/cardano_node_dwarf/ \
  cardano-box:/home/nigel/dwarf-v4/antithesis/cardano_node_dwarf/
ssh cardano-box 'cd /home/nigel/dwarf-v4/antithesis/cardano_node_dwarf && \
  docker compose up -d && sleep 120 && \
  echo "=== relay2 chain-sync against dwarf-adversary ===" && \
  docker compose logs relay2 2>&1 | grep -iE "dwarf-adversary|ChainSync|RollForward|DecoderError|Decoder" | tail -30 && \
  echo "=== adversary assertions ===" && \
  docker compose exec -T dwarf-adversary cat /tmp/sdk.jsonl 2>/dev/null | tail -20 && \
  echo "=== adversary still up? ===" && docker compose ps dwarf-adversary'
```
**Gate (must pass):** relay2 logs show it **connected to `dwarf-adversary.example` and decoded served headers** (RollForward/DecoderError traces referencing our peer), the adversary `sdk.jsonl` shows `dwarf_served_mutated_header` Sometimes hits and `dwarf_node_connected` Reachable, and `dwarf-adversary` is still `Up`. A node decoder panic (crash/exception) is a *finding*, not a failure of this gate — capture the log if it happens. Tear down: `docker compose down -v`.

- [ ] **Step 4: Commit**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add antithesis/cardano_node_dwarf/relay-dwarf-topology.json antithesis/cardano_node_dwarf/docker-compose.yaml
git commit -m "feat(antithesis): wire dwarf-adversary into cardano_node_dwarf testnet

relay2 topology lists dwarf-adversary.example:3001 as a trustable
localRoot; add the fuzz-server service (public image, fault-exclusion
label, fuzz daemon command). Verified relay2 syncs from it and decodes
mutated headers; harness stays up.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Publish the public image (needs the ghcr `write:packages` token)

**This is the step that requires the user's ghcr token.** Pause and request it here.

**Files:** none (registry operation).

- [ ] **Step 1: Request the token from the user and log in (token via stdin, never stored/printed)**

> Tell the user: "Ready to publish `dwarf-adversary` to ghcr. I need the `write:packages` token now. Paste it and I'll `docker login ghcr.io` with it on cardano-box — it's piped via stdin, never written to a file or the workbench."

```bash
# user provides the token interactively; pipe via stdin:
ssh cardano-box 'read -rs TOK; echo "$TOK" | docker login ghcr.io -u <ghcr-user> --password-stdin'
```

- [ ] **Step 2: Tag + push the image, set it public**

```bash
ssh cardano-box 'docker tag ghcr.io/cyber-castellum/dwarf-adversary:dev ghcr.io/cyber-castellum/dwarf-adversary:0.1.0 && \
  docker push ghcr.io/cyber-castellum/dwarf-adversary:0.1.0'
```
Then set the package **Public** in the ghcr UI (org → Packages → dwarf-adversary → Package settings → Change visibility → Public). CF requires public images.

- [ ] **Step 3: Verify public pull works without auth**

```bash
ssh cardano-box 'docker logout ghcr.io && docker pull ghcr.io/cyber-castellum/dwarf-adversary:0.1.0 2>&1 | tail -3'
```
Expected: pulls successfully while logged out (confirms public). No commit (registry-only step).

---

## Task 10: Push to `Cyber-Castellum/DWARF`, finish the branch, launch via Moog

**Files:** none new (uses the committed testnet + component).

- [ ] **Step 1: Finish the development branch**

Run the tests once more, then use the finishing-a-development-branch skill to merge `phase3b-cbor-fuzz` → `main`.
```bash
ssh cardano-box 'cd /home/nigel/dwarf-v4/antithesis/components/dwarf-adversary && cabal test 2>&1 | tail -10'
```
Expected: FuzzSpec passes. Then merge per the skill (Option 1, local merge).

- [ ] **Step 2: User pushes `main` (+ testnet/component) to `Cyber-Castellum/DWARF`**

The agent cannot push to `git.gainpalfam.com`/GitHub (no creds). Ask the user to push, and confirm the new commit SHA on `Cyber-Castellum/DWARF` that contains `antithesis/components/dwarf-adversary/` + the updated `antithesis/cardano_node_dwarf/`.

- [ ] **Step 3: Dry-run, then guarded live `moog create-test`**

Use the existing Dwarf tooling (deployed in Phase 3a) against the new commit:
```bash
ssh cardano-box 'cd /home/nigel/dwarf-v4 && \
  PYTHONPATH=dwarf python3 -m profile_manager.cli moog create-test \
    --directory antithesis/cardano_node_dwarf --commit <new-sha> --duration 1 --dry-run'
```
Review the rendered `moog requester create-test` command, then re-run with `--approve` (drops `--no-faults` for the real fault run). CF's agent launches it on the `amaru-cardano` Antithesis tenant.

- [ ] **Step 4: Read the result back**

Watch the dashboard (`https://amaru-cardano.antithesis.com/home`) and `moog test-status`. **Phase 3b acceptance:** the report shows Dwarf's own perturbation assertions (`dwarf_served_mutated_header`, `dwarf_base_header_obtained`, `dwarf_fuzz_run_completed`) as Sometimes rows, and any node decoder panic is captured + recreatable from the seed. Record testRunId + outcome on the workbench Live-Run Checklist.

---

## Self-Review

**1. Spec coverage:**
- "Reach the decoder / mutate payload not envelope" → Tasks 3 (server reaches node's client+decoder) + 4 (mutate header payload via codec, not mux). ✓
- "Structured Term mutation" → Task 2 (`Fuzz.hs`). ✓
- "Determinism, seed sole RNG, no urandom" → Task 2 (pure `mutateTerm`), Task 6 (`mkStdGen` from `--seed`, no urandom path), FuzzSpec determinism test. ✓
- "Sometimes/Reachable, no Always" → Task 6 + Task 7 (SDK reused; explicitly no `always`). ✓
- "Public ghcr image, fault-exclusion label, topology-wire a relay" → Tasks 7, 9 (image), 8 (label + relay2 topology). ✓
- "Haskell/cardano-node only, Amaru deferred" → no Amaru anywhere. ✓
- "Header source in-environment only" → Task 5 (`getBaseHeaders`, capture-from-in-bundle or fixture; never external). ✓
- "Spike first" → Task 3 is the spike, gated before Tasks 4+. ✓
- "Image public before launch" → Task 9 before Task 10. ✓
- "Validate it bites (node actually decodes)" → Task 3 gate + Task 8 gate (tracer evidence required). ✓

**2. Placeholder scan:** The two `error "..."`/`error "spike: ..."` stubs in Tasks 3 and 5 are **intentional spike-resolution points** with detailed implementer notes giving the concrete code to substitute — they are not silent TODOs. The `--seed 0x1` and `swapMajor (TInt n)` placeholder are explicitly called out with their replacements. These are honest about the one genuinely exploratory area (Ouroboros server wiring) that cannot be pinned offline; every other step has complete code.

**3. Type consistency:** `mutateTerm :: StdGen -> Double -> Term -> (Term, MutationInfo)` is used identically in FuzzSpec (Task 2), MutatingCodec (Task 4), and referenced in Main (Task 6). `MutationInfo{miKind,miDepth}` consistent. `getBaseHeaders :: Maybe (String,Int) -> FilePath -> IO [Header]` (Task 5) matches its Main call (Task 6). `runChainSyncServer` signature (Task 3) matches its Main call (Task 6). `chainSyncServer :: [Header] -> Tip -> ChainSyncServer ...` (Task 3) matches Main. Codec helper exports (Task 4 Step 2) match MutatingCodec's needs. ✓

**Known risk acknowledged:** Task 3 may reveal the installed `ouroboros-network` makes server-role hosting impractical without the full diffusion layer. The plan gates on this explicitly (STOP-and-report) rather than hiding it.
