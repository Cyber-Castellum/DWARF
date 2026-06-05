{-# LANGUAGE OverloadedStrings #-}
-- | cardano-node-mini-protocol-decode-keep-alive shim.
-- Decodes a KeepAlive mini-protocol wire-format message
-- (MsgKeepAlive cookie, MsgKeepAliveResponse cookie, or MsgDone).
-- Wire format from ouroboros-network KeepAlive codec_v2:
--   MsgKeepAlive          = [0, cookie:u16]
--   MsgKeepAliveResponse  = [1, cookie:u16]
--   MsgDone               = [2]
module Main (main) where

import qualified Codec.CBOR.Decoding             as CBOR
import qualified Codec.CBOR.Read                 as CBOR
import qualified Data.ByteString.Lazy            as BSL
import           System.Exit                     (ExitCode (..), exitSuccess, exitWith)

decoder :: CBOR.Decoder s ()
decoder = do
  len <- CBOR.decodeListLen
  key <- CBOR.decodeWord
  case (len, key) of
    (2, 0) -> CBOR.decodeWord16 >> pure ()
    (2, 1) -> CBOR.decodeWord16 >> pure ()
    (1, 2) -> pure ()
    _      -> fail ("unexpected (len, key) = (" ++ show len ++ ", " ++ show key ++ ")")

main :: IO ()
main = do
  bytes <- BSL.getContents
  case CBOR.deserialiseFromBytes decoder bytes of
    Left err          -> do
      putStrLn ("ERR " ++ show err)
      exitWith (ExitFailure 1)
    Right (rest, ()) | BSL.null rest -> do
      putStrLn "OK"
      exitSuccess
    Right _ -> do
      putStrLn "ERR trailing bytes after message"
      exitWith (ExitFailure 1)
