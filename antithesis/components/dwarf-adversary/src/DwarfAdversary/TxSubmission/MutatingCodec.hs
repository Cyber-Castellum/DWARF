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
import DwarfAdversary.Fuzz (MutationInfo (..))
import DwarfAdversary.TxSubmission.Target (TxField, mutateTxField)
import Network.TypedProtocol.Codec (Codec)
import Ouroboros.Network.Protocol.TxSubmission2.Codec (codecTxSubmission2)
import Ouroboros.Network.Protocol.TxSubmission2.Type (TxSubmission2)
import System.Random (mkStdGen)

-- | The tx-submission2 codec with a fuzzing tx encoder targeting @field@.
-- @seed@ is the sole randomness source; @rate@ ∈ [0,1] is the mutation
-- probability.
mutatingCodecTxSubmission
    :: TxField
    -> Word64
    -> Double
    -> Codec
        (TxSubmission2 (GenTxId Block) (GenTx Block))
        DeserialiseFailure
        IO
        LBS.ByteString
mutatingCodecTxSubmission field seed rate =
    codecTxSubmission2 encTxId decTxId (mutEncTx field seed rate) decTx

-- | Encode a tx, then structurally mutate the targeted sub-field of its CBOR
-- before emitting. If the tx CBOR does not decode as a single 'Term' (should
-- not happen for valid tx bytes), the original encoding is emitted unchanged.
mutEncTx :: TxField -> Word64 -> Double -> GenTx Block -> Encoding
mutEncTx field seed rate tx =
    let bytes = toLazyByteString (encTx tx)
    in  case deserialiseFromBytes decodeTerm bytes of
            Right (rest, term)
                | LBS.null rest ->
                    let g = mkStdGen (fromIntegral (subSeed seed bytes))
                        (term', _) = mutateTxField field g rate term
                    in  encodeTerm term'
            _ -> encTx tx

-- | The mutation 'mutEncTx' will apply — exposed so the server can emit a
-- matching SDK assertion (same pure function, so the reported kind agrees with
-- what went on the wire).
describeTxMutation :: TxField -> Word64 -> Double -> GenTx Block -> MutationInfo
describeTxMutation field seed rate tx =
    let bytes = toLazyByteString (encTx tx)
    in  case deserialiseFromBytes decodeTerm bytes of
            Right (rest, term)
                | LBS.null rest ->
                    snd (mutateTxField field (mkStdGen (fromIntegral (subSeed seed bytes))) rate term)
            _ -> MutationInfo "none" 0

-- | Fold the tx bytes into the seed so distinct txs mutate differently while
-- staying a pure function of (seed, bytes). Mirrors the header/block codecs.
subSeed :: Word64 -> LBS.ByteString -> Word64
subSeed seed bytes = seed `xor` fnv1a64 bytes

fnv1a64 :: LBS.ByteString -> Word64
fnv1a64 = LBS.foldl' step 0xcbf29ce484222325
  where
    step acc b = (acc `xor` fromIntegral b) * 0x100000001b3
