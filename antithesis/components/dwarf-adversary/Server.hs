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
    , tipFromHeaders
    ) where

import Control.Concurrent (threadDelay)
import Control.Monad (forever)
import DwarfAdversary.ChainSync.Codec (Header, Point, Tip)
import Ouroboros.Network.Block
    ( HeaderFields (..)
    , Tip (Tip, TipGenesis)
    , getHeaderFields
    )
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
    -> [Header]
    -> Tip
    -> ChainSyncServer Header Point Tip IO ()
chainSyncServer log_ onServe headers tip =
    ChainSyncServer (pure (idle (stream headers)))
  where
    -- Infinite stream of headers to serve (cycle the captured list), or
    -- empty if we captured nothing.
    stream [] = []
    stream hs = cycle hs

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
            , recvMsgFindIntersect = \_points -> do
                log_ "chain-sync: node sent MsgFindIntersect"
                pure
                    ( SendMsgIntersectNotFound
                        tip
                        (ChainSyncServer (pure (idle hs)))
                    )
            , recvMsgDoneClient = do
                log_ "chain-sync: node sent MsgDone"
                pure ()
            }

-- | Build the advertised tip from the captured headers (the last one),
-- or genesis if none were captured.
tipFromHeaders :: [Header] -> Tip
tipFromHeaders [] = TipGenesis
tipFromHeaders hs =
    let HeaderFields slot bno hash = getHeaderFields (last hs)
    in  Tip slot hash bno
