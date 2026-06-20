{-# LANGUAGE OverloadedStrings #-}

-- |
-- Module: DwarfAdversary.ChainSync.MutatingCodec
--
-- A chain-sync codec identical to 'DwarfAdversary.ChainSync.Codec'
-- except the header /encode/ path is fuzzed: each header is encoded
-- normally, its CBOR is decoded to a 'Codec.CBOR.Term.Term',
-- structurally mutated (DwarfAdversary.Fuzz), and re-encoded before it
-- goes on the wire. The node we serve then runs its real header decoder
-- on the mutated bytes. The decode side is untouched (we are the
-- server; the client decodes).
--
-- Determinism (recreate): the mutation applied to a header is a pure
-- function of @(seed, headerBytes)@ — no IORef, no clock, no
-- @\/dev\/urandom@. The same seed and the same captured header always
-- produce the same mutated bytes, so Antithesis can reproduce any
-- finding from the seed alone. Variety across served frames comes from
-- serving a /list/ of distinct captured headers (each hashes
-- differently), not from hidden state.
module DwarfAdversary.ChainSync.MutatingCodec
    ( mutatingCodecChainSync
    , describeHeaderMutation
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
    ( Header
    , Point
    , Tip
    , decHeader
    , decPoint
    , decTip
    , encHeader
    , encPoint
    , encTip
    )
import DwarfAdversary.Fuzz
    ( MutationInfo (..)
    , MutationLevel (..)
    , corruptBytes
    , mutateTerm
    , mutateTermSemantic
    )
import Network.TypedProtocol.Codec (Codec)
import Ouroboros.Network.Protocol.ChainSync.Codec qualified as ChainSync
import Ouroboros.Network.Protocol.ChainSync.Type (ChainSync)
import System.Random (mkStdGen)

-- | The chain-sync codec with a fuzzing header encoder. @seed@ is the
-- sole randomness source; @rate@ ∈ [0,1] is the mutation probability;
-- @level@ selects structural / byte-level / both corruption.
mutatingCodecChainSync
    :: MutationLevel
    -> Word64
    -> Double
    -> Codec (ChainSync Header Point Tip) DeserialiseFailure IO LBS.ByteString
mutatingCodecChainSync level seed rate =
    ChainSync.codecChainSync
        (mutEncHeader level seed rate)
        decHeader
        encPoint
        decPoint
        encTip
        decTip

-- | Encode a header, then mutate its CBOR before emitting. @LevelStruct@
-- mutates the Term and re-encodes (valid CBOR); @LevelBytes@ corrupts the
-- serialized bytes (malformed CBOR); @LevelBoth@ does struct then bytes.
mutEncHeader :: MutationLevel -> Word64 -> Double -> Header -> Encoding
mutEncHeader level seed rate h =
    let bytes = toLazyByteString (encHeader h)
        g = mkStdGen (fromIntegral (subSeed seed bytes))
        mut f = case deserialiseFromBytes decodeTerm bytes of
            Right (rest, term) | LBS.null rest -> Just (fst (f g rate term))
            _ -> Nothing
    in  case level of
            LevelStruct -> maybe (encHeader h) encodeTerm (mut mutateTerm)
            LevelSemantic -> maybe (encHeader h) encodeTerm (mut mutateTermSemantic)
            LevelBytes -> encodePreEncoded (fst (corruptBytes g rate (LBS.toStrict bytes)))
            LevelBoth ->
                let base = maybe bytes (toLazyByteString . encodeTerm) (mut mutateTerm)
                in  encodePreEncoded (fst (corruptBytes g rate (LBS.toStrict base)))

-- | The mutation that 'mutEncHeader' will apply to a header — exposed so
-- the server can emit a matching SDK assertion (same pure function, so
-- the reported mutation kind agrees with what went on the wire).
describeHeaderMutation :: MutationLevel -> Word64 -> Double -> Header -> MutationInfo
describeHeaderMutation level seed rate h =
    let bytes = toLazyByteString (encHeader h)
        g = mkStdGen (fromIntegral (subSeed seed bytes))
        viaTerm f = case deserialiseFromBytes decodeTerm bytes of
            Right (rest, term) | LBS.null rest -> snd (f g rate term)
            _ -> MutationInfo "undecodable" 0
    in  case level of
            LevelStruct -> viaTerm mutateTerm
            LevelSemantic -> viaTerm mutateTermSemantic
            _ -> snd (corruptBytes g rate (LBS.toStrict bytes))

-- | Per-header sub-seed: base seed XOR FNV-1a hash of the header bytes.
subSeed :: Word64 -> LBS.ByteString -> Word64
subSeed seed bytes = seed `xor` fnv1a64 bytes

fnv1a64 :: LBS.ByteString -> Word64
fnv1a64 = LBS.foldl' step 0xcbf29ce484222325
  where
    step acc b = (acc `xor` fromIntegral b) * 0x100000001b3
