{-# LANGUAGE OverloadedStrings #-}

-- |
-- Module: DwarfAdversary.TxSubmission.Target
--
-- Sub-field-targeted CBOR mutation over the Conway transaction 'Term' layout.
-- A Conway tx Term is @TList [tx_body (TMap), witness_set, is_valid, aux_data]@.
-- The tx_body is a map keyed by small ints: certificates live at key @4@.
-- Auxiliary data is the tx array's element index 3.
--
-- 'mutateTxField' selects the sub-'Term' to perturb by 'TxField' (from the
-- scenario's @--cbor-shape@), applies 'Fuzz.mutateTerm' to just that sub-Term,
-- and splices it back. The structural mutation engine is unchanged — only the
-- /target/ is selected. On a navigation miss (the field is absent, or the tx
-- is shaped unexpectedly) it falls back to mutating the whole tx_body and
-- records @"fallback:"@ in the 'MutationInfo' kind, so a clean-but-untargeted
-- mutation is visible rather than silently dropped.
module DwarfAdversary.TxSubmission.Target
    ( TxField (..)
    , mutateTxField
    ) where

import Codec.CBOR.Term (Term (..))
import Data.Text (Text)
import DwarfAdversary.Fuzz (MutationInfo (..), mutateTerm)
import System.Random (StdGen)

-- | Which part of a transaction to structurally mutate.
data TxField
    = WholeTx
    -- ^ mutate the whole tx_body (tx-body shape)
    | Certificate
    -- ^ navigate to tx_body key 4 (certificates) and mutate there
    | AuxData
    -- ^ navigate to the tx's auxiliary-data element and mutate there
    deriving (Eq, Show)

-- | Apply a seeded structural mutation to the targeted sub-field of a tx Term.
mutateTxField :: TxField -> StdGen -> Double -> Term -> (Term, MutationInfo)
mutateTxField field g rate tx = case (field, tx) of
    (WholeTx, TList (body : rest)) ->
        let (b', info) = mutateTerm g rate body
        in (TList (b' : rest), info)
    (Certificate, TList (body : rest)) ->
        case mutateMapKey (TInt 4) g rate body of
            Just (body', info) -> (TList (body' : rest), tag "cert:" info)
            Nothing -> fallback
    (AuxData, TList xs) | length xs >= 4 ->
        let aux = xs !! 3
            (a', info) = mutateTerm g rate aux
            xs' = take 3 xs ++ [a'] ++ drop 4 xs
        in (TList xs', tag "aux:" info)
    _ -> fallback
  where
    fallback =
        let (t', info) = mutateTerm g rate tx
        in (t', tag "fallback:" info)

tag :: Text -> MutationInfo -> MutationInfo
tag p info = info {miKind = p <> miKind info}

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
