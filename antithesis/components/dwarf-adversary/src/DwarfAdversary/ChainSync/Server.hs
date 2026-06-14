{-# LANGUAGE NumericUnderscores #-}
{-# LANGUAGE OverloadedStrings #-}

-- |
-- Module: DwarfAdversary.ChainSync.Server
--
-- A chain-sync /server/ (the upstream role): the cardano-node dials us
-- as a chain-sync client and we feed it 'SendMsgRollForward' for each
-- header we hold. Because the node decodes every header we serve, this
-- is the seam at which fuzzed header CBOR (via the mutating codec)
-- reaches the node's real header decoder.
module DwarfAdversary.ChainSync.Server
    ( chainSyncServer
    , advancingChainSyncServer
    , tipFromHeaders
    ) where

import Control.Concurrent (threadDelay)
import Control.Concurrent.Class.MonadSTM.Strict (MonadSTM (..), StrictTVar, readTVar)
import Control.Monad (forever)
import DwarfAdversary.ChainSync.Codec (Header, Point, Tip)
import Ouroboros.Consensus.Block (headerPoint)
import Ouroboros.Network.Block (HeaderFields (..), castPoint, getHeaderFields)
import Ouroboros.Network.Block qualified as Net
import Ouroboros.Network.Mock.Chain (Chain)
import Ouroboros.Network.Mock.Chain qualified as Chain
import Ouroboros.Network.Protocol.ChainSync.Server
    ( ChainSyncServer (..)
    , ServerStIdle (..)
    , ServerStIntersect (..)
    , ServerStNext (..)
    )

-- | Serve @headers@ in order via rollForward, advertising @tip@.
--
-- @log_@ receives a line each time the node drives the protocol
-- (FindIntersect / RequestNext) — evidence a node actually peered with
-- us. @onServe@ is invoked with each header just before it is sent, so
-- the caller can emit a matching SDK assertion. After the list is
-- exhausted the server cycles back to the start (so a single-header
-- capture still produces a continuous stream of served — and, via the
-- mutating codec, freshly-mutated — frames). @headers@ being empty is
-- supported (spike: prove peering without serving any header).
chainSyncServer
    :: (String -> IO ())
    -> (Header -> IO ())
    -> Bool
    -- ^ cyclic: True = cycle the captured headers forever (header-fuzz mode —
    --   maximum decode coverage; the node decodes each on receipt). False =
    --   serve the headers once in order then park in await (block/tx modes —
    --   a stable, adoptable chain so the node can advance + block-fetch).
    -> [Header]
    -> Tip
    -> ChainSyncServer Header Point Tip IO ()
chainSyncServer log_ onServe cyclic headers tip =
    ChainSyncServer (pure (idle (stream headers)))
  where
    -- Header stream: cycle (header-fuzz) or serve-once (block/tx). Empty if we
    -- captured nothing. After a non-cyclic list is exhausted the RequestNext
    -- handler hits the [] branch and parks (SendMsgAwaitReply-equivalent).
    stream [] = []
    stream hs = if cyclic then cycle hs else hs

    idle :: [Header] -> ServerStIdle Header Point Tip IO ()
    idle hs =
        ServerStIdle
            { recvMsgRequestNext = do
                log_ "chain-sync: node sent MsgRequestNext"
                case hs of
                    (h : rest) -> do
                        onServe h
                        pure
                            ( Left
                                ( SendMsgRollForward
                                    h
                                    tip
                                    (ChainSyncServer (pure (idle rest)))
                                )
                            )
                    [] ->
                        -- nothing to serve: park in await (never resolves)
                        pure (Right (forever (threadDelay 1_000_000)))
            , recvMsgFindIntersect = \points -> do
                log_ "chain-sync: node sent MsgFindIntersect"
                -- Claim intersection at the first point the client
                -- offered, so it sets its read pointer and proceeds to
                -- MsgRequestNext (rather than concluding we share no
                -- chain and sending MsgDone). We then serve our
                -- (mutated) headers, which the node decodes.
                case points of
                    (p : _) ->
                        pure
                            ( SendMsgIntersectFound
                                p
                                tip
                                (ChainSyncServer (pure (idle hs)))
                            )
                    [] ->
                        pure
                            ( SendMsgIntersectNotFound
                                tip
                                (ChainSyncServer (pure (idle hs)))
                            )
            , recvMsgDoneClient = do
                log_ "chain-sync: node sent MsgDone"
                pure ()
            }

-- | An ADVANCING chain-sync server: reads a shared, continuously-growing
-- 'Chain' (filled by 'runChainProducerInto') and rolls forward to the
-- downstream node as the chain extends, always advertising @Chain.headTip@ as
-- the tip. Because the served tip tracks the upstream's RECENT tip, the node
-- reaches and holds GSM CaughtUp (vs the static 'chainSyncServer', whose
-- ancient fixed tip leaves the node 'TooOld' forever). Headers are still
-- mutated on the codec encode path; @onServe@ fires per header rolled forward.
advancingChainSyncServer
    :: (String -> IO ())
    -> (Header -> IO ())
    -> StrictTVar IO (Chain Header)
    -> ChainSyncServer Header Point Tip IO ()
advancingChainSyncServer log_ onServe chainVar =
    ChainSyncServer (pure (idle [Net.genesisPoint]))
  where
    -- The mock 'Chain Header' is keyed by @Point Header@/@Tip Header@, but the
    -- chain-sync protocol uses @Point@/@Tip@ over the block (nominally distinct
    -- types, equal HeaderHash). Bridge with 'castPoint' for points and reuse
    -- 'tipFromHeaders' (which builds a @Tip@ over the block) for the tip.
    chainTip :: Chain Header -> Tip
    chainTip = tipFromHeaders . Chain.toOldestFirst

    -- Is point @p@ on the current chain @c@?
    onChain :: Chain Header -> Point -> Bool
    onChain c p = Chain.pointOnChain (castPoint p) c

    -- Bounded history of points we have served the node, most-recent-first,
    -- always ending with genesis. Lets us roll back to the common ancestor on a
    -- reorg (the node never rolls back deeper than securityParam ~ 432/2160).
    servedCap :: Int
    servedCap = 2200

    push :: Point -> [Point] -> [Point]
    push p served = take servedCap (p : served)

    -- The producer's chainVar can REORG (p1 switches fork). The chain-sync
    -- server must then send MsgRollBackward to the latest served point still on
    -- the chain (else the node receives a fork header that fails Praos VRF
    -- validation and disconnects). On no reorg, roll forward as usual.
    idle :: [Point] -> ServerStIdle Header Point Tip IO ()
    idle [] = idle [Net.genesisPoint]
    idle served@(readPtr : _) =
        ServerStIdle
            { recvMsgRequestNext = do
                c <- atomically (readTVar chainVar)
                if not (onChain c readPtr)
                    then do
                        let served' = case dropWhile (not . onChain c) served of
                                [] -> [Net.genesisPoint]
                                ps -> ps
                        log_ ("chain-sync(advancing): reorg; RollBackward to " <> show (head served'))
                        pure
                            ( Left
                                ( SendMsgRollBackward
                                    (head served')
                                    (chainTip c)
                                    (ChainSyncServer (pure (idle served')))
                                )
                            )
                    else case Chain.successorBlock (castPoint readPtr) c of
                        Just h -> do
                            onServe h
                            logServed "RequestNext" h c
                            pure
                                ( Left
                                    ( SendMsgRollForward
                                        h
                                        (chainTip c)
                                        (ChainSyncServer (pure (idle (push (castPoint (headerPoint h)) served))))
                                    )
                                )
                        Nothing ->
                            -- caught up to our tip: await until the producer
                            -- extends (or reorgs) the chain, then act.
                            pure (Right (awaitNext served))
            , recvMsgFindIntersect = \points -> do
                log_ "chain-sync(advancing): node sent MsgFindIntersect"
                c <- atomically (readTVar chainVar)
                case Chain.findFirstPoint (map castPoint points) c of
                    Just p ->
                        pure
                            ( SendMsgIntersectFound
                                (castPoint p)
                                (chainTip c)
                                (ChainSyncServer (pure (idle [castPoint p, Net.genesisPoint])))
                            )
                    Nothing ->
                        pure
                            ( SendMsgIntersectNotFound
                                (chainTip c)
                                (ChainSyncServer (pure (idle served)))
                            )
            , recvMsgDoneClient = do
                log_ "chain-sync(advancing): node sent MsgDone"
                pure ()
            }

    awaitNext :: [Point] -> IO (ServerStNext Header Point Tip IO ())
    awaitNext served@(readPtr : _) = do
        -- block until the chain either extends past readPtr (roll forward) or
        -- reorgs away from readPtr (roll back).
        res <- atomically $ do
            c <- readTVar chainVar
            if not (onChain c readPtr)
                then pure (Left c)
                else case Chain.successorBlock (castPoint readPtr) c of
                    Just h' -> pure (Right h')
                    Nothing -> retry
        case res of
            Right h -> do
                onServe h
                c <- atomically (readTVar chainVar)
                logServed "await" h c
                pure
                    ( SendMsgRollForward
                        h
                        (chainTip c)
                        (ChainSyncServer (pure (idle (push (castPoint (headerPoint h)) served))))
                    )
            Left c -> do
                let served' = case dropWhile (not . onChain c) served of
                        [] -> [Net.genesisPoint]
                        ps -> ps
                log_ ("chain-sync(advancing): reorg during await; RollBackward to " <> show (head served'))
                pure
                    ( SendMsgRollBackward
                        (head served')
                        (chainTip c)
                        (ChainSyncServer (pure (idle served')))
                    )
    awaitNext [] = awaitNext [Net.genesisPoint]

    -- diagnostic: how far relay2 syncs (served header slot/blockNo vs our tip)
    logServed :: String -> Header -> Chain Header -> IO ()
    logServed via h c =
        let HeaderFields s b _ = getHeaderFields h
            tipNo = case chainTip c of Net.Tip _ _ bn -> show bn; _ -> "origin"
        in log_
            ( "chain-sync(advancing): RollForward("
                <> via
                <> ") slot="
                <> show s
                <> " blockNo="
                <> show b
                <> " ourTipBlockNo="
                <> tipNo
            )

-- | Build the advertised tip from the captured headers (the last one),
-- or genesis if none were captured.
tipFromHeaders :: [Header] -> Tip
tipFromHeaders [] = Net.TipGenesis
tipFromHeaders hs =
    let HeaderFields slot bno hash = getHeaderFields (last hs)
    in  Net.Tip slot hash bno
