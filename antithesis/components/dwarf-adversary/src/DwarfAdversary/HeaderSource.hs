{-# LANGUAGE NumericUnderscores #-}
{-# LANGUAGE OverloadedStrings #-}

-- |
-- Module: DwarfAdversary.HeaderSource
--
-- Obtain a decodable base 'Header' to mutate and serve. Hermetic: the
-- header is captured from a node that is part of /this/ bundle's
-- compose (an in-environment chain-sync at startup), never from an
-- external\/public node. Antithesis has no network during a run, so a
-- runtime fetch must stay inside the sealed environment.
module DwarfAdversary.HeaderSource
    ( getBaseHeaders
    ) where

import Control.Concurrent (threadDelay)
import DwarfAdversary (originPoint)
import DwarfAdversary.Application (Limit (..), syncHeaders)
import DwarfAdversary.ChainSync.Codec (Header)
import Ouroboros.Network.Magic (NetworkMagic)

-- | Capture @want@ base headers from the in-bundle node at @(host,
-- port)@ by chain-syncing as a client from origin. Retries while the
-- node is still starting up / has not produced its first blocks.
-- Returns a non-empty list, or errors after exhausting retries.
getBaseHeaders
    :: (String -> IO ())
    -- ^ logger
    -> NetworkMagic
    -> (String, Int)
    -- ^ in-bundle (host, port) to capture from
    -> Int
    -- ^ how many headers to try to capture
    -> IO [Header]
getBaseHeaders log_ magic (host, port) want = go (40 :: Int)
  where
    go :: Int -> IO [Header]
    go 0 = error "HeaderSource: gave up capturing a base header from upstream"
    go n = do
        r <-
            syncHeaders
                magic
                host
                (fromIntegral port)
                originPoint
                (Limit (fromIntegral want))
        case r of
            Right hs
                | not (null hs) -> do
                    log_ $
                        "captured "
                            <> show (length hs)
                            <> " base header(s) from "
                            <> host
                    pure hs
            Right _ -> retry n "empty chain (node has no blocks yet)"
            Left e -> retry n ("capture failed: " <> show e)

    retry :: Int -> String -> IO [Header]
    retry n why = do
        log_ $ "header capture retry (" <> show (n - 1) <> " left): " <> why
        threadDelay 3_000_000
        go (n - 1)
