{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE TypeApplications #-}

-- |
-- dwarf-snapshot-decode — de-risk probe for the applyBlock surface.
--
-- Reads a real UTxO-HD ledger snapshot's @state@ file (a version-wrapped
-- @ExtLedgerState (CardanoBlock StandardCrypto) EmptyMK@) using the SAME
-- 'ccfg' the dwarf-adversary chain-sync/block codecs use, and confirms it
-- decodes to a tip. Proving this decode is green is the gate before wiring
-- the full applyBlock (BBODY STS) surface: decode -> NewEpochState ->
-- Block BHeaderView -> applyBlock.
--
-- Usage: dwarf-snapshot-decode [SNAPSHOT_DIR]   (default ledger-snapshots/conway-slot430)
module Main (main) where

import Codec.Serialise (decode)
import Control.Monad.Except (runExceptT)
import DwarfAdversary.ChainSync.Codec (Block, ccfg)
import Ouroboros.Consensus.Ledger.Basics (EmptyMK, getTip)
import Ouroboros.Consensus.Ledger.Extended (ExtLedgerState, decodeDiskExtLedgerState)
import Ouroboros.Consensus.Storage.LedgerDB.Snapshots (readExtLedgerState)
import System.Environment (getArgs)
import System.Exit (exitFailure)
import System.FS.API (MountPoint (..), SomeHasFS (..), mkFsPath)
import System.FS.IO (ioHasFS)

main :: IO ()
main = do
    args <- getArgs
    let dir = case args of
            (d : _) -> d
            _ -> "ledger-snapshots/conway-slot430"
    let fs = SomeHasFS (ioHasFS (MountPoint dir))
    res <-
        runExceptT $
            readExtLedgerState
                fs
                (decodeDiskExtLedgerState ccfg)
                decode
                (mkFsPath ["state"])
    case res of
        Left err -> do
            putStrLn ("SNAPSHOT DECODE FAILED: " <> show err)
            exitFailure
        Right (st :: ExtLedgerState Block EmptyMK, crc) -> do
            putStrLn "SNAPSHOT DECODE OK"
            putStrLn ("tip = " <> show (getTip st))
            putStrLn ("crc = " <> show crc)
