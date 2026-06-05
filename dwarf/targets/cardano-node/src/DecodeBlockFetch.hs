{-# LANGUAGE OverloadedStrings #-}
-- | cardano-node-mini-protocol-decode-blockfetch shim.
-- Decodes one BlockFetch mini-protocol wire-format message.
module Main (main) where

import qualified Codec.CBOR.Decoding              as CBOR
import qualified Codec.CBOR.Read                  as CBOR
import qualified Data.ByteString                  as BS
import qualified Data.ByteString.Lazy             as BSL
import           System.Exit                      (ExitCode (..), exitSuccess, exitWith)

decodePoint :: CBOR.Decoder s ()
decodePoint = do
  len <- CBOR.decodeListLen
  case len of
    0 -> pure ()
    2 -> do
      _slot <- CBOR.decodeWord64
      hash <- CBOR.decodeBytes
      if BS.length hash == 32
         then pure ()
         else fail ("point hash length " ++ show (BS.length hash) ++ ", expected 32")
    _ -> fail ("point length " ++ show len ++ ", expected 0 or 2")

decoder :: CBOR.Decoder s ()
decoder = do
  len <- CBOR.decodeListLen
  key <- CBOR.decodeWord
  case (len, key) of
    (3, 0) -> decodePoint >> decodePoint
    (1, 1) -> pure ()
    (1, 2) -> pure ()
    (1, 3) -> pure ()
    (2, 4) -> do
      tag <- CBOR.decodeTag
      if tag == 24
         then pure ()
         else fail ("block tag " ++ show tag ++ ", expected 24")
      _bytes <- CBOR.decodeBytes
      pure ()
    (1, 5) -> pure ()
    _      -> fail ("unexpected blockfetch message (len, key) = (" ++ show len ++ ", " ++ show key ++ ")")

main :: IO ()
main = do
  bytes <- BSL.getContents
  case CBOR.deserialiseFromBytes decoder bytes of
    Left err -> do
      putStrLn ("ERR " ++ show err)
      exitWith (ExitFailure 1)
    Right (rest, ()) | BSL.null rest -> do
      putStrLn "OK"
      exitSuccess
    Right _ -> do
      putStrLn "ERR trailing bytes after message"
      exitWith (ExitFailure 1)
