{-# LANGUAGE NumericUnderscores #-}
{-# LANGUAGE OverloadedStrings #-}

-- |
-- Module: DwarfAdversary.BlockSource
--
-- Obtain one decodable base 'Block' to mutate and serve. Hermetic: the
-- block is captured from a node that is part of /this/ bundle's compose
-- (chain-sync a header to learn a point, then block-fetch that point's
-- body), never from an external\/public node. Antithesis has no network
-- during a run, so a runtime fetch must stay inside the sealed
-- environment. Mirrors 'DwarfAdversary.HeaderSource'.
module DwarfAdversary.BlockSource
    ( getBaseBlock
    , getBaseChain
    , captureChainTo
    , loadBakedChain
    ) where

import Codec.CBOR.Decoding (decodeListLen)
import Codec.CBOR.Encoding (encodeListLen)
import Codec.CBOR.Read (deserialiseFromBytes)
import Codec.CBOR.Write (toLazyByteString)
import Control.Concurrent (threadDelay)
import Control.Monad (forM, replicateM)
import Data.ByteString.Lazy qualified as LBS
import Data.Map.Strict (Map)
import Data.Map.Strict qualified as Map
import DwarfAdversary (originPoint)
import DwarfAdversary.Application (Limit (..), syncHeaders)
import DwarfAdversary.ChainSync.Codec (Block, Header, decBlock, encBlock)
import DwarfAdversary.ChainSync.Connection (fetchBlock)
import DwarfAdversary.HeaderSource (getBaseHeaders)
import Ouroboros.Consensus.Block (getHeader)
import Ouroboros.Network.Block (blockPoint, castPoint)
import Ouroboros.Network.Block qualified as Network
import Ouroboros.Network.Magic (NetworkMagic)

-- | Capture one base 'Block' from the in-bundle node at @(host, port)@:
-- chain-sync a few headers from origin, take the newest, then block-fetch
-- its body. Retries while the node is still starting / has no blocks yet.
-- Errors after exhausting retries (never returns a placeholder block).
getBaseBlock
    :: (String -> IO ())
    -- ^ logger
    -> NetworkMagic
    -> (String, Int)
    -- ^ in-bundle (host, port) to capture from
    -> IO Block
getBaseBlock log_ magic (host, port) = go (40 :: Int)
  where
    go :: Int -> IO Block
    go 0 = error "BlockSource: gave up capturing a base block from upstream"
    go n = do
        hs <- syncHeaders magic host (fromIntegral port) originPoint (Limit 5)
        case hs of
            Right headers@(_ : _) -> do
                let h = last headers :: Header
                    point = castPoint (blockPoint h)
                r <- fetchBlock magic host (fromIntegral port) point
                case r of
                    Right (Just b) -> do
                        log_ ("captured 1 base block from " <> host)
                        pure b
                    Right Nothing -> retry n "node served no block for the point yet"
                    Left e -> retry n ("body fetch failed: " <> show e)
            Right _ -> retry n "empty chain (node has no blocks yet)"
            Left e -> retry n ("header capture failed: " <> show e)

    retry :: Int -> String -> IO Block
    retry n why = do
        log_ ("BlockSource retry: " <> why)
        threadDelay 1_500_000
        go (n - 1)

-- | Capture the first @want@ headers from genesis AND each one's block body,
-- returning the ordered headers plus a point->block map keyed by the
-- block-fetch point. Bodies are fetched once at startup via the block-fetch
-- client. Used by block-fetch mode to serve a long, adoptable, genesis-anchored
-- chain (so the node advances) and serve the CORRECT body per requested point.
getBaseChain
    :: (String -> IO ())
    -> NetworkMagic
    -> (String, Int)
    -> Int
    -> IO ([Header], Map (Network.Point Block) Block)
getBaseChain log_ magic (host, port) want = do
    hs <- getBaseHeaders log_ magic (host, port) want
    pairs <- fmap concat $ forM hs $ \h -> do
        let pt = castPoint (blockPoint h)
        r <- fetchBlock magic host (fromIntegral port) pt
        case r of
            Right (Just b) -> pure [(pt, b)]
            _ -> log_ "getBaseChain: body fetch miss for a header" >> pure []
    log_
        ( "getBaseChain: "
            <> show (length hs)
            <> " headers, "
            <> show (length pairs)
            <> " bodies"
        )
    pure (hs, Map.fromList pairs)

-- | Capture @want@ blocks from the in-bundle node and SERIALIZE them
-- (oldest-first, CBOR list of full blocks) to @path@. Run once against a live
-- testnet to produce the embedded ("baked") chain that the producer-less
-- eclipse bundle ships — the node under test then bootstraps from this chain
-- with no live producer. The captured chain MUST be paired with the SAME
-- genesis it was forged under (capture both from one testnet incarnation;
-- systemStart='now' makes genesis non-reproducible across incarnations).
captureChainTo
    :: (String -> IO ())
    -> NetworkMagic
    -> (String, Int)
    -> Int
    -> FilePath
    -> IO ()
captureChainTo log_ magic hp want path = do
    (hs, blkMap) <- getBaseChain log_ magic hp want
    let ordered =
            [b | h <- hs, Just b <- [Map.lookup (castPoint (blockPoint h)) blkMap]]
        enc = encodeListLen (fromIntegral (length ordered)) <> foldMap encBlock ordered
    LBS.writeFile path (toLazyByteString enc)
    log_ ("captureChainTo: wrote " <> show (length ordered) <> " blocks to " <> path)

-- | Load a baked chain serialized by 'captureChainTo'. Returns the headers
-- (derived from the blocks, oldest-first), a point->block map, and the ordered
-- points — exactly the shape 'chainSyncServer' + 'servingBlockFetchResponderMap'
-- consume. No network: the chain travels with the bundle.
loadBakedChain
    :: (String -> IO ())
    -> FilePath
    -> IO ([Header], Map (Network.Point Block) Block, [Network.Point Block])
loadBakedChain log_ path = do
    bs <- LBS.readFile path
    case deserialiseFromBytes decChain bs of
        Left e -> error ("loadBakedChain: decode failed: " <> show e)
        Right (_rest, blocks) -> do
            let headers = map getHeader blocks
                pts = map (castPoint . blockPoint) headers
                m = Map.fromList (zip pts blocks)
            log_ ("loadBakedChain: loaded " <> show (length blocks) <> " baked blocks")
            pure (headers, m, pts)
  where
    decChain = do
        n <- decodeListLen
        replicateM n decBlock
