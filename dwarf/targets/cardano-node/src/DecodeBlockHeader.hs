{-# LANGUAGE OverloadedStrings #-}
-- | cardano-node-cbor-decode-block-header shim.
-- Decodes a Praos block header. BHeader is crypto-parameterised; mainnet
-- uses StandardCrypto.
{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE TypeApplications    #-}
module Main (main) where

import qualified Data.ByteString.Lazy as BSL
import           System.Exit                     (ExitCode (..), exitSuccess, exitWith)

import           Cardano.Ledger.Api.Era          (ConwayEra, eraProtVerLow)
import           Cardano.Ledger.Binary.Decoding  (DecCBOR (decCBOR), decodeFullAnnotator)
import           Cardano.Protocol.Crypto         (StandardCrypto)
import           Ouroboros.Consensus.Protocol.Praos.Header (Header)

main :: IO ()
main = do
  bytes <- BSL.getContents
  case decodeFullAnnotator (eraProtVerLow @ConwayEra) "Header" decCBOR bytes of
    Left err                              -> do
      putStrLn ("ERR " ++ show err)
      exitWith (ExitFailure 1)
    Right (_ :: Header StandardCrypto)    -> do
      putStrLn "OK"
      exitSuccess
