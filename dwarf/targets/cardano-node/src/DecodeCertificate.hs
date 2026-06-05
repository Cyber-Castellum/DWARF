-- | cardano-node-cbor-decode-certificate shim.
-- Decodes a Conway-era TxCert via the concrete 'ConwayTxCert' data type,
-- avoiding the TxLevel-indexed associated type family in ledger-core 1.19.
{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE TypeApplications    #-}
module Main (main) where

import qualified Data.ByteString.Lazy as BSL
import           System.Exit                    (ExitCode (..), exitSuccess, exitWith)

import           Cardano.Ledger.Api.Era         (ConwayEra, eraProtVerLow)
import           Cardano.Ledger.Binary.Decoding (DecoderError, decodeFull)
import           Cardano.Ledger.Conway.TxCert   (ConwayTxCert)

main :: IO ()
main = do
  bytes <- BSL.getContents
  let result :: Either DecoderError (ConwayTxCert ConwayEra)
      result = decodeFull (eraProtVerLow @ConwayEra) bytes
  case result of
    Left err -> do
      putStrLn ("ERR " ++ show err)
      exitWith (ExitFailure 1)
    Right _  -> do
      putStrLn "OK"
      exitSuccess
