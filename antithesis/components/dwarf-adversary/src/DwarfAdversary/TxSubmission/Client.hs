{-# LANGUAGE NumericUnderscores #-}
{-# LANGUAGE OverloadedStrings #-}

-- |
-- Module: DwarfAdversary.TxSubmission.Client
--
-- A tx-submission2 client (the PROVIDER role): the adversary offers captured
-- transactions (txid + body) to the connected node. The node's tx-submission
-- consumer then requests and decodes each one — running its real tx decoder
-- (and the certificate / auxiliary-data sub-decoders inside the tx) on
-- adversarial CBOR.
--
-- The transactions are supplied as real 'GenTx' values; the structural mutation
-- is applied on the codec /encode/ path (DwarfAdversary.TxSubmission.* mutating
-- codec), mirroring the chain-sync header and block-fetch block paths — so this
-- client serves the real tx value and the mutated bytes go on the wire.
--
-- The batch is pulled from a LIVE-REFRESHED source (an @IO@ action backed by a
-- background thread that re-captures from the synced chain), not a static list:
-- as the chain grows and new txs land, the provider announces the new txids and
-- serves their (mutated) bodies — a continuous fuzz stream. On an empty batch a
-- blocking @RequestTxIds@ WAITS for the chain to produce more (polling) instead
-- of parking forever, and it never sends @SendMsgDone@ (completing the
-- initiator #4 mini-protocol is what crashed the process, exit 1).
module DwarfAdversary.TxSubmission.Client
    ( txProviderClient
    ) where

import Control.Concurrent (threadDelay)
import Data.List.NonEmpty qualified as NE
import Data.Map.Strict (Map)
import Data.Map.Strict qualified as Map
import Data.Set (Set)
import Data.Set qualified as Set
import DwarfAdversary.ChainSync.Codec (Block, GenTx, GenTxId)
import Ouroboros.Network.Protocol.TxSubmission2.Client
    ( ClientStIdle (ClientStIdle, recvMsgRequestTxIds, recvMsgRequestTxs)
    , ClientStTxIds (SendMsgReplyTxIds)
    , ClientStTxs (SendMsgReplyTxs)
    , TxSubmissionClient (TxSubmissionClient)
    )
import Ouroboros.Network.Protocol.TxSubmission2.Type
    ( BlockingReplyList (BlockingReply, NonBlockingReply)
    , SingBlockingStyle (SingBlocking, SingNonBlocking)
    , SizeInBytes
    , StBlockingStyle (StBlocking)
    )

-- | Offer txs from a live-refreshed batch, announcing each fresh txid once and
-- serving its body on demand, forever. @fetchBatch@ returns the currently
-- captured txs (typically a recent window from the synced chain), and may grow
-- over time; the provider tracks which txids it has already announced and only
-- offers new ones. A blocking @RequestTxIds@ with no fresh txids polls
-- @fetchBatch@ until one appears (it must reply non-empty), so the connection
-- stays alive and serving without ever completing the mini-protocol.
txProviderClient
    :: (String -> IO ())
    -> (GenTx Block -> IO ())
    -- ^ onServe: called per tx actually handed to the consumer (for a true
    --   per-serve SDK assertion, not a once-at-startup emit)
    -> IO [(GenTxId Block, SizeInBytes, GenTx Block)]
    -- ^ fetch the current captured batch (refreshed live by the caller)
    -> TxSubmissionClient (GenTxId Block) (GenTx Block) IO ()
txProviderClient log_ onServe fetchBatch =
    TxSubmissionClient (pure (idle Map.empty Set.empty))
  where
    -- @known@: every tx body seen so far, by id (for RequestTxs lookup).
    -- @announced@: txids already offered (so we never re-announce one).
    idle
        :: Map (GenTxId Block) (GenTx Block)
        -> Set (GenTxId Block)
        -> ClientStIdle (GenTxId Block) (GenTx Block) IO ()
    idle known announced =
        ClientStIdle
            { recvMsgRequestTxIds = \blocking _ack req -> do
                let n = max 1 (fromIntegral req)
                batch <- fetchBatch
                let known' = absorb known batch
                    offer = take n (freshOf announced batch)
                case (offer, blocking) of
                    (_ : _, SingBlocking) -> do
                        log_ ("tx-submission: offering " <> show (length offer) <> " txid(s) (blocking)")
                        pure
                            ( SendMsgReplyTxIds
                                (BlockingReply (NE.fromList offer))
                                (idle known' (announce announced offer))
                            )
                    (_ : _, SingNonBlocking) ->
                        pure
                            ( SendMsgReplyTxIds
                                (NonBlockingReply offer)
                                (idle known' (announce announced offer))
                            )
                    ([], SingNonBlocking) ->
                        pure (SendMsgReplyTxIds (NonBlockingReply []) (idle known' announced))
                    ([], SingBlocking) -> do
                        -- A blocking request MUST be answered non-empty. No fresh
                        -- txids yet: wait for the chain to produce more, then offer
                        -- (replaces the old park-forever — keeps serving live).
                        log_ "tx-submission: no fresh tx; waiting for chain to produce more"
                        waitForFresh n known' announced
            , recvMsgRequestTxs = \requested -> do
                let served = [tx | tid <- requested, Just tx <- [Map.lookup tid known]]
                log_ ("tx-submission: serving " <> show (length served) <> " tx(s)")
                mapM_ onServe served
                pure (SendMsgReplyTxs served (idle known announced))
            }

    -- Poll the live batch until at least one fresh txid appears, then reply with
    -- a non-empty blocking list. Accumulates newly-seen bodies into @known@.
    waitForFresh
        :: Int
        -> Map (GenTxId Block) (GenTx Block)
        -> Set (GenTxId Block)
        -> IO (ClientStTxIds StBlocking (GenTxId Block) (GenTx Block) IO ())
    waitForFresh n known announced = do
        threadDelay 3_000_000
        batch <- fetchBatch
        let known' = absorb known batch
        case take n (freshOf announced batch) of
            [] -> waitForFresh n known' announced
            offer -> do
                log_ ("tx-submission: offering " <> show (length offer) <> " fresh txid(s) after wait")
                pure
                    ( SendMsgReplyTxIds
                        (BlockingReply (NE.fromList offer))
                        (idle known' (announce announced offer))
                    )

    -- txids in @batch@ not yet announced, paired with their advertised size.
    freshOf
        :: Set (GenTxId Block)
        -> [(GenTxId Block, SizeInBytes, GenTx Block)]
        -> [(GenTxId Block, SizeInBytes)]
    freshOf announced batch =
        [(tid, sz) | (tid, sz, _) <- batch, tid `Set.notMember` announced]

    absorb
        :: Map (GenTxId Block) (GenTx Block)
        -> [(GenTxId Block, SizeInBytes, GenTx Block)]
        -> Map (GenTxId Block) (GenTx Block)
    absorb = foldr (\(tid, _, tx) m -> Map.insert tid tx m)

    announce :: Set (GenTxId Block) -> [(GenTxId Block, SizeInBytes)] -> Set (GenTxId Block)
    announce = foldr (\(tid, _) s -> Set.insert tid s)
