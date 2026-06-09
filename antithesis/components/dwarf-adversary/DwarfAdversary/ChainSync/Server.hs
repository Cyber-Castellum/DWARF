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
--
-- For the spike this serves a captured base chain unmutated; the
-- mutating codec (DwarfAdversary.ChainSync.MutatingCodec) swaps in on
-- the wire without changing this logic.
module DwarfAdversary.ChainSync.Server
    ( chainSyncServer
    ) where

import Control.Concurrent (threadDelay)
import Control.Monad (forever)
import DwarfAdversary.ChainSync.Codec (Header, Point, Tip)
import Ouroboros.Network.Protocol.ChainSync.Server
    ( ChainSyncServer (..)
    , ServerStIdle (..)
    , ServerStIntersect (..)
    , ServerStNext (..)
    )

-- | Serve @headers@ in order via rollForward, advertising @tip@. After
-- the list is exhausted the server parks in await-reply (a never-
-- resolving @Right@) so the connection stays open without erroring —
-- the node has by then decoded every served header.
--
-- @headers@ must be non-empty (the caller — HeaderSource — guarantees
-- at least one decodable base header).
chainSyncServer :: [Header] -> Tip -> ChainSyncServer Header Point Tip IO ()
chainSyncServer headers tip = ChainSyncServer (pure (idle headers))
  where
    idle :: [Header] -> ServerStIdle Header Point Tip IO ()
    idle hs =
        ServerStIdle
            { recvMsgRequestNext = pure (next hs)
            , recvMsgFindIntersect = \_points ->
                -- We don't track a real producer chain: report no
                -- intersection and let the client sync from our
                -- rollForwards starting at genesis.
                pure (SendMsgIntersectNotFound tip (ChainSyncServer (pure (idle hs))))
            , recvMsgDoneClient = pure ()
            }

    -- 'Left' = a next message is immediately available; 'Right' = the
    -- server must await before it can send (we never resolve it).
    next
        :: [Header]
        -> Either
            (ServerStNext Header Point Tip IO ())
            (IO (ServerStNext Header Point Tip IO ()))
    next (h : hs) =
        Left (SendMsgRollForward h tip (ChainSyncServer (pure (idle hs))))
    next [] =
        Right (forever (threadDelay 1_000_000))
