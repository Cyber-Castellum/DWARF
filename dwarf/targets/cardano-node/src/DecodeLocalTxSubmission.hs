{-# LANGUAGE OverloadedStrings #-}
module Main (main) where

import qualified Codec.CBOR.Read      as CBOR
import qualified Codec.CBOR.Term      as CBOR
import qualified Data.ByteString.Lazy as BSL
import           System.Exit          (ExitCode (..), exitSuccess, exitWith)

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

validate :: CBOR.Term -> Either String ()
validate term = do
  xs <- termList term
  case xs of
    [keyTerm, _payload] -> do
      key <- termWord16 "message key" keyTerm
      case key of
        0 -> Right ()
        2 -> Right ()
        _ -> Left ("unexpected localtxsubmission message key " ++ show key)
    [keyTerm] -> do
      key <- termWord16 "message key" keyTerm
      case key of
        1 -> Right ()
        3 -> Right ()
        _ -> Left ("unexpected localtxsubmission message key " ++ show key)
    _ -> Left ("unexpected localtxsubmission message length " ++ show (length xs))

main :: IO ()
main = do
  bytes <- BSL.getContents
  case CBOR.deserialiseFromBytes CBOR.decodeTerm bytes of
    Left err -> do
      putStrLn ("ERR " ++ show err)
      exitWith (ExitFailure 1)
    Right (rest, term) | BSL.null rest ->
      case validate term of
        Right () -> putStrLn "OK" >> exitSuccess
        Left msg -> putStrLn ("ERR " ++ msg) >> exitWith (ExitFailure 1)
    Right _ -> putStrLn "ERR trailing bytes after message" >> exitWith (ExitFailure 1)
