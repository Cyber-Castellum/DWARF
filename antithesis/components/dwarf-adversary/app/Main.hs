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
import Control.Concurrent.Async (mapConcurrently_)
import Control.Concurrent.Class.MonadSTM.Strict (atomically, newTVarIO, readTVar, writeTVar)
import Control.Exception (SomeException, catch)
import Control.Monad (forever, void)
import Data.IORef (newIORef)
import Data.Aeson (object, (.=))
import Data.ByteString qualified as BS
import Data.Text qualified as T
import Data.Word (Word16, Word32, Word64)
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
    , runAdversaryServerSM
    , runChainSyncServer
    , runSMInitiatorOnce
    , servingBlockFetchResponderMap
    )
import DwarfAdversary.TxSource (getBaseTxsFromChain, harvestTxs, loadSeedTxs)
import DwarfAdversary.TxSubmission.Client (txProviderClient)
import DwarfAdversary.TxSubmission.MutatingCodec
    ( describeTxMutation
    , mutatingCodecTxSubmission
    )
import DwarfAdversary.TxSubmission.Target (TxField (AuxData, Certificate, GovAction, PlutusWitness, Witness, WholeTx))
import DwarfAdversary.ChainSync.MutatingCodec
    ( describeHeaderMutation
    , mutatingCodecChainSync
    )
import DwarfAdversary.ChainSync.Server (advancingChainSyncServer, chainSyncServer, deepRollbackChainSyncServer, tipFromHeaders)
import DwarfAdversary.Fuzz (MutationInfo (..), MutationLevel (..), parseMutationLevel)
import DwarfAdversary.HeaderSource (getBaseHeaders)
import DwarfAdversary.SDK qualified as SDK
import Ouroboros.Network.Block (blockPoint, castPoint)
import Network.Socket (PortNumber)
import Numeric (readHex, showHex)
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
    , many
    , metavar
    , option
    , optional
    , progDesc
    , str
    , switch
    , value
    , (<**>)
    , (<|>)
    )
import Ouroboros.Network.Magic (NetworkMagic (..))
import System.IO
    ( BufferMode (LineBuffering)
    , IOMode (ReadMode)
    , hPutStrLn
    , hSetBuffering
    , stderr
    , withBinaryFile
    )

