{-# LANGUAGE NumericUnderscores #-}
{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE ScopedTypeVariables #-}

module Main (main) where

import Codec.CBOR.Read (deserialiseFromBytes)
import Codec.CBOR.Write (toLazyByteString)
import Control.Concurrent (forkIO, threadDelay)
import Control.Concurrent.Class.MonadSTM.Strict (newTVarIO)
import Control.Exception (SomeException, catch)
import Control.Monad (forever)
import Data.Aeson (Value, encode, object, (.=))
import Data.ByteString qualified as BS
import Data.ByteString.Lazy qualified as LBS
import Data.ByteString.Lazy.Char8 qualified as LBS8
import Data.IORef (IORef, atomicModifyIORef', newIORef)
import Data.Set qualified as Set
import Data.Word (Word64)
import DwarfAdversary (originPoint)
import DwarfAdversary.Application (Limit (..), runChainProducerInto, syncHeaders)
import DwarfAdversary.ChainSync.Codec (Header, decHeader, encHeader)
import DwarfAdversary.ChainSync.Connection
    ( blockFetchResponder
    , plainBlockFetchCodec
    , runChainSyncServer
    )
import DwarfAdversary.ChainSync.Server (advancingChainSyncServer, tipFromHeaders)
import DwarfAdversary.SDK qualified as SDK
import DwarfKesAdversary
    ( KesMutation (..)
    , describeKesMutation
    , gateKesMutation
    , kesMismatchCodec
    , kesSeededCodec
    , mutateKesSignatureSeeded
    , singleTargetServer
    )
import Ouroboros.Consensus.Block (headerPoint)
import Ouroboros.Network.Block (HeaderFields (..), SlotNo (..), castPoint, getHeaderFields)
import Ouroboros.Network.Magic (NetworkMagic (..))
import Ouroboros.Network.Mock.Chain qualified as Chain
import System.Directory (createDirectoryIfMissing, doesFileExist, getFileSize)
import System.Environment (getArgs)
import System.FilePath (takeDirectory)
import System.IO
    ( BufferMode (LineBuffering)
    , IOMode (AppendMode, ReadMode)
    , hSetBuffering
    , stdout
    , withBinaryFile
    )
import Text.Read (readMaybe)


main :: IO ()
main = do
    hSetBuffering stdout LineBuffering
    args <- getArgs
    case args of
        [ "live-proxy"
            , "--upstream"
            , upstream
            , "--listen-port"
            , listenText
            , "--seed"
            , seedText
            , "--mutation-rate"
            , rateText
            , "--min-slot"
            , minimumSlotText
            , "--evidence"
            , evidence
            ] ->
                case (parseHostPort upstream, readMaybe listenText, readMaybe rateText, readMaybe minimumSlotText) of
                    (Just (host, upstreamPort), Just listenPort, Just rate, Just minimumSlot)
                        | rate >= 0 && rate <= 1 -> do
                            seed <- parseSeed seedText
                            runLive host upstreamPort listenPort seed rate minimumSlot evidence
                    _ -> usage
        [host, portText, countText, listenText] ->
            case (readMaybe portText, readMaybe countText, readMaybe listenText) of
                (Just upstreamPort, Just count, Just listenPort) | count >= 2 ->
                    runOneShot host upstreamPort count listenPort
                _ -> usage
        _ -> usage


usage :: IO a
usage =
    error
        "usage: dwarf-kes-adversary UPSTREAM_HOST UPSTREAM_PORT HEADER_COUNT LISTEN_PORT | dwarf-kes-adversary live-proxy --upstream HOST:PORT --listen-port PORT --seed random|WORD64 --mutation-rate RATE --min-slot SLOT --evidence FILE"


parseHostPort :: String -> Maybe (String, Int)
parseHostPort value =
    case break (== ':') value of
        (host, ':' : portText) | not (null host) -> do
            port <- readMaybe portText
            pure (host, port)
        _ -> Nothing


parseSeed :: String -> IO Word64
parseSeed "random" = withBinaryFile "/dev/urandom" ReadMode $ \handle -> do
    bytes <- BS.hGet handle 8
    if BS.length bytes /= 8
        then error "could not read an eight-byte seed"
        else pure (BS.foldl' (\acc byte -> acc * 256 + fromIntegral byte) 0 bytes)
parseSeed value = maybe usage pure (readMaybe value)


runOneShot :: String -> Int -> Int -> Int -> IO ()
runOneShot host upstreamPort count listenPort = do
    captured <- syncHeaders (NetworkMagic 42) host (fromIntegral upstreamPort) originPoint (Limit (fromIntegral count))
    headers <- either (error . show) pure captured
    if length headers /= count
        then error "capture count was not exact"
        else do
            let parent = headers !! (count - 2)
                target = last headers
                parentPoint = castPoint (headerPoint parent)
                tip = tipFromHeaders [target]
            putStrLn $ "kes-target: captured exact parent=" <> show (getHeaderFields parent)
            putStrLn $ "kes-target: captured exact target=" <> show (getHeaderFields target)
            _ <-
                runChainSyncServer
                    (NetworkMagic 42)
                    (fromIntegral listenPort)
                    (\peer -> putStrLn ("kes-target: accepted " <> peer))
                    kesMismatchCodec
                    (singleTargetServer putStrLn parentPoint target tip)
                    plainBlockFetchCodec
                    blockFetchResponder
            pure ()


runLive :: String -> Int -> Int -> Word64 -> Double -> Word64 -> FilePath -> IO ()
runLive host upstreamPort listenPort seed rate minimumSlot evidence = do
    let magic = NetworkMagic 42
    chainVar <- newTVarIO Chain.Genesis
    seenHeaders <- newIORef Set.empty
    acceptedOnce <- newIORef False
    SDK.reachable
        "mixed_kes_proxy_started"
        (object ["upstream" .= (host <> ":" <> show upstreamPort), "seed" .= seed, "rate" .= rate, "minimum_slot" .= minimumSlot])
    appendEvidence evidence $
        object
            [ "kind" .= ("proxy_started" :: String)
            , "seed" .= seed
            , "mutation_rate" .= rate
            , "minimum_slot" .= minimumSlot
            ]
    _ <- forkIO $ forever $ do
        result <- runChainProducerInto chainVar magic host (fromIntegral upstreamPort)
        case result of
            Left exception -> putStrLn ("kes-live: upstream ended: " <> show exception)
            Right () -> putStrLn "kes-live: upstream ended cleanly"
        threadDelay 1_000_000
    let onAccept peer = do
            first <- atomicModifyIORef' acceptedOnce (\seen -> (True, not seen))
            if first
                then do
                    putStrLn ("kes-live: accepted " <> peer)
                    SDK.reachable "mixed_kes_victim_connected" (object ["peer" .= peer, "seed" .= seed])
                else pure ()
        onServe = emitHeaderEvidence seenHeaders evidence seed rate minimumSlot
        server = advancingChainSyncServer (const (pure ())) onServe chainVar
    forever $
        ( runChainSyncServer
            magic
            (fromIntegral listenPort)
            onAccept
            (kesSeededCodec seed rate minimumSlot)
            server
            plainBlockFetchCodec
            blockFetchResponder
            >> pure ()
        ) `catch` \(exception :: SomeException) -> do
            putStrLn ("kes-live: server restart after: " <> show exception)
            threadDelay 1_000_000


emitHeaderEvidence :: IORef (Set.Set String) -> FilePath -> Word64 -> Double -> Word64 -> Header -> IO ()
emitHeaderEvidence seenHeaders evidence seed rate minimumSlot header = do
    let originalBytes = LBS.toStrict (toLazyByteString (encHeader header))
        HeaderFields sourceSlot@(SlotNo currentSlot) sourceBlock sourceHash = getHeaderFields header
        mutation = gateKesMutation minimumSlot currentSlot (describeKesMutation seed rate originalBytes)
        mutatedBytes =
            if kesMutationApplied mutation
                then mutateKesSignatureSeeded seed rate originalBytes
                else originalBytes
        mutatedFields = case deserialiseFromBytes decHeader (LBS.fromStrict mutatedBytes) of
            Right (rest, decoded) | LBS.null rest -> Just (getHeaderFields decoded)
            _ -> Nothing
        kind :: String
        kind = if kesMutationApplied mutation then "kes_mutation" else "honest_header"
        mutatedHash = case mutatedFields of
            Just (HeaderFields _ _ hashValue) -> show hashValue
            Nothing -> "decode-failed"
        details =
            object
                [ "kind" .= kind
                , "source_slot" .= show sourceSlot
                , "source_block" .= show sourceBlock
                , "source_hash" .= show sourceHash
                , "mutated_hash" .= mutatedHash
                , "signature_offset" .= kesSignatureOffset mutation
                , "bit" .= kesMutationBit mutation
                , "seed" .= seed
                , "minimum_slot" .= minimumSlot
                ]
        key = show sourceHash
    first <- atomicModifyIORef' seenHeaders $ \seen ->
        if Set.member key seen
            then (seen, False)
            else (Set.insert key seen, True)
    if first
        then do
            appendEvidence evidence details
            if kesMutationApplied mutation
                then SDK.sometimes True "mixed_kes_mutation_served" details
                else SDK.reachable "mixed_kes_honest_header_served" details
        else pure ()


appendEvidence :: FilePath -> Value -> IO ()
appendEvidence path value = do
    createDirectoryIfMissing True (takeDirectory path)
    exists <- doesFileExist path
    size <- if exists then getFileSize path else pure 0
    if size >= 4 * 1024 * 1024
        then pure ()
        else withBinaryFile path AppendMode $ \handle -> LBS8.hPutStrLn handle (encode value)
