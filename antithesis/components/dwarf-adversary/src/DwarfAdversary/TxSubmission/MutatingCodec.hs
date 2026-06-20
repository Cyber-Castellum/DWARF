{-# LANGUAGE OverloadedStrings #-}

-- |
-- Module: DwarfAdversary.TxSubmission.MutatingCodec
--
-- A tx-submission2 codec identical to
-- @codecTxSubmission2 encTxId decTxId encTx decTx@ except the tx /encode/ path
-- is fuzzed: each tx is encoded, its CBOR decoded to a 'Codec.CBOR.Term.Term',
-- structurally mutated at the targeted sub-field (DwarfAdversary.TxSubmission.Target),
-- and re-encoded before it goes on the wire. The node we offer to then runs its
-- real tx decoder (and the certificate / auxiliary-data sub-decoders inside the
-- tx) on the mutated bytes. The decode side is untouched (we are the provider;
-- the node decodes).
--
-- Determinism (recreate): the mutation is a pure function of @(seed, txBytes)@ —
-- no IORef, clock, or @\/dev\/urandom@ — mirroring the chain-sync header and
-- block-fetch block paths, so Antithesis can reproduce any finding from the
-- seed alone.
module DwarfAdversary.TxSubmission.MutatingCodec
    ( mutatingCodecTxSubmission
    , mutEncTx
    , describeTxMutation
    ) where

import Codec.CBOR.Encoding (encodePreEncoded)
import Codec.CBOR.Read (deserialiseFromBytes)
import Codec.CBOR.Term (decodeTerm, encodeTerm)
import Codec.CBOR.Write (toLazyByteString)
import Codec.Serialise (DeserialiseFailure)
import Codec.Serialise.Encoding (Encoding)
import Data.Bits (xor)
import Data.ByteString.Lazy qualified as LBS
import Data.Word (Word64)
import DwarfAdversary.ChainSync.Codec
    ( Block
    , GenTx
    , GenTxId
    , decTx
    , decTxId
    , encTx
    , encTxId
    )
import DwarfAdversary.Fuzz
    ( MutationInfo (..)
    , MutationLevel (..)
    , corruptBytes
    , mutateTermSemantic
    )
import DwarfAdversary.TxSubmission.Target (TxField, mutateTxField)
import Network.TypedProtocol.Codec (Codec)
import Ouroboros.Network.Protocol.TxSubmission2.Codec (codecTxSubmission2)
import Ouroboros.Network.Protocol.TxSubmission2.Type (TxSubmission2)
import System.Random (mkStdGen)

-- | The tx-submission2 codec with a fuzzing tx encoder targeting @field@.
-- @seed@ is the sole randomness source; @rate@ ∈ [0,1] is the mutation
-- probability; @level@ selects structural / byte-level / both corruption.
mutatingCodecTxSubmission
    :: TxField
    -> MutationLevel
    -> Word64
    -> Double
    -> Codec
        (TxSubmission2 (GenTxId Block) (GenTx Block))
        DeserialiseFailure
        IO
        LBS.ByteString
mutatingCodecTxSubmission field level seed rate =
    codecTxSubmission2 encTxId decTxId (mutEncTx field level seed rate) decTx

-- | Encode a tx, then mutate it before emitting. @LevelStruct@ mutates the
-- targeted sub-field's Term and re-encodes (valid CBOR); @LevelBytes@ corrupts
-- the serialized bytes (malformed CBOR); @LevelBoth@ does struct then bytes.
mutEncTx :: TxField -> MutationLevel -> Word64 -> Double -> GenTx Block -> Encoding
mutEncTx field level seed rate tx =
    let bytes = toLazyByteString (encTx tx)
        g = mkStdGen (fromIntegral (subSeed seed bytes))
        viaTerm f = case deserialiseFromBytes decodeTerm bytes of
            Right (rest, term) | LBS.null rest -> Just (fst (f term))
            _ -> Nothing
        structTerm = viaTerm (mutateTxField field g rate)
    in  case level of
            LevelStruct -> maybe (encTx tx) encodeTerm structTerm
            LevelSemantic -> maybe (encTx tx) encodeTerm (viaTerm (mutateTermSemantic g rate))
            LevelBytes -> encodePreEncoded (fst (corruptBytes g rate (LBS.toStrict bytes)))
            LevelBoth ->
                let base = maybe bytes (toLazyByteString . encodeTerm) structTerm
                in  encodePreEncoded (fst (corruptBytes g rate (LBS.toStrict base)))

-- | The mutation 'mutEncTx' will apply — exposed so the server can emit a
-- matching SDK assertion (same pure function, so the reported kind agrees with
-- what went on the wire).
describeTxMutation :: TxField -> MutationLevel -> Word64 -> Double -> GenTx Block -> MutationInfo
describeTxMutation field level seed rate tx =
    let bytes = toLazyByteString (encTx tx)
        g = mkStdGen (fromIntegral (subSeed seed bytes))
        decoded = case deserialiseFromBytes decodeTerm bytes of
            Right (rest, term) | LBS.null rest -> Just term
            _ -> Nothing
    in  case level of
            LevelStruct -> maybe (MutationInfo "none" 0) (snd . mutateTxField field g rate) decoded
            LevelSemantic -> maybe (MutationInfo "none" 0) (snd . mutateTermSemantic g rate) decoded
            _ -> snd (corruptBytes g rate (LBS.toStrict bytes))

-- | Fold the tx bytes into the seed so distinct txs mutate differently while
-- staying a pure function of (seed, bytes). Mirrors the header/block codecs.
subSeed :: Word64 -> LBS.ByteString -> Word64
subSeed seed bytes = seed `xor` fnv1a64 bytes

fnv1a64 :: LBS.ByteString -> Word64
fnv1a64 = LBS.foldl' step 0xcbf29ce484222325
  where
    step acc b = (acc `xor` fromIntegral b) * 0x100000001b3
