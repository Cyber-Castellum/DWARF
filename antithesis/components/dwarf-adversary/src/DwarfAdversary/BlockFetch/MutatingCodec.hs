{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE TypeApplications #-}

-- |
-- Module: DwarfAdversary.BlockFetch.MutatingCodec
--
-- A block-fetch codec identical to the plain
-- @codecBlockFetch encBlock decBlock encBlockPoint decBlockPoint@ except
-- the block /encode/ path is fuzzed: each block is encoded normally, its
-- CBOR decoded to a 'Codec.CBOR.Term.Term', structurally mutated
-- (DwarfAdversary.Fuzz), and re-encoded before it goes on the wire. The
-- node we serve then runs its real block-body decoder on the mutated
-- bytes. The decode side is untouched (we are the server; the client
-- decodes).
--
-- Determinism (recreate): the mutation applied to a block is a pure
-- function of @(seed, blockBytes)@ — no IORef, no clock, no
-- @\/dev\/urandom@ — so Antithesis can reproduce any finding from the
-- seed alone. This mirrors the chain-sync header path
-- (DwarfAdversary.ChainSync.MutatingCodec); 'subSeed'/'fnv1a64' are the
-- same determinism contract, kept local to avoid a cross-codec import.
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
    ( Block
    , decBlock
    , decBlockPoint
    , encBlock
    , encBlockPoint
    )
import DwarfAdversary.Fuzz (MutationInfo (..), mutateTerm)
import Network.TypedProtocol.Codec (Codec)
import Ouroboros.Network.Block qualified as Network
import Ouroboros.Network.Protocol.BlockFetch.Codec (codecBlockFetch)
import Ouroboros.Network.Protocol.BlockFetch.Type (BlockFetch)
import System.Random (mkStdGen)

-- | The block-fetch codec with a fuzzing block encoder. @seed@ is the
-- sole randomness source; @rate@ ∈ [0,1] is the mutation probability.
mutatingCodecBlockFetch
    :: Word64
    -> Double
    -> Codec
        (BlockFetch Block (Network.Point Block))
        DeserialiseFailure
        IO
        LBS.ByteString
mutatingCodecBlockFetch seed rate =
    codecBlockFetch (mutEncBlock seed rate) decBlock encBlockPoint decBlockPoint

-- | Encode a block, then structurally mutate its CBOR before emitting.
-- If the block CBOR does not decode as a single 'Term' (should not happen
-- for valid block bytes), the original encoding is emitted unchanged.
mutEncBlock :: Word64 -> Double -> Block -> Encoding
mutEncBlock seed rate b =
    let bytes = toLazyByteString (encBlock b)
    in  case deserialiseFromBytes decodeTerm bytes of
            Right (rest, term)
                | LBS.null rest ->
                    let g = mkStdGen (fromIntegral (subSeed seed bytes))
                        (term', _) = mutateTerm g rate term
                    in  encodeTerm term'
            _ -> encBlock b

-- | The mutation 'mutEncBlock' will apply to a block — exposed so the
-- server can emit a matching SDK assertion (same pure function, so the
-- reported mutation kind agrees with what went on the wire).
describeBlockMutation :: Word64 -> Double -> Block -> MutationInfo
describeBlockMutation seed rate b =
    let bytes = toLazyByteString (encBlock b)
    in  case deserialiseFromBytes decodeTerm bytes of
            Right (rest, term)
                | LBS.null rest ->
                    snd (mutateTerm (mkStdGen (fromIntegral (subSeed seed bytes))) rate term)
            _ -> MutationInfo "none" 0

-- | Fold a block's bytes into its seed so distinct captured blocks mutate
-- differently while staying a pure function of (seed, bytes). Mirrors
-- DwarfAdversary.ChainSync.MutatingCodec.
subSeed :: Word64 -> LBS.ByteString -> Word64
subSeed seed bytes = seed `xor` fnv1a64 bytes

fnv1a64 :: LBS.ByteString -> Word64
fnv1a64 = LBS.foldl' step 0xcbf29ce484222325
  where
    step acc b = (acc `xor` fromIntegral b) * 0x100000001b3
