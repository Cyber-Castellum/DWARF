{-# LANGUAGE LambdaCase #-}
{-# LANGUAGE ScopedTypeVariables #-}

-- |
-- dwarf-decoder-fuzz — high-volume IN-PROCESS fuzzing of the real Haskell CBOR
-- decoder (#4 / FU5). Unlike the protocol adversary (wire-bound, ~1-2 tx/min),
-- this calls @decTx@ directly in a tight loop — millions of decode attempts —
-- and reports real metrics + any non-clean outcome.
--
-- Honest scope: this is high-volume GENERATIONAL fuzzing (seed corpus + the
-- existing mutation engine), NOT coverage-guided — GHC emits no SanitizerCoverage
-- for AFL/libFuzzer to steer on. It finds the classes Haskell can have: uncaught
-- exceptions (not the expected DeserialiseFailure) and non-termination / timeouts
-- (the DoS class). Not memory corruption (that's the C FFI, FU10).
module Main (main) where

import Codec.CBOR.Read (deserialiseFromBytes)
import Codec.CBOR.Term (decodeTerm, encodeTerm)
import Codec.CBOR.Write (toLazyByteString, toStrictByteString)
import Control.Exception (SomeException, evaluate, try)
import Control.Monad (filterM, forM)
import Data.Aeson (encode, object, (.=))
import Data.Bits ((.&.))
import Data.ByteString qualified as BS
import Data.ByteString.Lazy qualified as LBS
import Data.ByteString.Lazy.Char8 qualified as LC8
import Data.IORef
import Data.Time.Clock (diffUTCTime, getCurrentTime)
import Data.Word (Word8)
import DwarfAdversary.ApplyBlock (ApplyBlockOutcome (..), applyBlockOutcome)
import DwarfAdversary.ChainSync.Codec (decTx, encTx)
import DwarfAdversary.Fuzz
    ( MutationLevel (..)
    , corruptBytes
    , mutateTerm
    , mutateTermSemantic
    , parseMutationLevel
    )
import DwarfAdversary.SDK qualified as SDK
import System.IO (IOMode (ReadMode), withBinaryFile)
import Numeric (showHex)
import System.Directory (doesFileExist, listDirectory)
import System.Environment (getArgs)
import System.FilePath ((</>))
import System.Random (StdGen, mkStdGen, randomR, split)
import System.Timeout (timeout)

data Outcome = DecodedOk | CleanReject | Exception String | Timeout
    deriving (Eq)

-- | Decode the mutated bytes with the real GenTx decoder and FORCE the result
-- (re-encode the decoded tx) so lazy decode-bombs and exceptions are realized.
-- A returned @Left DeserialiseFailure@ is the EXPECTED clean reject; any thrown
-- exception (caught by 'try') is a finding; exceeding the per-input time budget
-- is a timeout/DoS candidate.
runOne :: String -> Int -> BS.ByteString -> IO Outcome
runOne target limitUs bytes = do
    r <- timeout limitUs (try (evaluate (force1 bytes)))
    pure $ case r of
        Nothing -> Timeout
        Just (Left (e :: SomeException)) -> Exception (show e)
        Just (Right o) -> o
  where
    force1 = case target of
        "applyblock" -> forceApplyBlock
        _ -> forceDecode
    forceDecode b = case deserialiseFromBytes decTx (LBS.fromStrict b) of
        Right (rest, tx) ->
            let n = LBS.length (toLazyByteString (encTx tx))
            in n `seq` (if LBS.null rest then DecodedOk else CleanReject)
        Left _ -> CleanReject
    -- applyblock: run the full BBODY -> LEDGERS -> per-tx ledger rules over a
    -- genesis-initialised Conway NewEpochState (shared DwarfAdversary.ApplyBlock).
    forceApplyBlock b = case applyBlockOutcome (LBS.fromStrict b) of
        AbTxDecodeFail -> CleanReject
        AbRejected s -> s `seq` CleanReject
        AbAccepted -> DecodedOk

-- | Mutate the seed bytes by the chosen level (reuses the adversary engine).
mutate :: MutationLevel -> StdGen -> BS.ByteString -> BS.ByteString
mutate lvl g bs = case lvl of
    LevelBytes -> fst (corruptBytes g 1.0 bs)
    LevelStruct -> viaTerm (\t -> fst (mutateTerm g 1.0 t))
    LevelSemantic -> viaTerm (\t -> fst (mutateTermSemantic g 1.0 t))
    LevelBoth -> fst (corruptBytes g 1.0 (viaTerm (\t -> fst (mutateTerm g 1.0 t))))
  where
    viaTerm f = case deserialiseFromBytes decodeTerm (LBS.fromStrict bs) of
        Right (rest, t) | LBS.null rest -> toStrictByteString (encodeTerm (f t))
        _ -> bs

main :: IO ()
main = do
    args <- getArgs
    let corpus = argVal "--corpus" args
        target = maybe "tx" id (lookupArg "--target" args)
        iters = maybe 1000000 read (lookupArg "--iters" args) :: Int
        maxSecs = maybe 0 read (lookupArg "--seconds" args) :: Double -- 0 = iter-bound
        lvl = maybe LevelBytes id (lookupArg "--level" args >>= parseMutationLevel)
        limitUs = maybe 1000000 read (lookupArg "--timeout-us" args) :: Int
        sdkOn = "--sdk" `elem` args
        keepN = 25 :: Int
    seed0 <- resolveSeed (lookupArg "--seed" args)
    seeds <- loadCorpus corpus
    if null seeds
        then putStrLn ("no corpus files in " <> corpus) >> pure ()
        else do
            putStrLn
                ( "dwarf-decoder-fuzz: target=" <> target <> " corpus=" <> corpus
                    <> " seeds=" <> show (length seeds)
                    <> " iters=" <> show iters <> " level=" <> show lvl
                    <> " seed=" <> show seed0
                )
            okR <- newIORef (0 :: Int)
            rejR <- newIORef (0 :: Int)
            excR <- newIORef (0 :: Int)
            toR <- newIORef (0 :: Int)
            findR <- newIORef ([] :: [(String, BS.ByteString, Int)])
            sawOkR <- newIORef False
            sawRejR <- newIORef False
            if sdkOn
                then do
                    SDK.reachable "dwarf_decoder_fuzz_ran" (object ["target" .= target, "level" .= show lvl])
                    SDK.always True "dwarf_decoder_no_uncaught_exception" (object ["target" .= target])
                    SDK.always True "dwarf_decoder_no_timeout" (object ["target" .= target])
                else pure ()
            let onceSometimes ref aid = do
                    seen <- readIORef ref
                    if sdkOn && not seen
                        then writeIORef ref True >> SDK.sometimes True aid (object ["target" .= target])
                        else pure ()
            let seedVec = seeds
                nSeeds = length seedVec
            t0 <- getCurrentTime
            let timeUp = do
                    tn <- getCurrentTime
                    pure (maxSecs > 0 && realToFrac (diffUTCTime tn t0) >= maxSecs)
            let loop !i !g
                    | i >= iters = pure ()
                    | otherwise = do
                        stop <- if i .&. 0xFFFF == 0 then timeUp else pure False
                        if stop
                            then pure ()
                            else runIter i g
                runIter !i !g = do
                        let (g1, g2) = split g
                            (si, g3) = randomR (0, nSeeds - 1) g1
                            base = seedVec !! si
                            mutated = mutate lvl g3 base
                        o <- runOne target limitUs mutated
                        case o of
                            DecodedOk -> modifyIORef' okR (+ 1) >> onceSometimes sawOkR "dwarf_decoder_decoded_ok"
                            CleanReject -> modifyIORef' rejR (+ 1) >> onceSometimes sawRejR "dwarf_decoder_clean_reject"
                            Exception e -> do
                                modifyIORef' excR (+ 1)
                                if sdkOn
                                    then SDK.always False "dwarf_decoder_no_uncaught_exception" (object ["detail" .= take 200 e, "iter" .= i])
                                    else pure ()
                                fs <- readIORef findR
                                if length fs < keepN
                                    then writeIORef findR ((e, mutated, i) : fs)
                                    else pure ()
                            Timeout -> do
                                modifyIORef' toR (+ 1)
                                if sdkOn
                                    then SDK.always False "dwarf_decoder_no_timeout" (object ["iter" .= i])
                                    else pure ()
                                fs <- readIORef findR
                                if length fs < keepN
                                    then writeIORef findR (("TIMEOUT", mutated, i) : fs)
                                    else pure ()
                        loop (i + 1) g2
            loop 0 (mkStdGen seed0)
            t1 <- getCurrentTime
            ok <- readIORef okR
            rej <- readIORef rejR
            exc <- readIORef excR
            to <- readIORef toR
            finds <- readIORef findR
            let done = ok + rej + exc + to
                secs = realToFrac (diffUTCTime t1 t0) :: Double
                eps = if secs > 0 then fromIntegral done / secs else 0 :: Double
                findJson =
                    [ object
                        [ "class" .= (if cls == "TIMEOUT" then "timeout" else "exception" :: String)
                        , "detail" .= take 200 cls
                        , "iter" .= it
                        , "inputHex" .= hexOf inp
                        ]
                    | (cls, inp, it) <- finds
                    ]
            LC8.putStrLn
                ( encode
                    ( object
                        [ "target" .= target
                        , "level" .= show lvl
                        , "seed" .= seed0
                        , "iterations" .= done
                        , "wallSeconds" .= secs
                        , "execPerSec" .= eps
                        , "decodedOk" .= ok
                        , "cleanReject" .= rej
                        , "exception" .= exc
                        , "timeout" .= to
                        , "findings" .= findJson
                        ]
                    )
                )

loadCorpus :: FilePath -> IO [BS.ByteString]
loadCorpus "" = pure []
loadCorpus dir = do
    names <- listDirectory dir
    let paths = map (dir </>) names
    files <- filterM doesFileExist paths
    forM files BS.readFile

hexOf :: BS.ByteString -> String
hexOf = concatMap (\b -> pad (showHex b "")) . BS.unpack
  where
    pad [c] = ['0', c]
    pad s = s

-- | @--seed random|auto@ draws 8 bytes from /dev/urandom (under Antithesis this
-- is intercepted as a per-timeline choice point → each timeline fuzzes a
-- different region of the mutation space); otherwise read the explicit Int.
resolveSeed :: Maybe String -> IO Int
resolveSeed = \case
    Just s | s `elem` ["random", "auto"] -> drawSeed
    Just s -> pure (read s)
    Nothing -> pure 1

drawSeed :: IO Int
drawSeed = withBinaryFile "/dev/urandom" ReadMode $ \h -> do
    bs <- BS.hGet h 8
    pure (BS.foldl' (\a b -> a * 256 + fromIntegral b) 0 bs)

lookupArg :: String -> [String] -> Maybe String
lookupArg k (a : v : rest)
    | a == k = Just v
    | otherwise = lookupArg k (v : rest)
lookupArg _ _ = Nothing

argVal :: String -> [String] -> String
argVal k = maybe "" id . lookupArg k
