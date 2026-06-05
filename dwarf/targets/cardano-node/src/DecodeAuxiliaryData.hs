{-# LANGUAGE OverloadedStrings #-}
-- | cardano-node-cbor-decode-auxiliary-data shim.
-- Decodes the concrete 'AlonzoTxAuxData' for ConwayEra (Alonzo+ all use the
-- same AlonzoTxAuxData outer container).
{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE TypeApplications    #-}
module Main (main) where

import qualified Data.ByteString.Lazy as BSL
import           System.Exit                    (ExitCode (..), exitSuccess, exitWith)

import           Cardano.Ledger.Alonzo.TxAuxData (AlonzoTxAuxData)
import           Cardano.Ledger.Api.Era         (ConwayEra, eraProtVerLow)
import           Cardano.Ledger.Binary.Decoding (DecCBOR (decCBOR), decodeFullAnnotator)

main :: IO ()
main = do
  bytes <- BSL.getContents
  case decodeFullAnnotator (eraProtVerLow @ConwayEra) "AlonzoTxAuxData" decCBOR bytes of
    Left err                                   -> do
      putStrLn ("ERR " ++ show err)
      exitWith (ExitFailure 1)
    Right (_ :: AlonzoTxAuxData ConwayEra)     -> do
      putStrLn "OK"
      exitSuccess
