{-# LANGUAGE OverloadedStrings #-}

-- |
-- Module: DwarfAdversary.Fuzz
--
-- Pure, seeded structural mutation of CBOR 'Term's. This is the
-- entire source of fuzz nondeterminism in dwarf-adversary: a single
-- 'StdGen' fully determines the mutation chosen and where it is
-- applied, so any finding Antithesis surfaces is reproducible from the
-- seed alone (no @\/dev\/urandom@, no clock, no system entropy).
--
-- The mutator walks a decodable base 'Term' (a real Cardano header,
-- decoded via 'Codec.CBOR.Term.decodeTerm') and applies one structural
-- perturbation at a seed-chosen position. Operating at the 'Term'
-- level (not raw bytes) keeps the output a well-formed CBOR item whose
-- /structure/ is hostile — wrong lengths, swapped major types,
-- truncated\/extended collections, nesting abuse — so the node's
-- header decoder engages deeply instead of rejecting trivial garbage
-- at the framing layer.
module DwarfAdversary.Fuzz
    ( MutationInfo (..)
    , mutateTerm
    , mutationKinds
    , MutationLevel (..)
    , parseMutationLevel
    , corruptBytes
    , byteMutationKinds
    , mutateTermSemantic
    ) where

import Codec.CBOR.Term (Term (..))
import Data.Bits (xor)
import Data.ByteString qualified as BS
import Data.ByteString.Lazy qualified as LBS
import Data.Text (Text)
import Data.Text qualified as T
import Data.Text.Encoding qualified as TE
import Data.Text.Lazy qualified as LT
import Data.Word (Word8)
import System.Random (StdGen, randomR, randomRs)

-- | What the mutator did, for SDK assertion details.
data MutationInfo = MutationInfo
    { miKind :: Text
    -- ^ e.g. @"swapMajorType"@, @"truncateCollection"@, @"none"@
    , miDepth :: Int
    -- ^ structural depth at which the mutation was applied
    }
    deriving (Eq, Show)

-- | The structural mutation kinds, by name. Stable order, so a given
-- seed maps to the same kind across runs (determinism).
mutationKinds :: [Text]
mutationKinds =
    [ "swapMajorType"
    , "truncateCollection"
    , "extendCollection"
    , "perturbInt"
    , "flipIndefinite"
    , "nestOnce"
    ]

-- | @mutateTerm gen rate term@ applies at most one structural
-- mutation. @rate@ in [0,1] is the probability the mutation fires;
-- 0.0 is the identity (used by the stock-server spike). Returns the
-- mutated 'Term' and a 'MutationInfo' describing what changed.
--
-- Determinism: every random choice comes from @gen@; calling with the
-- same @gen@ and @rate@ yields the same result.
mutateTerm :: StdGen -> Double -> Term -> (Term, MutationInfo)
mutateTerm gen rate term =
    let (roll, g1) = randomR (0.0, 1.0) gen
    in  if roll >= rate
            then (term, MutationInfo "none" 0)
            else
                let (kIx, g2) = randomR (0, length mutationKinds - 1) g1
                    kind = mutationKinds !! kIx
                    (mutated, info) = applyKind kind g2 0 term
                in  -- Guarantee non-identity when the mutation fires:
                    -- some kind/position pairs are no-ops for a given
                    -- shape (e.g. perturbInt on a list). Fall back to a
                    -- definite structural change so a fired mutation
                    -- always perturbs the bytes.
                    if mutated == term
                        then (forceChange term, MutationInfo (kind <> "+forced") 0)
                        else (mutated, info)

-- | Apply the named mutation, optionally descending into a seed-chosen
-- child so the perturbation can land anywhere in the structure.
applyKind :: Text -> StdGen -> Int -> Term -> (Term, MutationInfo)
applyKind kind gen depth t =
    let (descend, g1) = randomR (0.0, 1.0 :: Double) gen
    in  case (descend < 0.5, children t) of
            (True, cs@(_ : _)) ->
                let (ix, g2) = randomR (0, length cs - 1) g1
                    (child', info) = applyKind kind g2 (depth + 1) (cs !! ix)
                in  (replaceChild ix child' t, info)
            _ -> (mutateHere kind g1 t, MutationInfo kind depth)

-- | The direct CBOR children of a 'Term' (for descent).
children :: Term -> [Term]
children (TList xs) = xs
children (TListI xs) = xs
children (TMap kvs) = concatMap (\(k, v) -> [k, v]) kvs
children (TMapI kvs) = concatMap (\(k, v) -> [k, v]) kvs
children (TTagged _ x) = [x]
children _ = []

-- | Put a mutated child back at flattened index @ix@ (inverse of
-- 'children').
replaceChild :: Int -> Term -> Term -> Term
replaceChild ix c (TList xs) = TList (setAt ix c xs)
replaceChild ix c (TListI xs) = TListI (setAt ix c xs)
replaceChild ix c (TMap kvs) = TMap (setKv ix c kvs)
replaceChild ix c (TMapI kvs) = TMapI (setKv ix c kvs)
replaceChild _ c (TTagged tag _) = TTagged tag c
replaceChild _ _ t = t

setAt :: Int -> a -> [a] -> [a]
setAt i x xs = [if j == i then x else y | (j, y) <- zip [0 ..] xs]

-- | Map a flattened child index back onto a @[(k,v)]@ list: even
-- indices address keys, odd indices address values.
setKv :: Int -> Term -> [(Term, Term)] -> [(Term, Term)]
setKv flatIx c kvs =
    [ ( if flatIx == 2 * j then c else k
      , if flatIx == 2 * j + 1 then c else v
      )
    | (j, (k, v)) <- zip [0 ..] kvs
    ]

-- | Apply the structural mutation to /this/ term node.
mutateHere :: Text -> StdGen -> Term -> Term
mutateHere "swapMajorType" _ t = swapMajor t
mutateHere "truncateCollection" _ t = truncateColl t
mutateHere "extendCollection" g t = extendColl g t
mutateHere "perturbInt" g t = perturbInt g t
mutateHere "flipIndefinite" _ t = flipIndef t
mutateHere "nestOnce" _ t = TList [t]
mutateHere _ _ t = t

-- | Reinterpret a value under a different major type — structurally
-- legal CBOR, semantically wrong for the decoder.
swapMajor :: Term -> Term
swapMajor (TInt n) = TBytes (BS.replicate (max 1 (n `mod` 8)) 0x41)
swapMajor (TInteger n) = TBytes (BS.replicate (max 1 (fromIntegral (n `mod` 8))) 0x41)
swapMajor (TBytes b) = TString (TE.decodeLatin1 b)
swapMajor (TString s) = TInt (T.length s)
swapMajor (TList xs) = TMap (pairUp xs)
swapMajor (TMap kvs) = TList (concatMap (\(k, v) -> [k, v]) kvs)
swapMajor t = t

-- | Drop the last element of a collection so a definite-length header
-- over-counts the elements that follow.
truncateColl :: Term -> Term
truncateColl (TList xs) = TList (dropLast xs)
truncateColl (TListI xs) = TListI (dropLast xs)
truncateColl (TMap kvs) = TMap (dropLast kvs)
truncateColl (TMapI kvs) = TMapI (dropLast kvs)
truncateColl (TBytes b) = TBytes (if BS.null b then b else BS.init b)
truncateColl (TString s) = TString (dropEndT s)
truncateColl t = t

-- | Append a junk element so the collection over-runs expectations.
extendColl :: StdGen -> Term -> Term
extendColl g (TList xs) = TList (xs ++ [junk g])
extendColl g (TListI xs) = TListI (xs ++ [junk g])
extendColl g (TMap kvs) = TMap (kvs ++ [(junk g, junk g)])
extendColl g (TMapI kvs) = TMapI (kvs ++ [(junk g, junk g)])
extendColl _ t = t

-- | Perturb an integer by a large seed-derived delta.
perturbInt :: StdGen -> Term -> Term
perturbInt g (TInt n) =
    let (d, _) = randomR (minBound, maxBound :: Int) g
    in  TInteger (fromIntegral n + fromIntegral d)
perturbInt g (TInteger n) =
    let (d, _) = randomR (minBound, maxBound :: Int) g
    in  TInteger (n + fromIntegral d)
perturbInt _ t = t

-- | Flip a definite-length container to its indefinite-length form
-- (and vice-versa) — exercises the streaming-decode path.
flipIndef :: Term -> Term
flipIndef (TList xs) = TListI xs
flipIndef (TListI xs) = TList xs
flipIndef (TMap kvs) = TMapI kvs
flipIndef (TMapI kvs) = TMap kvs
flipIndef (TBytes b) = TBytesI (LBS.fromStrict b)
flipIndef (TString s) = TStringI (LT.fromStrict s)
flipIndef t = t

-- | A definite structural change for any term, used only as a fallback
-- when a fired mutation happened to be a no-op for the given shape.
forceChange :: Term -> Term
forceChange = TList . (: [])

junk :: StdGen -> Term
junk g =
    let (n, _) = randomR (0, 3 :: Int) g
    in  [TInt 0xdead, TBytes "\xff\xff", TString "junk", TList []] !! n

pairUp :: [Term] -> [(Term, Term)]
pairUp (a : b : rest) = (a, b) : pairUp rest
pairUp [a] = [(a, TNull)]
pairUp [] = []

dropLast :: [a] -> [a]
dropLast [] = []
dropLast xs = init xs

dropEndT :: Text -> Text
dropEndT s = if T.null s then s else T.dropEnd 1 s

-- ---------------------------------------------------------------------------
-- Byte-level (invalid / malformed) mutation
--
-- The 'mutateTerm' path above keeps output WELL-FORMED CBOR (hostile
-- structure, valid wire encoding). This complementary path corrupts the
-- SERIALIZED bytes directly, producing MALFORMED CBOR — truncated items,
-- flipped bytes, oversized length prefixes, pathological nesting, raw
-- garbage — to exercise the node's deserializer error handling and
-- resource bounds (the classic "throw invalid bytes at the parser"
-- security surface: parser panics, OOM on oversized lengths, stack
-- overflow on deep nesting). Still a pure function of (seed, bytes).
-- ---------------------------------------------------------------------------

-- | Which layer the adversary mutates at.
data MutationLevel
    = LevelStruct
    -- ^ structural mutation of the CBOR Term, re-encoded to valid CBOR (default)
    | LevelBytes
    -- ^ corrupt the serialized bytes into MALFORMED CBOR
    | LevelBoth
    -- ^ structural mutation, then byte-corrupt the re-encoded result
    | LevelSemantic
    -- ^ value-only perturbation: preserve every CBOR type + length so the payload
    -- still DECODES, but corrupt a leaf value (flip a byte in a hash/signature,
    -- change an int) so it is SEMANTICALLY invalid -> exercises the node's
    -- VALIDATION layer (InvalidBlock), not just the decoder.
    deriving (Eq, Show)

parseMutationLevel :: String -> Maybe MutationLevel
parseMutationLevel s = case s of
    "struct" -> Just LevelStruct
    "bytes" -> Just LevelBytes
    "both" -> Just LevelBoth
    "semantic" -> Just LevelSemantic
    _ -> Nothing

-- | The byte-corruption kinds, by name (stable order for determinism).
byteMutationKinds :: [Text]
byteMutationKinds =
    [ "byteTruncate" -- cut the item short -> incomplete CBOR
    , "byteFlip" -- flip one byte -> wrong major type / length
    , "oversizeLen" -- prepend array(2^64-1) header -> decoder expects astronomically many items
    , "deepNest" -- prepend many indefinite-array opens -> deep nesting
    , "garbageHead" -- prepend reserved/break bytes
    , "randomGarbage" -- replace with random bytes
    ]

-- | Corrupt serialized bytes into malformed CBOR. @rate@ ∈ [0,1] is the
-- probability a corruption fires (else identity). Pure in @gen@.
corruptBytes :: StdGen -> Double -> BS.ByteString -> (BS.ByteString, MutationInfo)
corruptBytes gen rate bs =
    let (roll, g1) = randomR (0.0, 1.0) gen
    in  if roll >= rate
            then (bs, MutationInfo "none" 0)
            else
                let (kIx, g2) = randomR (0, length byteMutationKinds - 1) g1
                    kind = byteMutationKinds !! kIx
                in  (applyByte kind g2 bs, MutationInfo ("bytes:" <> kind) 0)

applyByte :: Text -> StdGen -> BS.ByteString -> BS.ByteString
applyByte "byteTruncate" g bs
    | BS.length bs <= 1 = BS.empty
    | otherwise = let (k, _) = randomR (1, BS.length bs - 1) g in BS.take k bs
applyByte "byteFlip" g bs
    | BS.null bs = BS.singleton 0xff
    | otherwise =
        let (i, _) = randomR (0, BS.length bs - 1) g
            (a, b) = BS.splitAt i bs
        in  case BS.uncons b of
                Just (h, t) -> a <> BS.cons (h `xor` 0xff) t
                Nothing -> bs
applyByte "oversizeLen" _ bs = BS.pack (0x9b : replicate 8 0xff) <> bs
applyByte "deepNest" g bs =
    let (n, _) = randomR (16, 512 :: Int) g in BS.replicate n 0x9f <> bs
applyByte "garbageHead" _ bs = BS.pack [0xff, 0xff, 0x1f] <> bs
applyByte "randomGarbage" g bs =
    let (n, g') = randomR (1, 48 :: Int) g
    in  BS.pack (take n (randomRs (0, 255 :: Word8) g'))
applyByte _ _ bs = bs

-- ---------------------------------------------------------------------------
-- Semantic (decodable-but-invalid) mutation
--
-- Unlike 'mutateTerm' (breaks structure -> rejected at the DECODER) and
-- 'corruptBytes' (malformed bytes -> rejected at the DECODER), this perturbs a
-- single LEAF VALUE while preserving every CBOR type and length, so the payload
-- still DECODES into a well-formed block/tx — but a flipped hash byte / changed
-- signature / tweaked int makes it SEMANTICALLY invalid, so the node must reject
-- it at the VALIDATION layer (InvalidBlock / bad-signature / ledger rule), the
-- surface the decode-stage rejections never reached. Pure in the seed.
-- ---------------------------------------------------------------------------
mutateTermSemantic :: StdGen -> Double -> Term -> (Term, MutationInfo)
mutateTermSemantic gen rate term =
    let (roll, g1) = randomR (0.0, 1.0) gen
    in  if roll >= rate
            then (term, MutationInfo "none" 0)
            else
                let (t', info) = perturbLeaf g1 0 term
                in  if t' == term
                        then (perturbValue g1 term, info {miKind = "semantic:forced"})
                        else (t', info)

-- | Descend (biased) to a seed-chosen leaf and perturb its value in place,
-- keeping the surrounding structure identical so the term still decodes.
perturbLeaf :: StdGen -> Int -> Term -> (Term, MutationInfo)
perturbLeaf gen depth t =
    let (descend, g1) = randomR (0.0, 1.0 :: Double) gen
    in  case (descend < 0.7, children t) of
            (True, cs@(_ : _)) ->
                let (ix, g2) = randomR (0, length cs - 1) g1
                    (child', info) = perturbLeaf g2 (depth + 1) (cs !! ix)
                in  (replaceChild ix child' t, info)
            _ -> (perturbValue g1 t, MutationInfo ("semantic:" <> leafKind t) depth)

-- | Change a value while preserving its CBOR major type and (for byte/text
-- strings) its length — so the typed decoder still accepts it.
perturbValue :: StdGen -> Term -> Term
perturbValue g (TBytes b)
    | not (BS.null b) =
        let (i, g2) = randomR (0, BS.length b - 1) g
            (d, _) = randomR (1, 255 :: Word8) g2
            (a, rest) = BS.splitAt i b
        in  case BS.uncons rest of
                Just (h, t) -> TBytes (a <> BS.cons (h `xor` d) t)
                Nothing -> TBytes b
perturbValue g (TInt n) = let (d, _) = randomR (1, maxBound :: Int) g in TInt (n + d)
perturbValue g (TInteger n) = let (d, _) = randomR (1, maxBound :: Int) g in TInteger (n + fromIntegral d)
perturbValue _ (TBool x) = TBool (not x)
perturbValue _ (TString s) = TString (if T.null s then "x" else T.cons 'X' (T.drop 1 s))
perturbValue _ t = t

leafKind :: Term -> Text
leafKind (TBytes _) = "bytes"
leafKind (TInt _) = "int"
leafKind (TInteger _) = "int"
leafKind (TString _) = "string"
leafKind (TBool _) = "bool"
leafKind _ = "other"
