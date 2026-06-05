{-# LANGUAGE OverloadedStrings #-}
-- | cardano-node-mini-protocol-decode-chainsync shim.
-- Decodes one ChainSync mini-protocol wire-format message.
module Main (main) where

import           Control.Monad                    (replicateM_)
import qualified Codec.CBOR.Decoding              as CBOR
import qualified Codec.CBOR.Read                  as CBOR
import qualified Data.ByteString                  as BS
import qualified Data.ByteString.Lazy             as BSL
import           System.Exit                      (ExitCode (..), exitSuccess, exitWith)

expectLen :: Int -> Int -> String -> CBOR.Decoder s ()
expectLen actual expected context =
  if actual == expected
     then pure ()
     else fail (context ++ " length " ++ show actual ++ ", expected " ++ show expected)

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

decodeTip :: CBOR.Decoder s ()
decodeTip = do
  len <- CBOR.decodeListLen
  expectLen len 2 "tip"
  decodePoint
  _height <- CBOR.decodeWord64
  pure ()

decodeHeaderContent :: CBOR.Decoder s ()
decodeHeaderContent = do
  len <- CBOR.decodeListLen
  era <- CBOR.decodeWord8
  case era of
    0 -> do
      expectLen len 2 "byron header_content"
      prefixLen <- CBOR.decodeListLen
      expectLen prefixLen 2 "byron header prefix"
      _prefixA <- CBOR.decodeWord8
      _prefixB <- CBOR.decodeWord64
      _tag <- CBOR.decodeTag
      _bytes <- CBOR.decodeBytes
      pure ()
    _ | era >= 1 && era <= 7 -> do
      expectLen len 2 "header_content"
      _tag <- CBOR.decodeTag
      _bytes <- CBOR.decodeBytes
      pure ()
    _ -> fail ("unknown header_content era variant: " ++ show era)

decoder :: CBOR.Decoder s ()
decoder = do
  len <- CBOR.decodeListLen
  key <- CBOR.decodeWord
  case (len, key) of
    (1, 0) -> pure ()
    (1, 1) -> pure ()
    (3, 2) -> decodeHeaderContent >> decodeTip
    (3, 3) -> decodePoint >> decodeTip
    (2, 4) -> do
      points <- CBOR.decodeListLen
      replicateM_ points decodePoint
    (3, 5) -> decodePoint >> decodeTip
    (2, 6) -> decodeTip
    (1, 7) -> pure ()
    _      -> fail ("unexpected chainsync message (len, key) = (" ++ show len ++ ", " ++ show key ++ ")")

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
