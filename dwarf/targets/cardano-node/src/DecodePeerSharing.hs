{-# LANGUAGE OverloadedStrings #-}
-- | cardano-node-mini-protocol-decode-peersharing shim.
-- Decodes one PeerSharing mini-protocol message envelope.
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

termWord8 :: String -> CBOR.Term -> Either String Word
termWord8 name (CBOR.TInt n)
  | n >= 0 && n <= 255 = Right (fromIntegral n)
  | otherwise = Left (name ++ " out of Word8 range: " ++ show n)
termWord8 name (CBOR.TInteger n)
  | n >= 0 && n <= 255 = Right (fromIntegral n)
  | otherwise = Left (name ++ " out of Word8 range: " ++ show n)
termWord8 name other = Left (name ++ " expected unsigned integer, got " ++ show other)

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

validateAddress :: CBOR.Term -> Either String ()
validateAddress term = do
  xs <- termList term
  case xs of
    [keyTerm, ipv4, port] -> do
      key <- termWord16 "peer address key" keyTerm
      if key == 0
         then termWord32 "ipv4 word" ipv4 >> termWord16 "ipv4 port" port >> Right ()
         else Left ("unexpected peer address key " ++ show key)
    [keyTerm, word1, word2, word3, word4, port] -> do
      key <- termWord16 "peer address key" keyTerm
      if key == 1
         then do
           _ <- termWord32 "ipv6 word1" word1
           _ <- termWord32 "ipv6 word2" word2
           _ <- termWord32 "ipv6 word3" word3
           _ <- termWord32 "ipv6 word4" word4
           _ <- termWord16 "ipv6 port" port
           Right ()
         else Left ("unexpected peer address key " ++ show key)
    _ -> Left ("unexpected peer address length " ++ show (length xs))

validate :: CBOR.Term -> Either String ()
validate term = do
  xs <- termList term
  case xs of
    [keyTerm, amount] -> do
      key <- termWord16 "message key" keyTerm
      case key of
        0 -> termWord8 "share amount" amount >> Right ()
        1 -> mapM_ validateAddress =<< termList amount
        _ -> Left ("unexpected peersharing message key " ++ show key)
    [keyTerm] -> do
      key <- termWord16 "message key" keyTerm
      if key == 2
         then Right ()
         else Left ("unexpected peersharing message key " ++ show key)
    _ -> Left ("unexpected peersharing message length " ++ show (length xs))

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
