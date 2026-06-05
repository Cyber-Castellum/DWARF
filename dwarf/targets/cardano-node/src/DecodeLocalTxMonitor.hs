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

termWord :: String -> CBOR.Term -> Either String Word
termWord name (CBOR.TInt n)
  | n >= 0 = Right (fromIntegral n)
  | otherwise = Left (name ++ " negative: " ++ show n)
termWord name (CBOR.TInteger n)
  | n >= 0 = Right (fromIntegral n)
  | otherwise = Left (name ++ " negative: " ++ show n)
termWord name other = Left (name ++ " expected unsigned integer, got " ++ show other)

validate :: CBOR.Term -> Either String ()
validate term = do
  xs <- termList term
  case xs of
    [keyTerm] -> do
      key <- termWord "message key" keyTerm
      case key of
        0 -> Right ()
        1 -> Right ()
        3 -> Right ()
        4 -> Right ()
        5 -> Right ()
        6 -> Right ()
        9 -> Right ()
        _ -> Left ("unexpected localtxmonitor message key " ++ show key)
    [keyTerm, payload] -> do
      key <- termWord "message key" keyTerm
      case key of
        2 -> termWord "slot" payload >> Right ()
        6 -> Right ()
        7 -> Right ()
        8 -> Right ()
        10 -> case payload of
          CBOR.TList [a, b, c] -> termWord "capacity" a >> termWord "size" b >> termWord "count" c >> Right ()
          CBOR.TListI [a, b, c] -> termWord "capacity" a >> termWord "size" b >> termWord "count" c >> Right ()
          _ -> Left "unexpected size-capacity payload shape"
        _ -> Left ("unexpected localtxmonitor message key " ++ show key)
    _ -> Left ("unexpected localtxmonitor message length " ++ show (length xs))

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
