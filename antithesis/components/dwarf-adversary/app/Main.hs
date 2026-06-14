{-# LANGUAGE NumericUnderscores #-}
{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE ScopedTypeVariables #-}

-- |
-- @dwarf-adversary@ — chain-sync UPSTREAM SERVER. A cardano-node dials
-- us as a chain-sync client; we accept, complete the N2N handshake as
-- responder, and serve headers whose CBOR is structurally mutated (via
-- the mutating codec) so the node runs its real header decoder on
-- adversarial input. Seeded solely by @--seed@ (from antithesis_random)
-- for deterministic recreation.
module Main (main) where

import Control.Concurrent (forkIO, threadDelay)
import Control.Concurrent.Class.MonadSTM.Strict (atomically, newTVarIO, readTVar, writeTVar)
import Control.Exception (SomeException, catch)
import Control.Monad (forever)
import Data.Aeson (object, (.=))
import Data.Word (Word32, Word64)
import DwarfAdversary (originPoint)
import DwarfAdversary.Application (Limit (..), adversaryApplication, runChainProducerInto)
import Ouroboros.Network.Mock.Chain qualified as Chain
import DwarfAdversary.BlockFetch.MutatingCodec
    ( describeBlockMutation
    , mutatingCodecBlockFetch
    )
import DwarfAdversary.BlockSource (captureChainTo, getBaseChain, loadBakedChain)
import DwarfAdversary.ChainSync.Codec (codecChainSync)
import DwarfAdversary.ChainSync.Connection
    ( blockFetchResponder
    , fetchBlock
    , onDemandBlockFetchResponder
    , plainBlockFetchCodec
    , plainTxSubmissionCodec
    , runAdversaryServerIR
    , runChainSyncServer
    , servingBlockFetchResponderMap
    )
import DwarfAdversary.TxSource (getBaseTxsFromChain)
import DwarfAdversary.TxSubmission.Client (txProviderClient)
import DwarfAdversary.TxSubmission.MutatingCodec
    ( describeTxMutation
    , mutatingCodecTxSubmission
    )
import DwarfAdversary.TxSubmission.Target (TxField (AuxData, Certificate, WholeTx))
import DwarfAdversary.ChainSync.MutatingCodec
    ( describeHeaderMutation
    , mutatingCodecChainSync
    )
import DwarfAdversary.ChainSync.Server (advancingChainSyncServer, chainSyncServer, tipFromHeaders)
import DwarfAdversary.Fuzz (MutationInfo (..))
import DwarfAdversary.HeaderSource (getBaseHeaders)
import DwarfAdversary.SDK qualified as SDK
import Ouroboros.Network.Block (blockPoint, castPoint)
import Network.Socket (PortNumber)
import Numeric (readHex)
import Options.Applicative
    ( Parser
    , auto
    , eitherReader
    , execParser
    , flag'
    , fullDesc
    , help
    , helper
    , info
    , long
    , metavar
    , option
    , optional
    , progDesc
    , str
    , value
    , (<**>)
    , (<|>)
    )
import Ouroboros.Network.Magic (NetworkMagic (..))
import System.IO
    ( BufferMode (LineBuffering)
    , hPutStrLn
    , hSetBuffering
    , stderr
    )

data Args = Args
    { argMagic :: Word32
    , argPort :: Int
    , argRate :: Double
    , argSeed :: Word64
    , argUpstream :: Maybe (String, Int)
    , argSelftest :: Bool
    , argProtocol :: String
    , argShape :: String
    , argCaptureTo :: Maybe FilePath
    , argBakedChain :: Maybe FilePath
    }

argsParser :: Parser Args
argsParser =
    Args
        <$> option
            auto
            ( long "network-magic"
                <> metavar "INT"
                <> value 42
                <> help "Network magic of the target cluster (default: 42)."
            )
        <*> option
            auto
            ( long "listen-port"
                <> metavar "PORT"
                <> value 3001
                <> help "N2N port to listen on (default: 3001)."
            )
        <*> option
            auto
            ( long "mutation-rate"
                <> metavar "DOUBLE"
                <> value 0.5
                <> help "Probability [0,1] a served header is mutated (default: 0.5; 0 = stock)."
            )
        <*> option
            (eitherReader parseSeed)
            ( long "seed"
                <> metavar "HEX-OR-DEC"
                <> value 0
                <> help "Sole RNG seed; source from $(antithesis_random). Hex (0x..) or decimal."
            )
        <*> optional
            ( option
                (eitherReader parseUpstream)
                ( long "upstream"
                    <> metavar "HOST:PORT"
                    <> help "In-bundle node to capture a base header from (NEVER external)."
                )
            )
        <*> ( flag'
                True
                ( long "selftest"
                    <> help
                        "Spawn the server then run our own client\
                        \ against it (proves handshake + protocol wiring)."
                )
                <|> pure False
            )
        <*> option
            str
            ( long "protocol"
                <> metavar "P"
                <> value "chainsync"
                <> help "Mini-protocol to fuzz: chainsync (default) | blockfetch."
            )
        <*> option
            str
            ( long "cbor-shape"
                <> metavar "S"
                <> value "block-header"
                <> help "Target CBOR shape: block-header (default) | block."
            )
        <*> optional
            ( option
                str
                ( long "capture-to"
                    <> metavar "FILE"
                    <> help "Capture blocks from --upstream, serialize to FILE, then exit (bake step)."
                )
            )
        <*> optional
            ( option
                str
                ( long "baked-chain"
                    <> metavar "FILE"
                    <> help "Serve a baked chain from FILE (no --upstream) — producer-less eclipse blockfetch."
                )
            )

parseSeed :: String -> Either String Word64
parseSeed s = case s of
    '0' : 'x' : hex -> case readHex hex of
        [(n, "")] -> Right n
        _ -> Left ("not a hex uint64: " <> s)
    _ -> case reads s of
        [(n :: Word64, "")] -> Right n
        _ -> Left ("not a uint64: " <> s)

parseUpstream :: String -> Either String (String, Int)
parseUpstream s = case break (== ':') s of
    (h, ':' : p) -> case reads p of
        [(n, "")] -> Right (h, n)
        _ -> Left ("bad port in --upstream: " <> s)
    _ -> Left ("expected HOST:PORT in --upstream: " <> s)

main :: IO ()
main = do
    hSetBuffering stderr LineBuffering
    args <- execParser opts
    let logMsg s = hPutStrLn stderr ("dwarf-adversary: " <> s)
        magic = NetworkMagic (argMagic args)
        port = fromIntegral (argPort args) :: PortNumber
    case argCaptureTo args of
        Just path -> case argUpstream args of
            Just hp -> captureChainTo logMsg magic hp 200 path
            Nothing -> error "--capture-to requires --upstream (the in-bundle node to capture from)"
        Nothing ->
            if argSelftest args
                then case argProtocol args of
                    "blockfetch" -> runBlockFetchSelftest logMsg magic port
                    "txsubmission" -> runTxSubmissionSelftest logMsg magic port
                    _ -> runSelftest logMsg magic port
                else case argProtocol args of
                    "blockfetch" -> case argBakedChain args of
                        Just bp -> runServeBakedBlockFetch logMsg args bp magic port
                        Nothing -> runServeBlockFetch logMsg args magic port
                    "txsubmission" -> runServeTxSubmission logMsg args magic port
                    _ -> runServe logMsg args magic port
  where
    opts =
        info
            (argsParser <**> helper)
            ( fullDesc
                <> progDesc
                    "Run a chain-sync upstream server that a cardano-node\
                    \ syncs from, serving structurally-mutated header CBOR."
            )

-- | Production path: capture a base header from the in-bundle upstream,
-- then serve (mutated) rollForwards forever.
runServe :: (String -> IO ()) -> Args -> NetworkMagic -> PortNumber -> IO ()
runServe logMsg args magic port = do
    SDK.reachable
        "dwarf_fuzz_server_started"
        ( object
            [ "port" .= argPort args
            , "seed" .= argSeed args
            , "mutation_rate" .= argRate args
            ]
        )
    headers <- case argUpstream args of
        Just hp -> getBaseHeaders logMsg magic hp 5
        Nothing -> do
            logMsg "no --upstream given: serving no headers (peering-only mode)"
            pure []
    SDK.sometimes
        (not (null headers))
        "dwarf_base_header_obtained"
        (object ["count" .= length headers])
    let tip = tipFromHeaders headers
        codec =
            if argRate args <= 0
                then codecChainSync
                else mutatingCodecChainSync (argSeed args) (argRate args)
        onServe h = do
            let inf = describeHeaderMutation (argSeed args) (argRate args) h
            SDK.sometimes
                True
                "dwarf_served_mutated_header"
                ( object
                    [ "kind" .= miKind inf
                    , "depth" .= miDepth inf
                    , "seed" .= argSeed args
                    ]
                )
    SDK.reachable "dwarf_fuzz_server_listening" (object ["port" .= argPort args])
    let onAccept peerAddr = do
            logMsg ("inbound connection accepted from " <> peerAddr)
            SDK.reachable "dwarf_node_connected" (object ["peer" .= peerAddr])
    _ <-
        runChainSyncServer
            magic
            port
            onAccept
            codec
            (chainSyncServer logMsg onServe True headers tip)
            plainBlockFetchCodec
            blockFetchResponder
    pure ()

-- | Selftest: prove the server completes the N2N handshake and a real
-- Ouroboros chain-sync client drives the protocol against it.
runSelftest :: (String -> IO ()) -> NetworkMagic -> PortNumber -> IO ()
runSelftest logMsg magic port = do
    logMsg "selftest: starting server"
    _ <-
        forkIO $ do
            _ <-
                runChainSyncServer
                    magic
                    port
                    (\p -> logMsg ("inbound connection accepted from " <> p))
                    codecChainSync
                    (chainSyncServer logMsg (\_ -> pure ()) True [] (tipFromHeaders []))
                    plainBlockFetchCodec
                    blockFetchResponder
            pure ()
    threadDelay 2_000_000
    logMsg "selftest: connecting our own client to 127.0.0.1"
    res <- adversaryApplication magic "127.0.0.1" port originPoint (Limit 5)
    logMsg $ "selftest: client result = " <> show res
    threadDelay 1_000_000

-- | Production block-fetch path: capture a real header (advertised
-- unmutated via chain-sync so the node requests the body) and a real
-- block (served structurally mutated via block-fetch, so the node runs
-- its real block-body decoder on adversarial bytes).
runServeBlockFetch :: (String -> IO ()) -> Args -> NetworkMagic -> PortNumber -> IO ()
runServeBlockFetch logMsg args magic port = do
    SDK.reachable
        "dwarf_block_fuzz_server_started"
        ( object
            [ "port" .= argPort args
            , "seed" .= argSeed args
            , "mutation_rate" .= argRate args
            , "shape" .= argShape args
            ]
        )
    hp <- case argUpstream args of
        Just hp -> pure hp
        Nothing -> error "block-fetch mode requires --upstream (in-bundle node)"
    -- ADVANCING block-fetch (eclipse-ready). A background producer continuously
    -- chain-syncs the upstream into chainVar (with keep-alive, so it is not
    -- reaped); the advancing chain-sync server serves those REAL headers
    -- (unmutated, plain codec) at a RECENT tip so the node adopts the chain and
    -- stays CaughtUp — even when the adversary is its SOLE peer (eclipse). Block
    -- BODIES are served on demand and fuzzed via the mutating block-fetch codec.
    -- (Replaces the old static getBaseChain capture, which froze at the initial
    -- chain — fine to prove the seam, but it served only the few captured
    -- blocks. Advancing serves a continuous stream as the chain grows.)
    chainVar <- newTVarIO Chain.Genesis
    _ <- forkIO $ forever $ do
        r <- runChainProducerInto chainVar magic (fst hp) (fromIntegral (snd hp))
        n <- Chain.length <$> atomically (readTVar chainVar)
        case r of
            Left e -> logMsg ("producer: chain-sync client ENDED (chainLen=" <> show n <> "): " <> show e)
            Right _ -> logMsg ("producer: chain-sync client returned cleanly (chainLen=" <> show n <> ")")
        threadDelay 1_000_000
    let csServer = advancingChainSyncServer logMsg (\_ -> pure ()) chainVar
        bfCodec = mutatingCodecBlockFetch (argSeed args) (argRate args)
        onServeBlk b = do
            let inf = describeBlockMutation (argSeed args) (argRate args) b
            SDK.sometimes
                True
                "dwarf_served_mutated_block"
                ( object
                    [ "kind" .= miKind inf
                    , "depth" .= miDepth inf
                    , "seed" .= argSeed args
                    ]
                )
        bfServer = onDemandBlockFetchResponder logMsg onServeBlk magic hp chainVar
        onAccept peerAddr = do
            logMsg ("inbound connection accepted from " <> peerAddr)
            SDK.reachable "dwarf_node_connected" (object ["peer" .= peerAddr])
    SDK.reachable
        "dwarf_block_decoder_reachable"
        (object ["seed" .= argSeed args, "shape" .= argShape args])
    -- Listen immediately; restart in-process on a mux exception (peer churn) —
    -- the node rejects mutated bodies and disconnects, which is expected.
    forever $ do
        (runChainSyncServer magic port onAccept codecChainSync csServer bfCodec bfServer >> pure ())
            `catch` \(e :: SomeException) -> do
                logMsg ("block server exception (restart): " <> show e)
                threadDelay 1_000_000
        threadDelay 2_000_000

-- | BAKED block-fetch (producer-less ECLIPSE). Serves a chain LOADED FROM FILE
-- (embedded in the bundle, captured by 'captureChainTo') instead of capturing
-- live from an upstream — so the bundle needs NO producers and the node under
-- test, having no other peer, is eclipsed by construction (no custom docker
-- network, which Antithesis rejects). Serves the REAL headers (valid → the node
-- bootstraps from origin) and MUTATED bodies (the block decoder runs on them).
-- The baked chain MUST be paired with the SAME genesis it was forged under
-- (the bundle ships that fixed genesis).
runServeBakedBlockFetch :: (String -> IO ()) -> Args -> FilePath -> NetworkMagic -> PortNumber -> IO ()
runServeBakedBlockFetch logMsg args path magic port = do
    SDK.reachable
        "dwarf_block_fuzz_server_started"
        ( object
            [ "port" .= argPort args
            , "seed" .= argSeed args
            , "mutation_rate" .= argRate args
            , "shape" .= argShape args
            , "baked" .= True
            ]
        )
    (headers, blockMap, orderedPts) <- loadBakedChain logMsg path
    SDK.sometimes
        (not (null headers))
        "dwarf_base_header_obtained"
        (object ["count" .= length headers])
    let tip = tipFromHeaders headers
        csServer = chainSyncServer logMsg (\_ -> pure ()) False headers tip
        bfCodec = mutatingCodecBlockFetch (argSeed args) (argRate args)
        onServeBlk b = do
            let inf = describeBlockMutation (argSeed args) (argRate args) b
            SDK.sometimes
                True
                "dwarf_served_mutated_block"
                ( object
                    [ "kind" .= miKind inf
                    , "depth" .= miDepth inf
                    , "seed" .= argSeed args
                    ]
                )
        bfServer = servingBlockFetchResponderMap onServeBlk blockMap orderedPts
        onAccept peerAddr = do
            logMsg ("inbound connection accepted from " <> peerAddr)
            SDK.reachable "dwarf_node_connected" (object ["peer" .= peerAddr])
    SDK.reachable
        "dwarf_block_decoder_reachable"
        (object ["seed" .= argSeed args, "shape" .= argShape args])
    forever $ do
        (runChainSyncServer magic port onAccept codecChainSync csServer bfCodec bfServer >> pure ())
            `catch` \(e :: SomeException) -> do
                logMsg ("baked block server exception (restart): " <> show e)
                threadDelay 1_000_000
        threadDelay 2_000_000

-- | Selftest for block-fetch mode: prove the combined responder completes
-- the N2N handshake and our own block-fetch client drives mini-protocol #3
-- against it (no-blocks responder — proves wiring; the mutated-block
-- serve+decode is proven on Antithesis with real in-bundle blocks).
runBlockFetchSelftest :: (String -> IO ()) -> NetworkMagic -> PortNumber -> IO ()
runBlockFetchSelftest logMsg magic port = do
    logMsg "selftest(blockfetch): starting server"
    _ <-
        forkIO $ do
            _ <-
                runChainSyncServer
                    magic
                    port
                    (\p -> logMsg ("inbound connection accepted from " <> p))
                    codecChainSync
                    (chainSyncServer logMsg (\_ -> pure ()) True [] (tipFromHeaders []))
                    plainBlockFetchCodec
                    blockFetchResponder
            pure ()
    threadDelay 2_000_000
    logMsg "selftest(blockfetch): connecting our own block-fetch client to 127.0.0.1"
    res <- fetchBlock magic "127.0.0.1" port (castPoint originPoint)
    let summary = case res of
            Left e -> "client error: " <> show e
            Right Nothing -> "no blocks served (wiring OK)"
            Right (Just _) -> "received a block (decoded OK)"
    logMsg ("selftest(blockfetch): " <> summary)
    threadDelay 1_000_000

-- | SP3b spike selftest: start the Initiator+Responder server (with the #4 tx
-- provider initiator registered) and connect a chain-sync client. Proves the IR
-- app binds + accepts + the responders serve under withServerNode (the gating
-- unknown). The full provider->consumer->decode flow is exercised in the
-- tx-submission selftest with a real captured tx (T6) and the live run.
runTxSubmissionSelftest :: (String -> IO ()) -> NetworkMagic -> PortNumber -> IO ()
runTxSubmissionSelftest logMsg magic port = do
    logMsg "selftest(txsubmission): starting IR server (initiator #4 provider registered)"
    let txCodec = plainTxSubmissionCodec
        -- lazy placeholders: forced only if a consumer requests txids/txs, which
        -- the chain-sync-only client below never does.
        -- empty batch + no-op onServe: the chain-sync-only client below never
        -- requests txids/txs, so the provider just parks.
        provider = txProviderClient logMsg (\_ -> pure ()) (pure [])
    _ <-
        forkIO $ do
            _ <-
                runAdversaryServerIR
                    magic
                    port
                    (\p -> logMsg ("inbound connection accepted from " <> p))
                    codecChainSync
                    (chainSyncServer logMsg (\_ -> pure ()) True [] (tipFromHeaders []))
                    plainBlockFetchCodec
                    blockFetchResponder
                    txCodec
                    provider
            pure ()
    threadDelay 2_000_000
    logMsg "selftest(txsubmission): connecting a chain-sync client (proves IR server binds + responds)"
    res <- adversaryApplication magic "127.0.0.1" port originPoint (Limit 5)
    logMsg ("selftest(txsubmission): client result = " <> show res)
    threadDelay 1_000_000

-- | Map the scenario's --cbor-shape to the targeted tx sub-field.
txFieldOfShape :: String -> TxField
txFieldOfShape "certificate" = Certificate
txFieldOfShape "auxiliary-data" = AuxData
txFieldOfShape _ = WholeTx

-- | Production tx-submission path: serve a real chain (so relay2 peers happily),
-- and OFFER a captured, sub-field-mutated transaction over tx-submission (#4 as
-- initiator). relay2's consumer requests + decodes the tx, running its real tx
-- decoder (and the targeted certificate / auxiliary-data sub-decoder) on the
-- mutated CBOR.
runServeTxSubmission :: (String -> IO ()) -> Args -> NetworkMagic -> PortNumber -> IO ()
runServeTxSubmission logMsg args magic port = do
    SDK.reachable
        "dwarf_tx_fuzz_server_started"
        ( object
            [ "port" .= argPort args
            , "seed" .= argSeed args
            , "mutation_rate" .= argRate args
            , "shape" .= argShape args
            ]
        )
    hp <- case argUpstream args of
        Just hp -> pure hp
        Nothing -> error "tx-submission mode requires --upstream (in-bundle node)"
    chainVar <- newTVarIO Chain.Genesis
    -- Producer: continuously sync the upstream chain into chainVar so the
    -- advancing chain-sync server presents a RECENT, advancing tip — the node
    -- then reaches + holds GSM CaughtUp, the precondition for it to run
    -- tx-submission with this peer (a fixed 5-header chain left the tip
    -- ancient/TooOld, so the node never requested txs).
    _ <- forkIO $ forever $ do
        r <- runChainProducerInto chainVar magic (fst hp) (fromIntegral (snd hp))
        n <- Chain.length <$> atomically (readTVar chainVar)
        case r of
            Left e -> logMsg ("producer: chain-sync client ENDED (chainLen=" <> show n <> "): " <> show e)
            Right _ -> logMsg ("producer: chain-sync client returned cleanly (chainLen=" <> show n <> ")")
        threadDelay 1_000_000
    let field = txFieldOfShape (argShape args)
        csServer = advancingChainSyncServer logMsg (\_ -> pure ()) chainVar
        txCodec = mutatingCodecTxSubmission field (argSeed args) (argRate args)
        -- per-serve assertion: fired each time the node actually pulls a tx
        -- (true serve signal, not a once-at-startup emit).
        onServeTx t = do
            let inf = describeTxMutation field (argSeed args) (argRate args) t
            SDK.sometimes
                True
                "dwarf_served_mutated_tx"
                ( object
                    [ "kind" .= miKind inf
                    , "depth" .= miDepth inf
                    , "shape" .= argShape args
                    , "seed" .= argSeed args
                    ]
                )
        onAccept peerAddr = do
            logMsg ("inbound connection accepted from " <> peerAddr)
            SDK.reachable "dwarf_node_connected" (object ["peer" .= peerAddr])
    SDK.reachable
        "dwarf_tx_decoder_reachable"
        (object ["seed" .= argSeed args, "shape" .= argShape args])
    -- CONTINUOUS tx refresh. A background thread re-captures the recent txs from
    -- the synced chain into txsVar every few seconds. The tx provider reads
    -- txsVar live (cheap, no network in the protocol hot path) and announces
    -- each fresh txid once — so as the tx-generator's txs land in new blocks the
    -- adversary keeps serving NEW mutated txs, instead of capturing one batch at
    -- startup (empty before any tx lands) and never refreshing.
    txsVar <- newTVarIO []
    _ <- forkIO $ forever $ do
        batch <- getBaseTxsFromChain logMsg chainVar magic hp 10
        atomically (writeTVar txsVar batch)
        SDK.sometimes
            (not (null batch))
            "dwarf_base_tx_obtained"
            (object ["count" .= length batch])
        threadDelay 8_000_000
    -- LISTEN IMMEDIATELY — do NOT gate the server on the producer having synced
    -- a chain. Under approach B the downstream node reaches GSM CaughtUp via the
    -- real producers, so it dials us (a trustable local root) from t=0; if we are
    -- not yet listening those early dials are refused and the node backs the peer
    -- off (it then never connects within the run). The advancing chain-sync
    -- server parks in await on an empty chainVar and starts rolling the node
    -- forward once the producer fills it; the tx provider blocks on an empty
    -- batch until the refresher supplies one. A mux exception (peer churn)
    -- restarts the server in process (never exits 1).
    let fetchBatch = atomically (readTVar txsVar)
        provider = txProviderClient logMsg onServeTx fetchBatch
        -- Serve REAL block bodies on demand (tx mode fuzzes the tx channel, not
        -- blocks) so the node can block-fetch from us too.
        bfServer = onDemandBlockFetchResponder logMsg (\_ -> pure ()) magic hp chainVar
        runServer =
            runAdversaryServerIR
                magic
                port
                onAccept
                codecChainSync
                csServer
                plainBlockFetchCodec
                bfServer
                txCodec
                provider
    forever $ do
        chainLen <- Chain.length <$> atomically (readTVar chainVar)
        SDK.sometimes
            (chainLen > 0)
            "dwarf_base_header_obtained"
            (object ["count" .= chainLen])
        (runServer >> pure ())
            `catch` \(e :: SomeException) -> do
                logMsg ("tx server exception (restart): " <> show e)
                threadDelay 1_000_000
        threadDelay 2_000_000
