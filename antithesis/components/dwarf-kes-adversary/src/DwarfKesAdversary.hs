{-# LANGUAGE LambdaCase #-}
{-# LANGUAGE OverloadedStrings #-}

module DwarfKesAdversary
    ( KesMutation (..)
    , describeKesMutation
    , gateKesMutation
    , kesMismatchCodec
    , kesSeededCodec
    , mutateKesSignatureBytes
    , mutateKesSignatureSeeded
    , singleTargetServer
    ) where

import Codec.CBOR.Encoding (encodePreEncoded)
import Codec.CBOR.Write (toLazyByteString)
import Codec.Serialise (DeserialiseFailure)
import Control.Concurrent (threadDelay)
import Data.Bits (shiftL, shiftR, xor)
import Data.ByteString qualified as BS
import Data.ByteString.Lazy qualified as LBS
import Data.List (find)
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
import Network.TypedProtocol.Codec (Codec)
import Ouroboros.Network.Block (HeaderFields (..), SlotNo (..), getHeaderFields)
import Ouroboros.Network.Protocol.ChainSync.Codec qualified as ChainSync
import Ouroboros.Network.Protocol.ChainSync.Server
    ( ChainSyncServer (..)
    , ServerStIdle (..)
    , ServerStIntersect (..)
    , ServerStNext (..)
    )
import Ouroboros.Network.Protocol.ChainSync.Type (ChainSync)


-- | Flip one bit in the final byte.  A Shelley-family header's KES
-- signature is the final CBOR field, so the header body, VRF proof, slot,
-- block number, parent and opcert are byte-for-byte intact.  The header hash
-- correctly changes because it commits to the signature-bearing header.
mutateKesSignatureBytes :: BS.ByteString -> BS.ByteString
mutateKesSignatureBytes bytes
    | BS.null bytes = bytes
    | otherwise = BS.init bytes <> BS.singleton (BS.last bytes `xor` 1)


data KesMutation = KesMutation
    { kesMutationApplied :: Bool
    , kesSignatureOffset :: Int
    , kesMutationBit :: Int
    }
    deriving (Eq, Show)


kesSignatureBytes :: Int
kesSignatureBytes = 448


-- | Pure per-header selection. The same seed and wire bytes always make the
-- same decision, which keeps Antithesis replays deterministic.
describeKesMutation :: Word64 -> Double -> BS.ByteString -> KesMutation
describeKesMutation seed rate bytes
    | BS.length bytes < kesSignatureBytes = KesMutation False (-1) (-1)
    | rate <= 0 = KesMutation False (-1) (-1)
    | otherwise =
        let h = fnv1a64 seed bytes
            bucket = h `mod` 1_000_000
            limit :: Word64
            limit
                | rate >= 1 = 1_000_000
                | otherwise = floor (rate * 1_000_000)
            offset = fromIntegral ((h `shiftR` 16) `mod` fromIntegral kesSignatureBytes)
            bitIndex = fromIntegral ((h `shiftR` 8) `mod` 8)
        in  KesMutation (bucket < limit) offset bitIndex


gateKesMutation :: Word64 -> Word64 -> KesMutation -> KesMutation
gateKesMutation minimumSlot currentSlot mutation
    | currentSlot < minimumSlot = KesMutation False (-1) (-1)
    | otherwise = mutation


-- | Flip exactly one selected bit inside the final 448 signature payload
-- bytes. CBOR framing and every byte before the signature remain unchanged.
mutateKesSignatureSeeded :: Word64 -> Double -> BS.ByteString -> BS.ByteString
mutateKesSignatureSeeded seed rate bytes =
    case describeKesMutation seed rate bytes of
        KesMutation False _ _ -> bytes
        KesMutation True offset bitIndex ->
            let absolute = BS.length bytes - kesSignatureBytes + offset
                (prefix, rest) = BS.splitAt absolute bytes
            in  case BS.uncons rest of
                    Nothing -> bytes
                    Just (selected, suffix) ->
                        prefix <> BS.singleton (selected `xor` (1 `shiftL` bitIndex)) <> suffix


fnv1a64 :: Word64 -> BS.ByteString -> Word64
fnv1a64 seed = BS.foldl' step (0xcbf29ce484222325 `xor` seed)
  where
    step acc byte = (acc `xor` fromIntegral byte) * 0x100000001b3


kesMismatchCodec
    :: Codec (ChainSync Header Point Tip) DeserialiseFailure IO LBS.ByteString
kesMismatchCodec =
    ChainSync.codecChainSync mutate decHeader encPoint decPoint encTip decTip
  where
    mutate =
        encodePreEncoded
            . mutateKesSignatureBytes
            . LBS.toStrict
            . toLazyByteString
            . encHeader


kesSeededCodec
    :: Word64
    -> Double
    -> Word64
    -> Codec (ChainSync Header Point Tip) DeserialiseFailure IO LBS.ByteString
kesSeededCodec seed rate minimumSlot =
    ChainSync.codecChainSync mutate decHeader encPoint decPoint encTip decTip
  where
    mutate header =
        let originalBytes = LBS.toStrict (toLazyByteString (encHeader header))
            HeaderFields (SlotNo currentSlot) _ _ = getHeaderFields header
            mutation = gateKesMutation minimumSlot currentSlot (describeKesMutation seed rate originalBytes)
            wireBytes =
                if kesMutationApplied mutation
                    then mutateKesSignatureSeeded seed rate originalBytes
                    else originalBytes
        in  encodePreEncoded wireBytes


-- | Offer exactly one target header, and only after the client proves it has
-- the exact real parent.  No fabricated intersection and no cyclic replay.
singleTargetServer
    :: (String -> IO ())
    -> Point
    -> Header
    -> Tip
    -> ChainSyncServer Header Point Tip IO ()
singleTargetServer log_ parentPoint target tip = ChainSyncServer (pure (idle False))
  where
    idle served =
        ServerStIdle
            { recvMsgRequestNext =
                if served
                    then pure (Right (foreverAwait served))
                    else do
                        log_ "kes-target: serving one hot-key-mismatch header"
                        pure
                            ( Left
                                ( SendMsgRollForward
                                    target
                                    tip
                                    (ChainSyncServer (pure (idle True)))
                                )
                            )
            , recvMsgFindIntersect = \points ->
                case find (== parentPoint) points of
                    Just point -> do
                        log_ "kes-target: exact parent intersection found"
                        pure
                            ( SendMsgIntersectFound
                                point
                                tip
                                (ChainSyncServer (pure (idle False)))
                            )
                    Nothing -> do
                        log_ "kes-target: exact parent absent; refusing intersection"
                        pure
                            ( SendMsgIntersectNotFound
                                tip
                                (ChainSyncServer (pure (idle False)))
                            )
            , recvMsgDoneClient = log_ "kes-target: client done"
            }

    foreverAwait served = do
        threadDelay 1_000_000
        foreverAwait served