data Args = Args
    { argMagic :: Word32
    , argPort :: Int
    , argRate :: Double
    , argSeedSpec :: String
    -- ^ Raw @--seed@ string: @random@/@auto@ (draw from entropy at launch — under
    -- Antithesis this is a per-timeline choice point) or an explicit hex/decimal seed.
    , argSeed :: Word64
    -- ^ Resolved RNG seed (set in 'main' from 'argSeedSpec'); NOT parsed directly.
    , argUpstream :: Maybe (String, Int)
    , argSelftest :: Bool
    , argStateMachine :: Bool
    , argProtocol :: String
    , argShape :: String
    , argLevel :: MutationLevel
    -- ^ struct (default) | bytes (malformed CBOR) | both | semantic | grammar
    , argCaptureTo :: Maybe FilePath
    , argBakedChain :: Maybe FilePath
    , argSeedTxFiles :: [FilePath]
    -- ^ Wire-GenTx files (what 'encTx' emits) always included as base txs to
    -- mutate, so sub-field targeting engages even when the synced chain carries
    -- no matching tx (e.g. the hermetic Antithesis devnet has no cert/metadata).
    , argHarvestTo :: Maybe FilePath
    -- ^ Dev tool: write each distinct captured tx's wire bytes here (to build a
    -- seed corpus from a live cert/metadata-carrying devnet).
    , argSmConnections :: Int
    -- ^ SP4 state-machine POOL: number of concurrent initiator workers
    -- (--sm-connections, default 64). Throughput ~ N / per-connection latency.
    , argSmServerMode :: Bool
    -- ^ SP4: use the legacy inbound responder server (the already-soaked slot-#2
    -- surface) instead of the default concurrent initiator pool.
    , argSmConnMs :: Int
    -- ^ SP4 pool: per-connection lifetime budget in milliseconds (--sm-conn-ms,
    -- default 750). After injecting we force-close at this budget instead of
    -- idling ~26s waiting for the node's connection-manager teardown.
    , argRollbackDepth :: Int
    -- ^ Long-range (Plan A): if > 0, once the eclipsed node has caught up, the
    -- advancing block-fetch server injects ONE deep RollBackward this many blocks
    -- behind the served tip (pick > k). A correct node must refuse and disconnect.
    , argRollbackMinTip :: Int
    -- ^ Long-range (Plan A): only inject the deep rollback once the served chain
    -- has reached this length/height (so it fires at the true honest tip). 0 = any.
    , argRollbackRepeatSecs :: Int
    -- ^ Long-range (Plan A) SOAK: if > 0, re-arm the one-shot injection every N
    -- seconds so the deep rollback is injected repeatedly (a durable differential
    -- soak). 0 = inject once.
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
            str
            ( long "seed"
                <> metavar "random|HEX-OR-DEC"
                <> value "random"
                <> help
                    "Sole RNG seed. 'random'/'auto' (default) draws fresh entropy at\
                    \ launch — under Antithesis this is a per-timeline choice point, so\
                    \ each timeline fuzzes from a different seed. The drawn value is\
                    \ logged for reproduction. Or pin an explicit seed: hex (0x..) or decimal."
            )
        <*> pure 0 -- argSeed: resolved in main from argSeedSpec
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
        <*> switch
                ( long "state-machine-fuzz"
                    <> help
                        "SP4 state-machine fuzz: serve ChainSync with scripted\
                        \ ILLEGAL message sequences (well-formed CBOR, illegal\
                        \ protocol state / agency) — exercises the node's\
                        \ mini-protocol state machine, not its decoder."
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
        <*> option
            (eitherReader (\s -> maybe (Left ("bad --mutation-level: " <> s)) Right (parseMutationLevel s)))
            ( long "mutation-level"
                <> metavar "L"
                <> value LevelStruct
                <> help
                    "Mutation layer: struct (default, valid CBOR / hostile structure) |\
                    \ bytes (malformed CBOR — truncate/flip/oversize-length/deep-nest/garbage,\
                    \ exercises the deserializer's error handling) | both |\
                    \ semantic (type-valid but rule-violating field values) |\
                    \ grammar (malform the mini-protocol MESSAGE frame itself —\
                    \ message-tag flip / array-arity bump / frame truncate / junk\
                    \ prepend-append / frame byte-flip — exercises the codec/mux\
                    \ envelope decoder, not just the payload)."
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
        <*> many
            ( option
                str
                ( long "seed-tx"
                    <> metavar "FILE"
                    <> help
                        ( "Wire GenTx bytes (as encTx emits) always offered as a base tx to "
                            <> "mutate. Repeatable. Lets --cbor-shape certificate/auxiliary-data "
                            <> "engage on a chain that carries no such tx."
                        )
                )
            )
        <*> optional
            ( option
                str
                ( long "harvest-to"
                    <> metavar "DIR"
                    <> help "Dev tool: write each distinct captured tx's wire bytes to DIR/cap-<fnv>.cbor."
                )
            )
        <*> option
            auto
            ( long "sm-connections"
                <> metavar "N"
                <> value 64
                <> help
                    "SP4 state-machine pool: number of concurrent initiator workers\
                    \ that dial --upstream and inject illegal sequences (default 64)."
            )
        <*> switch
            ( long "sm-server-mode"
                <> help
                    "SP4 state-machine fuzz: use the legacy inbound responder server\
                    \ (the already-soaked slot-#2 surface) instead of the default\
                    \ concurrent initiator pool."
            )
        <*> option
            auto
            ( long "sm-conn-ms"
                <> metavar "MS"
                <> value 750
                <> help
                    "SP4 state-machine pool: per-connection lifetime budget in\
                    \ milliseconds (default 750). The injector force-closes at this\
                    \ budget instead of idling for the node's connection-manager\
                    \ teardown."
            )
        <*> option
            auto
            ( long "rollback-depth"
                <> metavar "N"
                <> value 0
                <> help
                    "Long-range (Plan A): if > 0, once the eclipsed node has caught\
                    \ up, inject ONE deep RollBackward N blocks behind the served\
                    \ tip (pick N > k). A correct node refuses and disconnects;\
                    \ accepting it is a safety violation. Used with --protocol blockfetch."
            )
        <*> option
            auto
            ( long "rollback-min-tip"
                <> metavar "N"
                <> value 0
                <> help
                    "Long-range (Plan A): only inject the deep rollback once the\
                    \ served chain length reaches N (so it fires at the true honest\
                    \ tip, not during rebuild). 0 = fire as soon as caught up."
            )
        <*> option
            auto
            ( long "rollback-repeat-secs"
                <> metavar "N"
                <> value 0
                <> help
                    "Long-range (Plan A) SOAK: if > 0, re-arm the injection every N\
                    \ seconds so the deep rollback fires repeatedly (durable\
                    \ differential soak). 0 = inject once."
            )

parseSeed :: String -> Either String Word64
parseSeed s = case s of
    '0' : 'x' : hex -> case readHex hex of
        [(n, "")] -> Right n
        _ -> Left ("not a hex uint64: " <> s)
    _ -> case reads s of
        [(n :: Word64, "")] -> Right n
        _ -> Left ("not a uint64: " <> s)

-- | Resolve the @--seed@ spec to a concrete RNG seed. @random@/@auto@ draws a
-- fresh Word64 from system entropy at launch — under Antithesis, entropy is a
-- controlled per-timeline choice point, so each timeline fuzzes from a distinct
-- seed (the mutation seed-space is explored, not fixed). The drawn value is
-- logged so any timeline is reproducible with @--seed 0x<value>@. An explicit
-- hex/decimal seed pins the RNG (deterministic recreation).
resolveSeed :: (String -> IO ()) -> String -> IO Word64
resolveSeed logMsg spec
    | spec `elem` ["random", "auto", "antithesis_random"] = do
        w <- drawEntropyWord64
        logMsg
            ( "seed: drew random seed from entropy (per-timeline under Antithesis): 0x"
                <> showHex w ""
                <> " | reproduce with --seed 0x"
                <> showHex w ""
            )
        pure w
    | otherwise = case parseSeed spec of
        Right w -> do
            logMsg ("seed: using explicit seed 0x" <> showHex w "")
            pure w
        Left e -> error ("bad --seed: " <> e)

-- | Draw 8 bytes of entropy and fold them into a 'Word64'. Reads @/dev/urandom@,
-- whose reads Antithesis intercepts as a randomness choice point.
drawEntropyWord64 :: IO Word64
drawEntropyWord64 =
    withBinaryFile "/dev/urandom" ReadMode $ \h -> do
        bs <- BS.hGet h 8
        pure (BS.foldl' (\acc b -> acc * 256 + fromIntegral b) 0 bs)

parseUpstream :: String -> Either String (String, Int)
parseUpstream s = case break (== ':') s of
    (h, ':' : p) -> case reads p of
        [(n, "")] -> Right (h, n)
        _ -> Left ("bad port in --upstream: " <> s)
    _ -> Left ("expected HOST:PORT in --upstream: " <> s)

main :: IO ()
main = do
    hSetBuffering stderr LineBuffering
    args0 <- execParser opts
    let logMsg s = hPutStrLn stderr ("dwarf-adversary: " <> s)
    seed <- resolveSeed logMsg (argSeedSpec args0)
    let args = args0 {argSeed = seed}
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
                else if argStateMachine args
                    then
                        if argSmServerMode args
                            then runStateMachineFuzz logMsg args magic port
                            else runStateMachineFuzzPool logMsg args magic
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
                else mutatingCodecChainSync (argLevel args) (argSeed args) (argRate args)
        onServe h = do
            let inf = describeHeaderMutation (argLevel args) (argSeed args) (argRate args) h
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
    firedVar <- newTVarIO False
    -- SOAK: re-arm the one-shot injection every --rollback-repeat-secs so the deep
    -- rollback is injected repeatedly (durable differential soak).
    _ <-
        if argRollbackDepth args > 0 && argRollbackRepeatSecs args > 0
            then fmap Just $ forkIO $ forever $ do
                threadDelay (argRollbackRepeatSecs args * 1_000_000)
                already <- atomically (readTVar firedVar)
                atomically (writeTVar firedVar False)
                if already
                    then logMsg ("deep-rb SOAK: re-armed injection (repeat every " <> show (argRollbackRepeatSecs args) <> "s)")
                    else pure ()
            else pure Nothing
    let csServer =
            if argRollbackDepth args > 0
                then deepRollbackChainSyncServer logMsg (\_ -> pure ()) chainVar (argRollbackDepth args) (argRollbackMinTip args) firedVar
                else advancingChainSyncServer logMsg (\_ -> pure ()) chainVar
        bfCodec = mutatingCodecBlockFetch (argLevel args) (argSeed args) (argRate args)
        onServeBlk b = do
            let inf = describeBlockMutation (argLevel args) (argSeed args) (argRate args) b
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
        bfCodec = mutatingCodecBlockFetch (argLevel args) (argSeed args) (argRate args)
        onServeBlk b = do
            let inf = describeBlockMutation (argLevel args) (argSeed args) (argRate args) b
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
txFieldOfShape "witness" = Witness
txFieldOfShape "governance" = GovAction
txFieldOfShape "plutus" = PlutusWitness
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
        txCodec = mutatingCodecTxSubmission field (argLevel args) (argSeed args) (argRate args)
        -- per-serve assertion: fired each time the node actually pulls a tx
        -- (true serve signal, not a once-at-startup emit).
        onServeTx t = do
            let inf = describeTxMutation field (argLevel args) (argSeed args) (argRate args) t
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
    -- Seed-corpus: wire-GenTx files (what encTx emits) always offered as base
    -- txs, so --cbor-shape certificate/auxiliary-data engages even when the
    -- synced chain has no matching tx (the hermetic Antithesis devnet carries
    -- only payment + Plutus txs). Loaded once; prepended to every refresh.
    seedTxs <- loadSeedTxs logMsg (argSeedTxFiles args)
    logMsg ("seed-corpus: " <> show (length seedTxs) <> " seed tx(s) loaded")
    txsVar <- newTVarIO seedTxs
    _ <- forkIO $ forever $ do
        batch <- getBaseTxsFromChain logMsg chainVar magic hp 10
        case argHarvestTo args of
            Just dir -> harvestTxs logMsg dir batch
            Nothing -> pure ()
        atomically (writeTVar txsVar (seedTxs <> batch))
        SDK.sometimes
            (not (null seedTxs && null batch))
            "dwarf_base_tx_obtained"
            (object ["count" .= (length seedTxs + length batch), "seeds" .= length seedTxs])
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

-- | SP4 state-machine fuzz serve loop. Drives ChainSync (#2), BlockFetch (#3),
-- TxSubmission2 (#4) and KeepAlive (#8) concurrently with raw model-driven
-- responders emitting generative ILLEGAL sequences; a protocol violation makes
-- the node drop us, so we loop (the per-connection counter advances the
-- generated sequences). The node REJECTING the violation is the success path.
runStateMachineFuzz :: (String -> IO ()) -> Args -> NetworkMagic -> PortNumber -> IO ()
runStateMachineFuzz logMsg args magic port = do
    SDK.reachable "dwarf_fuzz_server_started" (object ["mode" .= ("state-machine" :: String)])
    SDK.reachable "dwarf_fuzz_server_listening" (object ["port" .= argPort args])
    logMsg
        "state-machine fuzz: serving ChainSync#2/BlockFetch#3/TxSubmission2#4/KeepAlive#8 \
        \with generative ILLEGAL sequences"
    ctr <- newIORef 0
    let onAccept p = do
            logMsg ("inbound connection accepted from " <> p)
            SDK.reachable "dwarf_node_connected" (object ["peer" .= p])
        -- Per-protocol x departure-class exploration signal: tells Antithesis the
        -- space so it can steer entropy to cover every class on every protocol.
        onDeparture proto cls = do
            SDK.reachable
                ("dwarf_sm_" <> proto <> "_" <> T.pack (show cls))
                (object ["protocol" .= proto, "class" .= show cls])
            -- breadth: we served an illegal sequence for this protocol at least once
            SDK.sometimes True
                ("dwarf_sm_served_" <> proto)
                (object ["protocol" .= proto])
        -- HEURISTIC accepted-illegal exploration signal (reachable only, NEVER a
        -- pass/fail oracle; see StateMachine.isAcceptedIllegal). The real win/lose
        -- liveness Always is asserted by the harness / bundle, not here.
        onAccepted proto =
            SDK.reachable
                ("dwarf_sm_illegal_accepted_" <> proto)
                (object ["protocol" .= proto])
    forever $
        void (runAdversaryServerSM magic port onAccept logMsg onDeparture onAccepted ctr (argSeed args))
            `catch` \(e :: SomeException) -> do
                logMsg ("state-machine server restart on: " <> show e)
                threadDelay 1_000_000

-- | SP4 state-machine fuzz POOL (the default for @--state-machine-fuzz@): N
-- concurrent workers each DIAL the node (@--upstream@), inject one generated
-- illegal sequence as the mux initiator, tear down, and reconnect immediately.
-- Throughput is @N / per-connection latency@, decoupled from the node's serial
-- reconnect backoff that gated the inbound responder server. As initiator the
-- adversary also opens BlockFetch (#3), closing the on-wire gap, and exercises the
-- node's SERVER-side mini-protocol state machines.
runStateMachineFuzzPool :: (String -> IO ()) -> Args -> NetworkMagic -> IO ()
runStateMachineFuzzPool logMsg args magic = do
    (host, port) <- case argUpstream args of
        Just (h, p) -> pure (h, fromIntegral p :: PortNumber)
        Nothing ->
            error "--state-machine-fuzz (pool mode) requires --upstream HOST:PORT (the node to dial)"
    let n = max 1 (argSmConnections args)
        budgetMicros = max 1 (argSmConnMs args) * 1000
        protos = [("chainsync", 2), ("blockfetch", 3), ("txsubmission", 4), ("keepalive", 8)]
            :: [(T.Text, Word16)]
        target = host <> ":" <> show (fromIntegral port :: Int)
    SDK.reachable
        "dwarf_fuzz_server_started"
        (object ["mode" .= ("state-machine-pool" :: String), "connections" .= n, "target" .= target])
    logMsg
        ( "state-machine fuzz POOL: " <> show n <> " concurrent initiators dialing " <> target
            <> " (ChainSync#2/BlockFetch#3/TxSubmission2#4/KeepAlive#8, generative ILLEGAL sequences)"
        )
    ctr <- newIORef 0
    let onDeparture proto cls = do
            SDK.reachable
                ("dwarf_sm_" <> proto <> "_" <> T.pack (show cls))
                (object ["protocol" .= proto, "class" .= show cls])
            SDK.sometimes True ("dwarf_sm_served_" <> proto) (object ["protocol" .= proto])
        onAccepted proto =
            SDK.reachable
                ("dwarf_sm_illegal_accepted_" <> proto)
                (object ["protocol" .= proto])
        worker workerId = loop (0 :: Int)
          where
            -- Round-robin protocol selection, offset by workerId: deterministic
            -- (reproducible from --seed) AND evenly covers all four protocols, with
            -- the N workers spread across protocols at any instant.
            loop iteration = do
                let (proto, protoNum) = protos !! ((workerId + iteration) `mod` length protos)
                    -- Decorrelate workers: fold the worker id into the seed (on top
                    -- of the shared counter's per-connection index inside
                    -- scriptedSequenceInitiator) so no two workers run identical
                    -- selectors.
                    workerSeed = argSeed args + fromIntegral workerId * 0x9E3779B97F4A7C15
                injected <-
                    runSMInitiatorOnce
                        magic host port proto protoNum budgetMicros
                        logMsg onDeparture onAccepted ctr workerSeed
                -- Injected (established + ran, incl. the force-close fast path): loop
                -- immediately. Failed before injecting (node down/refused, e.g.
                -- AcceptedConnectionsLimit): small backoff to avoid a busy-spin.
                if injected then pure () else threadDelay 50000
                loop (iteration + 1)
    -- Fixed N concurrency bounds fd/resource use; each worker loops forever.
    mapConcurrently_ worker [0 .. n - 1]
