{-# LANGUAGE OverloadedStrings #-}
-- | cardano-node-cbor-decode-block shim.
-- Decodes a full Conway-era block (header + tx-sequence).
{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE TypeApplications    #-}
module Main (main) where

import qualified Data.ByteString.Lazy as BSL
import           System.Exit                     (ExitCode (..), exitSuccess, exitWith)

import           Cardano.Ledger.Api.Era          (ConwayEra, eraProtVerLow)
import           Cardano.Ledger.Block            (Block)
import           Cardano.Ledger.Binary.Decoding  (DecCBOR (decCBOR), decodeFullAnnotator)
import           Cardano.Protocol.Crypto         (StandardCrypto)
import           Ouroboros.Consensus.Protocol.Praos.Header (Header)

main :: IO ()
main = do
  bytes <- BSL.getContents
  case decodeFullAnnotator (eraProtVerLow @ConwayEra) "Block" decCBOR bytes of
    Left err                                              -> do
      putStrLn ("ERR " ++ show err)
      exitWith (ExitFailure 1)
    Right (_ :: Block (Header StandardCrypto) ConwayEra)  -> do
      putStrLn "OK"
      exitSuccess
