{-# LANGUAGE NumericUnderscores #-}
{-# LANGUAGE OverloadedStrings #-}

-- |
-- Module: DwarfAdversary.TxSource
--
-- Capture one real transaction to mutate + offer over tx-submission. Hermetic:
-- reuses 'DwarfAdversary.BlockSource.getBaseBlock' to grab a real block from an
-- in-bundle node, then extracts one transaction from it (consensus
-- 'extractTxs'). The transaction's CBOR is mutated on the codec encode path
-- (DwarfAdversary.TxSubmission.* mutating codec), so this returns the real
-- 'GenTx'; the bytes are perturbed on the wire.
module DwarfAdversary.TxSource
    ( getBaseTx
    , getBaseTxs
    , getBaseTxsFromChain
    , loadSeedTxs
    , harvestTxs
    ) where

import Codec.CBOR.Read (deserialiseFromBytes)
import Codec.CBOR.Write (toLazyByteString)
import Control.Concurrent (threadDelay)
import Control.Concurrent.Class.MonadSTM.Strict (StrictTVar, atomically, readTVar)
import Control.Exception (SomeException, try)
import Control.Monad (forM, forM_, unless)
import Data.Bits (xor)
import Data.ByteString.Lazy qualified as LBS
import Data.Map.Strict qualified as Map
import Data.Word (Word64)
import DwarfAdversary.BlockSource (getBaseBlock, getBaseChain)
import DwarfAdversary.ChainSync.Codec (Block, GenTx, GenTxId, Header, decTx, encTx)
import DwarfAdversary.ChainSync.Connection (fetchBlock)
import Numeric (showHex)
import Ouroboros.Consensus.Block (headerPoint)
import Ouroboros.Consensus.Ledger.SupportsMempool (extractTxs, txId)
import Ouroboros.Network.Block (castPoint)
import Ouroboros.Network.Magic (NetworkMagic)
import Ouroboros.Network.Mock.Chain (Chain)
import Ouroboros.Network.Mock.Chain qualified as Chain
import Ouroboros.Network.Protocol.TxSubmission2.Type (SizeInBytes (SizeInBytes))
import System.Directory (createDirectoryIfMissing, doesFileExist)
import System.FilePath ((</>))

-- | Capture one real transaction (txid + on-wire size + body) from the
-- in-bundle node at @(host, port)@: grab a block, extract its first tx. Retries
-- while the captured block has no transactions (idle chain). Errors after
-- exhausting retries; never returns a placeholder.
getBaseTx
    :: (String -> IO ())
    -> NetworkMagic
    -> (String, Int)
    -> IO (GenTxId Block, SizeInBytes, GenTx Block)
getBaseTx log_ magic hp = go (40 :: Int)
  where
    go :: Int -> IO (GenTxId Block, SizeInBytes, GenTx Block)
    go 0 = error "TxSource: gave up capturing a tx (no block with transactions)"
    go n = do
        blk <- getBaseBlock log_ magic hp
        case extractTxs blk of
            (tx : _) -> do
                let txid = txId tx
                    size = SizeInBytes (fromIntegral (LBS.length (toLazyByteString (encTx tx))))
                log_ "TxSource: captured 1 tx from an in-bundle block"
                pure (txid, size, tx)
            [] -> do
                log_ "TxSource: captured block had no transactions; retrying"
                threadDelay 2_000_000
                go (n - 1)

-- | Capture up to @want@ real transactions from the in-bundle chain's blocks.
-- Reuses 'getBaseChain' (headers + point->block map), flattens 'extractTxs' over
-- the captured blocks, and returns the first @want@ as (txid, on-wire size, tx).
-- Used by the resilient tx-submission provider to serve a batch (so the
-- adversary fuzzes several txs from one long-lived process).
--
-- RETRIES with a GROWING scan depth: on a freshly-started bundle the
-- tx-generator may not have landed any transactions in the first @chainLen@
-- blocks yet (early blocks are empty). Each retry waits, then scans deeper —
-- both to let the chain grow and to reach blocks where txs have appeared. If
-- no tx is found after the retry budget, it logs loudly and returns [] rather
-- than erroring: an empty batch makes the provider park (stay alive), which
-- preserves the no-crash property — better than a startup crash-loop.
getBaseTxs
    :: (String -> IO ())
    -> NetworkMagic
    -> (String, Int)
    -> Int
    -- ^ starting number of chain blocks to scan (grows on retry)
    -> Int
    -- ^ max txs to return
    -> IO [(GenTxId Block, SizeInBytes, GenTx Block)]
getBaseTxs log_ magic hp chainLen want = go (8 :: Int) chainLen
  where
    depthCap = 1000

    go :: Int -> Int -> IO [(GenTxId Block, SizeInBytes, GenTx Block)]
    go 0 _ = do
        log_ "getBaseTxs: WARN no txs captured after retries; serving empty (adversary stays alive)"
        pure []
    go n depth = do
        (_headers, blocks) <- getBaseChain log_ magic hp depth
        let txs =
                take want
                    [ (txId t, SizeInBytes (fromIntegral (LBS.length (toLazyByteString (encTx t)))), t)
                    | b <- Map.elems blocks
                    , t <- extractTxs b
                    ]
        log_
            ( "getBaseTxs: "
                <> show (length txs)
                <> " txs from "
                <> show (Map.size blocks)
                <> " blocks (depth "
                <> show depth
                <> ")"
            )
        if not (null txs)
            then pure txs
            else do
                log_ "getBaseTxs: no txs in scanned range yet; waiting + scanning deeper"
                threadDelay 5_000_000
                go (n - 1) (min depthCap (depth * 2))

-- | Capture up to @want@ txs from the RECENT end of the producer's shared
-- chain (the same chain the advancing chain-sync server serves). Reads the
-- newest ~50 headers from @chainVar@, fetches each block body from upstream,
-- and flattens 'extractTxs'. Sharing the served chain as the capture source
-- keeps the offered txids consistent with the chain the node is syncing.
getBaseTxsFromChain
    :: (String -> IO ())
    -> StrictTVar IO (Chain Header)
    -> NetworkMagic
    -> (String, Int)
    -> Int
    -> IO [(GenTxId Block, SizeInBytes, GenTx Block)]
getBaseTxsFromChain log_ chainVar magic (host, port) want = do
    hdrs <- Chain.toOldestFirst <$> atomically (readTVar chainVar)
    let recent = take 50 (reverse hdrs) -- newest-first; prefer recent blocks
    blocks <- fmap concat $ forM recent $ \h -> do
        r <- fetchBlock magic host (fromIntegral port) (castPoint (headerPoint h))
        pure $ case r of
            Right (Just b) -> [b]
            _ -> []
    let txs =
            take want
                [ (txId t, SizeInBytes (fromIntegral (LBS.length (toLazyByteString (encTx t)))), t)
                | b <- blocks
                , t <- extractTxs b
                ]
    log_
        ( "getBaseTxsFromChain: "
            <> show (length txs)
            <> " txs from "
            <> show (length blocks)
            <> " recent blocks"
        )
    pure txs

-- | Load seed-corpus txs from wire-GenTx files (each holding exactly what
-- 'encTx' emits). Decoded via 'decTx', so a file that round-trips the node's
-- N2N tx codec is accepted; a bad file is logged and skipped. These are always
-- offered as base txs, letting sub-field targeting engage when the synced chain
-- has no matching tx (e.g. the hermetic Antithesis devnet — no cert/metadata).
loadSeedTxs
    :: (String -> IO ())
    -> [FilePath]
    -> IO [(GenTxId Block, SizeInBytes, GenTx Block)]
loadSeedTxs log_ files = fmap concat $ forM files $ \f -> do
    r <- try (LBS.readFile f) :: IO (Either SomeException LBS.ByteString)
    case r of
        Left e -> log_ ("loadSeedTxs: cannot read " <> f <> ": " <> show e) >> pure []
        Right bytes -> case deserialiseFromBytes decTx bytes of
            Right (rest, tx) | LBS.null rest -> do
                let size = SizeInBytes (fromIntegral (LBS.length (toLazyByteString (encTx tx))))
                log_ ("loadSeedTxs: loaded seed tx from " <> f)
                pure [(txId tx, size, tx)]
            Right _ -> log_ ("loadSeedTxs: trailing bytes in " <> f <> "; skipped") >> pure []
            Left e -> log_ ("loadSeedTxs: decode failed for " <> f <> ": " <> show e) >> pure []

-- | Dev tool: write each tx's wire bytes ('encTx') to @dir/cap-<fnv64hex>.cbor@,
-- deduped by content hash (skips files that already exist). Run against a live
-- cert/metadata-carrying devnet to harvest a seed corpus.
harvestTxs
    :: (String -> IO ())
    -> FilePath
    -> [(GenTxId Block, SizeInBytes, GenTx Block)]
    -> IO ()
harvestTxs log_ dir txs = do
    createDirectoryIfMissing True dir
    forM_ txs $ \(_, _, tx) -> do
        let bytes = toLazyByteString (encTx tx)
            path = dir </> ("cap-" <> showHex (fnv1a64 bytes) "" <> ".cbor")
        exists <- doesFileExist path
        unless exists $ do
            LBS.writeFile path bytes
            log_ ("harvestTxs: wrote " <> path <> " (" <> show (LBS.length bytes) <> " bytes)")

-- | FNV-1a 64-bit over the wire bytes — content-addressed harvest filenames.
fnv1a64 :: LBS.ByteString -> Word64
fnv1a64 = LBS.foldl' step 0xcbf29ce484222325
  where
    step acc b = (acc `xor` fromIntegral b) * 0x100000001b3
