{-# LANGUAGE DataKinds #-}
{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE TypeApplications #-}

-- |
-- Module: DwarfAdversary.MiniProtocolDecode
--
-- Single-message decode entrypoints for N2N mini-protocol codecs, for the
-- coverage-guided fuzz harness (@dwarf-decode-any@). Each runs the real
-- ouroboros-network codec's decoder for a representative protocol state over
-- the input bytes via 'runDecoder', returning whether it decoded cleanly.
--
-- These exercise mini-protocol *message grammar* code that the payload
-- decoders (decTx/decBlock/decHeader) do not: the typed-protocols message
-- envelopes, request/reply structure, and (tx-submission) the txid/tx list
-- framing. The version-sensitive typed-protocols plumbing lives here so the
-- harness only selects a surface.
module DwarfAdversary.MiniProtocolDecode
    ( decodeKeepAlive
    , decodeTxSubmission2
    , decodeHandshake
    ) where

import qualified Codec.CBOR.Term as CBOR (Term)
import qualified Data.ByteString.Lazy as LBS

import Network.TypedProtocol.Codec (Codec (..), runDecoder)
import Network.TypedProtocol.Core (StateToken, StateTokenI (stateToken))

import Ouroboros.Network.NodeToNode (NodeToNodeVersion)
import Ouroboros.Network.Protocol.Handshake.Codec (nodeToNodeHandshakeCodec)
import Ouroboros.Network.Protocol.Handshake.Type (Handshake (StConfirm))
import Ouroboros.Network.Protocol.KeepAlive.Codec (codecKeepAlive_v2)
import Ouroboros.Network.Protocol.KeepAlive.Type (KeepAlive (StServer))
import Ouroboros.Network.Protocol.TxSubmission2.Codec (codecTxSubmission2)
import Ouroboros.Network.Protocol.TxSubmission2.Type (TxSubmission2 (StIdle))

import DwarfAdversary.ChainSync.Codec (Block, GenTx, GenTxId, decTx, decTxId, encTx, encTxId)

-- | Decode a keep-alive message in the server state (MsgKeepAliveResponse
-- cookie path) over the input bytes.
decodeKeepAlive :: LBS.ByteString -> IO Bool
decodeKeepAlive bytes = do
    let Codec {decode} = codecKeepAlive_v2
    step <- decode (stateToken :: StateToken StServer)
    r <- runDecoder [bytes] step
    pure (either (const False) (const True) r)

-- | Decode a tx-submission2 message in the idle state (server's
-- MsgRequestTxIds / MsgRequestTxs / MsgInit request grammar) over the input
-- bytes, using the node's real GenTxId/GenTx (de)serialisers.
decodeTxSubmission2 :: LBS.ByteString -> IO Bool
decodeTxSubmission2 bytes = do
    let Codec {decode} = codecTxSubmission2 encTxId decTxId encTx decTx
    step <- decode
        (stateToken :: StateToken (StIdle :: TxSubmission2 (GenTxId Block) (GenTx Block)))
    r <- runDecoder [bytes] step
    pure (either (const False) (const True) r)

-- | Decode an N2N handshake message in the confirm state (the server's
-- MsgReplyVersions / MsgAcceptVersion / MsgRefuse version-negotiation reply)
-- over the input bytes.
decodeHandshake :: LBS.ByteString -> IO Bool
decodeHandshake bytes = do
    let Codec {decode} = nodeToNodeHandshakeCodec
    step <- decode
        (stateToken :: StateToken (StConfirm :: Handshake NodeToNodeVersion CBOR.Term))
    r <- runDecoder [bytes] step
    pure (either (const False) (const True) r)
