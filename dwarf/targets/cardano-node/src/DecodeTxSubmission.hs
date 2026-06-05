{-# LANGUAGE OverloadedStrings #-}
-- | cardano-node-mini-protocol-decode-txsubmission shim.
-- Decodes one TxSubmission mini-protocol message envelope.
module Main (main) where

import qualified Codec.CBOR.Read                  as CBOR
import qualified Codec.CBOR.Term                  as CBOR
import qualified Data.ByteString.Lazy             as BSL
import           Data.Word                        (Word32)
import           System.Exit                      (ExitCode (..), exitSuccess, exitWith)

termList :: CBOR.Term -> Either String [CBOR.Term]
termList (CBOR.TList xs) = Right xs
termList (CBOR.TListI xs) = Right xs
termList other = Left ("expected list term, got " ++ show other)

termWord16 :: String -> CBOR.Term -> Either String Word
termWord16 name (CBOR.TInt n)
  | n >= 0 && n <= 65535 = Right (fromIntegral n)
  | otherwise = Left (name ++ " out of Word16 range: " ++ show n)
termWord16 name (CBOR.TInteger n)
  | n >= 0 && n <= 65535 = Right (fromIntegral n)
  | otherwise = Left (name ++ " out of Word16 range: " ++ show n)
termWord16 name other = Left (name ++ " expected unsigned integer, got " ++ show other)

termWord32 :: String -> CBOR.Term -> Either String Word32
termWord32 name (CBOR.TInt n)
  | n >= 0 && n <= fromIntegral (maxBound :: Word32) = Right (fromIntegral n)
  | otherwise = Left (name ++ " out of Word32 range: " ++ show n)
termWord32 name (CBOR.TInteger n)
  | n >= 0 && n <= fromIntegral (maxBound :: Word32) = Right (fromIntegral n)
  | otherwise = Left (name ++ " out of Word32 range: " ++ show n)
termWord32 name other = Left (name ++ " expected unsigned integer, got " ++ show other)

validateTxIdSize :: CBOR.Term -> Either String ()
validateTxIdSize term = do
  xs <- termList term
  case xs of
    [_txid, size] -> termWord32 "tx size" size >> Right ()
    _ -> Left ("txid-size pair length " ++ show (length xs) ++ ", expected 2")

validate :: CBOR.Term -> Either String ()
validate term = do
  xs <- termList term
  case xs of
    [keyTerm, CBOR.TBool _blocking, ack, req] -> do
      key <- termWord16 "message key" keyTerm
      if key == 0
         then termWord16 "ack count" ack >> termWord16 "request count" req >> Right ()
         else Left ("unexpected txsubmission message key " ++ show key)
    [keyTerm, payload] -> do
      key <- termWord16 "message key" keyTerm
      case key of
        1 -> mapM_ validateTxIdSize =<< termList payload
        2 -> termList payload >> Right ()
        3 -> termList payload >> Right ()
        _ -> Left ("unexpected txsubmission message key " ++ show key)
    [keyTerm] -> do
      key <- termWord16 "message key" keyTerm
      case key of
        4 -> Right ()
        6 -> Right ()
        _ -> Left ("unexpected txsubmission message key " ++ show key)
    _ -> Left ("unexpected txsubmission message length " ++ show (length xs))

main :: IO ()
main = do
  bytes <- BSL.getContents
  case CBOR.deserialiseFromBytes CBOR.decodeTerm bytes of
    Left err -> do
      putStrLn ("ERR " ++ show err)
      exitWith (ExitFailure 1)
    Right (rest, term) | BSL.null rest ->
      case validate term of
        Right () -> do
          putStrLn "OK"
          exitSuccess
        Left msg -> do
          putStrLn ("ERR " ++ msg)
          exitWith (ExitFailure 1)
    Right _ -> do
      putStrLn "ERR trailing bytes after message"
      exitWith (ExitFailure 1)
