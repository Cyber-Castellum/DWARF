{-# LANGUAGE NumericUnderscores #-}
{-# LANGUAGE OverloadedStrings #-}

-- |
-- @dwarf-adversary@ — chain-sync UPSTREAM SERVER. A cardano-node dials
-- us as a chain-sync client; we accept, complete the N2N handshake as
-- responder, and serve headers whose CBOR is structurally mutated (via
-- the mutating codec) so the node runs its real header decoder on
-- adversarial input. Seeded solely by @--seed@ (from antithesis_random)
-- for deterministic recreation.
module Main (main) where

import Control.Concurrent (forkIO, threadDelay)
import Data.Aeson (object, (.=))
import Data.Word (Word32, Word64)
import DwarfAdversary (originPoint)
import DwarfAdversary.Application (Limit (..), adversaryApplication)
import DwarfAdversary.ChainSync.Codec (codecChainSync)
import DwarfAdversary.ChainSync.Connection (runChainSyncServer)
import DwarfAdversary.ChainSync.MutatingCodec
    ( describeHeaderMutation
    , mutatingCodecChainSync
    )
import DwarfAdversary.ChainSync.Server (chainSyncServer, tipFromHeaders)
import DwarfAdversary.Fuzz (MutationInfo (..))
import DwarfAdversary.HeaderSource (getBaseHeaders)
import DwarfAdversary.SDK qualified as SDK
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
                        "Spawn the server then run our own chain-sync client\
                        \ against it (proves handshake + chain-sync wiring)."
                )
                <|> pure False
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
    if argSelftest args
        then runSelftest logMsg magic port
        else runServe logMsg args magic port
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
    _ <- runChainSyncServer magic port onAccept codec (chainSyncServer logMsg onServe headers tip)
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
                    (chainSyncServer logMsg (\_ -> pure ()) [] (tipFromHeaders []))
            pure ()
    threadDelay 2_000_000
    logMsg "selftest: connecting our own client to 127.0.0.1"
    res <- adversaryApplication magic "127.0.0.1" port originPoint (Limit 5)
    logMsg $ "selftest: client result = " <> show res
    threadDelay 1_000_000
