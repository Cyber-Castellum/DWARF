{-# LANGUAGE OverloadedStrings #-}

-- |
-- Module: DwarfAdversary.TxSubmission.Target
--
-- Sub-field-targeted CBOR mutation over the Conway transaction 'Term' layout.
-- A Conway tx Term is @TList [tx_body (TMap), witness_set, is_valid, aux_data]@.
-- The tx_body is a map keyed by small ints: certificates live at key @4@.
-- Auxiliary data is the tx array's element index 3.
--
-- IMPORTANT — envelope unwrapping. The bytes the codec hands us are NOT the bare
-- Conway tx array. @encTx = encodeNodeToNode \@Block@ wraps the era tx in the
-- HardFork node-to-node /GenTx envelope/: an era-disambiguation tag plus a
-- CBOR-in-CBOR (CBOR tag 24) byte string holding the real tx, i.e. roughly
-- @TList [TInt eraIndex, TTagged 24 (TBytes \<conway-tx\>)]@. A naive navigation
-- on the top-level Term therefore never sees @tx_body@ key 4 and silently falls
-- back to mutating the envelope. 'locateTxArray' descends through @TList@ /
-- @TListI@ structure and unwraps tag-24 byte strings to find the real tx array
-- (the array whose head is the @tx_body@ map, identified by key @0@ = inputs),
-- applies the targeted transform there, and re-encodes the unwrapped CBOR layers
-- on the way out so the result is a valid wire 'GenTx' again.
--
-- 'mutateTxField' selects the sub-'Term' to perturb by 'TxField' (from the
-- scenario's @--cbor-shape@), applies 'Fuzz.mutateTerm' to just that sub-Term,
-- and splices it back. The structural mutation engine is unchanged — only the
-- /target/ is selected. On a navigation miss (no tx array found, or the field is
-- absent) it falls back to mutating the whole top-level Term and records
-- @"fallback:"@ in the 'MutationInfo' kind, so a clean-but-untargeted mutation is
-- visible rather than silently dropped.
module DwarfAdversary.TxSubmission.Target
    ( TxField (..)
    , mutateTxField
    ) where

import Codec.CBOR.Read (deserialiseFromBytes)
import Codec.CBOR.Term (Term (..), decodeTerm, encodeTerm)
import Codec.CBOR.Write (toStrictByteString)
import Data.Bits (xor)
import Data.ByteString qualified as BS
import Data.ByteString.Lazy qualified as LBS
import Data.Text (Text)
import DwarfAdversary.Fuzz (MutationInfo (..), mutateTerm)
import System.Random (StdGen, randomR)

-- | Which part of a transaction to structurally mutate.
data TxField
    = WholeTx
    -- ^ mutate the whole Conway tx array (tx-body shape)
    | Certificate
    -- ^ navigate to tx_body key 4 (certificates) and mutate there
    | AuxData
    -- ^ navigate to the tx's auxiliary-data element and mutate there
    | Witness
    -- ^ flip a byte of the first vkey-witness signature (witness_set key 0),
    -- leaving tx_body untouched so the txid is unchanged. The node therefore
    -- accepts the tx into its mempool (txid matches) and ledger validation
    -- rejects it at the witness check — the ledger-layer probe (FU3c-deep).
    deriving (Eq, Show)

-- | Apply a seeded structural mutation to the targeted sub-field of a tx Term.
-- The incoming @tx@ is the HardFork N2N GenTx envelope; we unwrap to the real
-- Conway tx array before targeting (see module note).
mutateTxField :: TxField -> StdGen -> Double -> Term -> (Term, MutationInfo)
mutateTxField field g rate tx = case field of
    WholeTx ->
        case locateTxArray (\t -> Just (mutateTerm g rate t)) tx of
            Just (tx', info) -> (tx', info)
            Nothing -> fallback
    Certificate ->
        case locateTxArray (mutCertField g rate) tx of
            Just (tx', info) -> (tx', tag "cert:" info)
            Nothing -> fallback
    AuxData ->
        case locateTxArray (mutAuxField g rate) tx of
            Just (tx', info) -> (tx', tag "aux:" info)
            Nothing -> fallback
    Witness ->
        case locateTxArray (mutWitnessField g) tx of
            Just (tx', info) -> (tx', tag "wit:" info)
            Nothing -> fallback
  where
    fallback =
        let (t', info) = mutateTerm g rate tx
        in (t', tag "fallback:" info)

tag :: Text -> MutationInfo -> MutationInfo
tag p info = info {miKind = p <> miKind info}

-- | Is this Term a @tx_body@ map? (A CBOR map carrying key @TInt 0@ = inputs.)
isTxBody :: Term -> Bool
isTxBody (TMap kvs) = any ((== TInt 0) . fst) kvs
isTxBody (TMapI kvs) = any ((== TInt 0) . fst) kvs
isTxBody _ = False

-- | Is this Term the Conway tx array, i.e. @[tx_body, wits, ...]@?
isTxArray :: Term -> Bool
isTxArray (TList (b : _)) = isTxBody b
isTxArray (TListI (b : _)) = isTxBody b
isTxArray _ = False

-- | Decode an embedded CBOR byte string to a Term (must consume all bytes).
decodeInner :: LBS.ByteString -> Maybe Term
decodeInner bs = case deserialiseFromBytes decodeTerm bs of
    Right (rest, t) | LBS.null rest -> Just t
    _ -> Nothing

-- | Descend through the HardFork N2N GenTx envelope to the real Conway tx array
-- and apply @f@ to it. @f@ returns 'Nothing' when the targeted field is absent
-- (so the caller can fall back). Unwrapped CBOR-in-CBOR (tag 24) layers are
-- re-encoded on the way out so the rebuilt Term is a valid wire GenTx again.
locateTxArray
    :: (Term -> Maybe (Term, MutationInfo)) -> Term -> Maybe (Term, MutationInfo)
locateTxArray f t
    | isTxArray t = f t
    | otherwise = case t of
        TTagged 24 (TBytes bs) -> do
            inner <- decodeInner (LBS.fromStrict bs)
            (inner', info) <- locateTxArray f inner
            pure (TTagged 24 (TBytes (toStrictByteString (encodeTerm inner'))), info)
        TList xs -> descend TList xs
        TListI xs -> descend TListI xs
        _ -> Nothing
  where
    descend ctor xs = go 0 xs
      where
        go _ [] = Nothing
        go i (x : rest) = case locateTxArray f x of
            Just (x', info) -> Just (ctor (take i xs ++ [x'] ++ drop (i + 1) xs), info)
            Nothing -> go (i + 1) rest

-- | Mutate the certificates entry (key 4) of a tx array's @tx_body@ map.
-- 'Nothing' if the body is not a map or has no key-4 (a non-cert tx).
mutCertField :: StdGen -> Double -> Term -> Maybe (Term, MutationInfo)
mutCertField g rate t = case t of
    TList (body : rest) -> wrap TList rest <$> mutateMapKey (TInt 4) g rate body
    TListI (body : rest) -> wrap TListI rest <$> mutateMapKey (TInt 4) g rate body
    _ -> Nothing
  where
    wrap ctor rest (body', info) = (ctor (body' : rest), info)

-- | Mutate the auxiliary-data element (index 3) of a tx array. 'Nothing' if the
-- array is too short or aux-data is absent (@TNull@) — so a metadata-less tx
-- falls back rather than "engaging" on a null.
mutAuxField :: StdGen -> Double -> Term -> Maybe (Term, MutationInfo)
mutAuxField g rate t = case t of
    TList xs | length xs >= 4, notNull (xs !! 3) -> Just (splice TList xs)
    TListI xs | length xs >= 4, notNull (xs !! 3) -> Just (splice TListI xs)
    _ -> Nothing
  where
    notNull TNull = False
    notNull _ = True
    splice ctor xs =
        let (a', info) = mutateTerm g rate (xs !! 3)
        in (ctor (take 3 xs ++ [a'] ++ drop 4 xs), info)

-- | Ledger-layer probe (FU3c-deep): flip a byte of the first vkey-witness
-- signature. The tx array is @[tx_body, witness_set, is_valid, aux]@; we touch
-- only @witness_set@ (index 1), leaving @tx_body@ (index 0) byte-identical so the
-- txid is unchanged — the node accepts the tx into its mempool (txid matches) and
-- ledger validation then rejects the bad signature (@InvalidWitnessesUTXOW@).
-- 'Nothing' if there is no vkey witness to tamper (→ fallback).
mutWitnessField :: StdGen -> Term -> Maybe (Term, MutationInfo)
mutWitnessField g t = case t of
    TList (body : wits : rest) -> wrap TList body rest <$> mutWitnessSet g wits
    TListI (body : wits : rest) -> wrap TListI body rest <$> mutWitnessSet g wits
    _ -> Nothing
  where
    wrap ctor body rest (wits', info) = (ctor (body : wits' : rest), info)

-- | In the witness_set map, mutate the value at key 0 (the vkey-witness list).
mutWitnessSet :: StdGen -> Term -> Maybe (Term, MutationInfo)
mutWitnessSet g w = case w of
    TMap kvs -> rebuild TMap kvs
    TMapI kvs -> rebuild TMapI kvs
    _ -> Nothing
  where
    rebuild ctor kvs = case lookup (TInt 0) kvs of
        Just wl -> (\(wl', info) -> (ctor (replaceVal kvs wl'), info)) <$> mutWitnessList g wl
        Nothing -> Nothing
    replaceVal kvs v' = map (\(k, v) -> if k == TInt 0 then (k, v') else (k, v)) kvs

-- | Flip a byte of the first witness's signature. Handles the @set@ tag (258)
-- wrapper Conway uses around the witness list.
mutWitnessList :: StdGen -> Term -> Maybe (Term, MutationInfo)
mutWitnessList g w = case w of
    TList (w0 : ws) -> (\(w0', i) -> (TList (w0' : ws), i)) <$> flipSig g w0
    TListI (w0 : ws) -> (\(w0', i) -> (TListI (w0' : ws), i)) <$> flipSig g w0
    TTagged tg inner -> (\(inner', i) -> (TTagged tg inner', i)) <$> mutWitnessList g inner
    _ -> Nothing

-- | A vkey witness is @[vkey, signature]@; flip a byte of the signature TBytes,
-- preserving its length (still a valid 64-byte ed25519 sig structurally).
flipSig :: StdGen -> Term -> Maybe (Term, MutationInfo)
flipSig g w0 = case w0 of
    TList [vk, TBytes sig] -> Just (TList [vk, TBytes (flipByte g sig)], MutationInfo "sigflip" 0)
    TListI [vk, TBytes sig] -> Just (TListI [vk, TBytes (flipByte g sig)], MutationInfo "sigflip" 0)
    _ -> Nothing

-- | XOR-flip the low bit of one (seed-chosen) byte; length-preserving.
flipByte :: StdGen -> BS.ByteString -> BS.ByteString
flipByte g bs
    | BS.null bs = bs
    | otherwise =
        let (i, _) = randomR (0, BS.length bs - 1) g
            b = BS.index bs i
        in BS.take i bs <> BS.singleton (b `xor` 1) <> BS.drop (i + 1) bs

-- | Mutate the value at @key@ inside a finite or indefinite CBOR map Term.
-- Returns Nothing if @term@ is not a map or @key@ is absent.
mutateMapKey :: Term -> StdGen -> Double -> Term -> Maybe (Term, MutationInfo)
mutateMapKey key g rate term = case term of
    TMap kvs -> rebuildTMap <$> go kvs
    TMapI kvs -> rebuildTMapI <$> go kvs
    _ -> Nothing
  where
    go kvs = case lookup key kvs of
        Nothing -> Nothing
        Just v ->
            let (v', info) = mutateTerm g rate v
                kvs' = map (\(k, ov) -> if k == key then (k, v') else (k, ov)) kvs
            in Just (kvs', info)
    rebuildTMap (kvs', info) = (TMap kvs', info)
    rebuildTMapI (kvs', info) = (TMapI kvs', info)
