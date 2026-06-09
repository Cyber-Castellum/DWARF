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
    ) where

import Codec.CBOR.Term (Term (..))
import Data.ByteString qualified as BS
import Data.ByteString.Lazy qualified as LBS
import Data.Text (Text)
import Data.Text qualified as T
import Data.Text.Encoding qualified as TE
import Data.Text.Lazy qualified as LT
import System.Random (StdGen, randomR)

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
