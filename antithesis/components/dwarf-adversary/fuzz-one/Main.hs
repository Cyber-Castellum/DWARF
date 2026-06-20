{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE ForeignFunctionInterface #-}

-- |
-- dwarf-decode-one — single-input AFL++ harness for the real cardano-node
-- Conway tx decoder. Reads ONE input file (AFL's @@), decodes via decTx and
-- forces it; AFL drives mutation + coverage (binary-only, QEMU/Frida). Oracle:
-- clean reject (DeserialiseFailure) -> exit 0; any other uncaught exception ->
-- abort() -> SIGABRT (AFL records a crash); non-termination -> AFL timeout.
module Main (main) where

import Codec.CBOR.Read (DeserialiseFailure, deserialiseFromBytes)
import Codec.CBOR.Write (toLazyByteString)
import Control.Exception (SomeException, evaluate, fromException, try)
import qualified Data.ByteString.Lazy as LBS
import DwarfAdversary.ChainSync.Codec (decTx, encTx)
import System.Environment (getArgs)
import System.Exit (exitSuccess)

foreign import ccall unsafe "abort" c_abort :: IO ()

forceDecode :: LBS.ByteString -> Bool
forceDecode b = case deserialiseFromBytes decTx b of
    Right (rest, tx) -> LBS.length (toLazyByteString (encTx tx)) `seq` LBS.null rest
    Left _ -> False

main :: IO ()
main = do
    args <- getArgs
    case args of
        (f : _) -> do
            bytes <- LBS.readFile f
            r <- try (evaluate (forceDecode bytes))
            case r of
                Right _ -> exitSuccess
                Left (e :: SomeException) -> case fromException e :: Maybe DeserialiseFailure of
                    Just _ -> exitSuccess
                    Nothing -> c_abort
        _ -> exitSuccess
