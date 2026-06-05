{-# LANGUAGE OverloadedStrings #-}
-- | cardano-node-mini-protocol-decode-handshake shim.
-- Decodes one Handshake mini-protocol wire-format message:
--   MsgProposeVersions = [0, version_table]
--   MsgAcceptVersion   = [1, version_number, version_data]
--   MsgRefuse          = [2, refuse_reason]
--   MsgQueryReply      = [3, version_table]
module Main (main) where

import           Control.Monad                    (replicateM_)
import qualified Codec.CBOR.Decoding              as CBOR
import qualified Codec.CBOR.Read                  as CBOR
import qualified Data.ByteString.Lazy             as BSL
import           System.Exit                      (ExitCode (..), exitSuccess, exitWith)

decodeVersionData :: Word -> CBOR.Decoder s ()
decodeVersionData version = do
  len <- CBOR.decodeListLen
  let expected = if version >= 11 then 4 else 2
  if len /= expected
     then fail ("version_data length " ++ show len ++ ", expected " ++ show expected)
     else pure ()
  _networkMagic <- CBOR.decodeWord64
  _initiatorOnly <- CBOR.decodeBool
  if version >= 11
     then do
       peerSharing <- CBOR.decodeWord8
       if peerSharing > 1
          then fail ("peer_sharing out of range: " ++ show peerSharing)
          else pure ()
       _query <- CBOR.decodeBool
       pure ()
     else pure ()

decodeVersionTable :: CBOR.Decoder s ()
decodeVersionTable = do
  len <- CBOR.decodeMapLen
  replicateM_ len $ do
    version <- CBOR.decodeWord
    decodeVersionData version

decodeRefuseReason :: CBOR.Decoder s ()
decodeRefuseReason = do
  len <- CBOR.decodeListLen
  key <- CBOR.decodeWord
  case (len, key) of
    (2, 0) -> do
      versions <- CBOR.decodeListLen
      replicateM_ versions (CBOR.decodeWord64 >> pure ())
    (3, 1) -> CBOR.decodeWord64 >> CBOR.decodeString >> pure ()
    (3, 2) -> CBOR.decodeWord64 >> CBOR.decodeString >> pure ()
    _      -> fail ("unexpected refuse_reason (len, key) = (" ++ show len ++ ", " ++ show key ++ ")")

decoder :: CBOR.Decoder s ()
decoder = do
  len <- CBOR.decodeListLen
  key <- CBOR.decodeWord
  case (len, key) of
    (2, 0) -> decodeVersionTable
    (3, 1) -> do
      version <- CBOR.decodeWord
      decodeVersionData version
    (2, 2) -> decodeRefuseReason
    (2, 3) -> decodeVersionTable
    _      -> fail ("unexpected handshake message (len, key) = (" ++ show len ++ ", " ++ show key ++ ")")

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
