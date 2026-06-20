{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE ForeignFunctionInterface #-}

-- |
-- ⚠️ DOES NOT WORK — kept as a documented experiment (not built by the cabal).
-- AFL engages persistent + shared-memory mode against this binary ("new fork
-- server model v1 is up"), but the deferred forkserver forks AFTER the GHC RTS
-- is initialized and the persistent child hangs (AFL calibration times out even
-- with a single seed + -t 8000; -V0 -I0 to quiet the RTS timer/idle-GC did not
-- help). This is the known managed-runtime persistent-fork incompatibility:
-- fork-per-exec works only because afl-compiler-rt forks at the C constructor,
-- before the RTS. The realistic throughput lever is shrinking the ~2.07M-entry
-- whole-tree SanCov coverage map (instrument fewer packages), not persistent mode.
--
-- dwarf-decode-persist — AFL++ PERSISTENT-mode sibling of dwarf-decode-any.
-- Instead of fork-per-exec (which on this whole-tree-SanCov GHC binary costs
-- ~12 ms/exec, ~82 execs/s), the forkserver forks ONCE and this loops over
-- test cases delivered in shared memory (__AFL_LOOP), reusing the GHC RTS and
-- the one-time genesis NewEpochState across iterations — typically 10-100x
-- faster while keeping coverage guidance.
--
-- Same surface dispatch + oracle as the fork harness (DwarfAdversary.Surfaces):
-- clean reject -> continue; any other uncaught exception -> abort() (AFL crash).
module Main (main) where

import Codec.CBOR.Read (DeserialiseFailure)
import Control.Exception (SomeException, evaluate, fromException, try)
import Control.Monad (when)
import qualified Data.ByteString as BS
import qualified Data.ByteString.Lazy as LBS
import Data.Maybe (fromMaybe)
import Data.Word (Word8)
import DwarfAdversary.ApplyBlock (conwayInitialNES)
import DwarfAdversary.Surfaces (runSurf)
import Foreign.C.Types (CInt (..), CUInt (..))
import Foreign.Ptr (Ptr, castPtr)
import System.Environment (lookupEnv)
import System.Exit (exitSuccess)
import System.IO (hPutStrLn, stderr)

foreign import ccall unsafe "dwarf_afl_enable_shmem" c_enable_shmem :: IO ()
foreign import ccall unsafe "dwarf_afl_init" c_afl_init :: IO ()
foreign import ccall unsafe "dwarf_afl_loop" c_afl_loop :: CUInt -> IO CInt
foreign import ccall unsafe "dwarf_afl_buf" c_afl_buf :: IO (Ptr Word8)
foreign import ccall unsafe "dwarf_afl_len" c_afl_len :: IO CUInt
foreign import ccall unsafe "abort" c_abort :: IO ()

main :: IO ()
main = do
    surf <- fromMaybe "tx" <$> lookupEnv "DWARF_DECODER"
    c_enable_shmem
    -- Prime the one-time genesis NewEpochState BEFORE the fork point so every
    -- persistent iteration reuses it (otherwise applyblock would rebuild it).
    when (surf == "applyblock") $ evaluate conwayInitialNES >> pure ()
    c_afl_init
    let loop = do
            cont <- c_afl_loop 100000
            if cont == 0
                then exitSuccess
                else do
                    buf <- c_afl_buf
                    len <- c_afl_len
                    bytes <- BS.packCStringLen (castPtr buf, fromIntegral len)
                    r <- try (runSurf surf (LBS.fromStrict bytes))
                    case r of
                        Right _ -> pure ()
                        Left (e :: SomeException) ->
                            case fromException e :: Maybe DeserialiseFailure of
                                Just _ -> pure ()
                                Nothing -> hPutStrLn stderr ("DWARF_ABORT: " <> show e) >> c_abort
                    loop
    loop
