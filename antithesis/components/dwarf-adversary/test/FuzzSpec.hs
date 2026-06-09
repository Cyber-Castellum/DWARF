{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE ScopedTypeVariables #-}

module FuzzSpec (spec) where

import Codec.CBOR.Read (deserialiseFromBytes)
import Codec.CBOR.Term (Term (..), decodeTerm, encodeTerm)
import Codec.CBOR.Write (toLazyByteString)
import Data.ByteString.Lazy qualified as LBS
import DwarfAdversary.Fuzz (MutationInfo (..), mutateTerm)
import System.Random (mkStdGen)
import Test.Hspec
import Test.QuickCheck (property)

-- A non-trivial, decodable base Term standing in for a header body.
baseTerm :: Term
baseTerm =
    TList
        [ TInt 2
        , TList [TInt 0, TBytes "abcd"]
        , TMap [(TString "slot", TInt 12345), (TString "hash", TBytes "deadbeef")]
        , TListI [TInt 1, TInt 2, TInt 3]
        ]

spec :: Spec
spec = do
    describe "mutateTerm determinism" $
        it "same seed + same rate produces identical output" $
            property $ \(seed :: Int) ->
                let (a, ia) = mutateTerm (mkStdGen seed) 1.0 baseTerm
                    (b, ib) = mutateTerm (mkStdGen seed) 1.0 baseTerm
                in  a == b && miKind ia == miKind ib && miDepth ia == miDepth ib

    describe "mutateTerm effect" $ do
        it "rate 0.0 is the identity" $ do
            let (t, info) = mutateTerm (mkStdGen 7) 0.0 baseTerm
            t `shouldBe` baseTerm
            miKind info `shouldBe` "none"

        it "rate 1.0 changes the Term" $ do
            let (t, _) = mutateTerm (mkStdGen 7) 1.0 baseTerm
            t `shouldNotBe` baseTerm

    describe "mutateTerm output re-encodes" $
        it "the mutated Term round-trips through encodeTerm" $ do
            let (t, _) = mutateTerm (mkStdGen 99) 1.0 baseTerm
                bytes = toLazyByteString (encodeTerm t)
            case deserialiseFromBytes decodeTerm bytes of
                Right (rest, t') -> do
                    LBS.null rest `shouldBe` True
                    t' `shouldBe` t
                Left e -> expectationFailure (show e)
