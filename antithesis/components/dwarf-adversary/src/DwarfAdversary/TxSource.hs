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
    ) where

import Codec.CBOR.Write (toLazyByteString)
import Control.Concurrent (threadDelay)
import Control.Concurrent.Class.MonadSTM.Strict (StrictTVar, atomically, readTVar)
import Control.Monad (forM)
import Data.ByteString.Lazy qualified as LBS
import Data.Map.Strict qualified as Map
import DwarfAdversary.BlockSource (getBaseBlock, getBaseChain)
import DwarfAdversary.ChainSync.Codec (Block, GenTx, GenTxId, Header, encTx)
import DwarfAdversary.ChainSync.Connection (fetchBlock)
import Ouroboros.Consensus.Block (headerPoint)
import Ouroboros.Consensus.Ledger.SupportsMempool (extractTxs, txId)
import Ouroboros.Network.Block (castPoint)
import Ouroboros.Network.Magic (NetworkMagic)
import Ouroboros.Network.Mock.Chain (Chain)
import Ouroboros.Network.Mock.Chain qualified as Chain
import Ouroboros.Network.Protocol.TxSubmission2.Type (SizeInBytes (SizeInBytes))

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
