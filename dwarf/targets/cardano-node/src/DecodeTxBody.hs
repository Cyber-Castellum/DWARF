{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE TypeApplications    #-}
-- | cardano-node-cbor-decode-tx-body shim.
-- Decodes a Conway-era TxBody (the inner CBOR map of tx fields), aligning
-- with Amaru's amaru_kernel::TransactionBody parser surface.
module Main (main) where

import qualified Data.ByteString.Lazy            as BSL
import           System.Exit                     (ExitCode (..), exitSuccess, exitWith)

import           Cardano.Ledger.Api              (TxBody)
import           Cardano.Ledger.Api.Era          (ConwayEra, eraProtVerLow)
import           Cardano.Ledger.Binary.Decoding  (DecCBOR (decCBOR), decodeFullAnnotator)

main :: IO ()
main = do
  bytes <- BSL.getContents
  case decodeFullAnnotator (eraProtVerLow @ConwayEra) "TxBody" decCBOR bytes of
    Left err                    -> do
      putStrLn ("ERR " ++ show err)
      exitWith (ExitFailure 1)
    Right (_ :: TxBody ConwayEra) -> do
      putStrLn "OK"
      exitSuccess
