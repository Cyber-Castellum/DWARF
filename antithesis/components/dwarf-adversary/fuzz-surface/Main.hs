{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE ForeignFunctionInterface #-}

-- |
-- dwarf-decode-any — multi-surface single-input AFL++ harness (fork-per-exec).
-- Reads ONE input file (AFL's @@) and runs it through the surface selected by
-- DWARF_DECODER (see 'DwarfAdversary.Surfaces' for the surface list + oracle).
--
-- Oracle: clean reject (DeserialiseFailure / Left / trailing bytes) -> exit 0;
-- any other uncaught exception -> abort() -> SIGABRT (AFL records a crash);
-- non-termination -> AFL timeout. The persistent-mode sibling is
-- dwarf-decode-persist (same DwarfAdversary.Surfaces.runSurf, no fork per exec).
module Main (main) where

import Codec.CBOR.Read (DeserialiseFailure)
import Control.Exception (SomeException, fromException, try)
import qualified Data.ByteString.Lazy as LBS
import Data.Maybe (fromMaybe)
import DwarfAdversary.Surfaces (runSurf)
import System.Environment (getArgs, lookupEnv)
import System.Exit (exitSuccess)
import System.IO (hPutStrLn, stderr)

foreign import ccall unsafe "abort" c_abort :: IO ()

main :: IO ()
main = do
    surf <- fromMaybe "tx" <$> lookupEnv "DWARF_DECODER"
    args <- getArgs
    case args of
        (f : _) -> do
            bytes <- LBS.readFile f
            r <- try (runSurf surf bytes)
            case r of
                Right _ -> exitSuccess
                Left (e :: SomeException) -> case fromException e :: Maybe DeserialiseFailure of
                    Just _ -> exitSuccess
                    Nothing -> do
                        hPutStrLn stderr ("DWARF_ABORT: " <> show e)
                        c_abort
        _ -> exitSuccess
