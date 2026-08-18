{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE ScopedTypeVariables #-}

module FuzzSpec (spec) where

import Codec.CBOR.Read (deserialiseFromBytes)
import Codec.CBOR.Term (Term (..), decodeTerm, encodeTerm)
import Codec.CBOR.Write (toLazyByteString)
import Data.ByteString.Lazy qualified as LBS
import Data.Maybe (listToMaybe)
import Data.Set qualified as Set
import Data.Text qualified as T
import DwarfAdversary.Fuzz (MutationInfo (..), mutateTerm)
import DwarfAdversary.TxSubmission.Target (TxField (..), mutateTxField)
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

-- A wire GenTx carrying a Conway governance proposal: the inner Conway tx array's
-- tx_body map has proposal_procedures at key 20 (verified against real gov wire
-- GenTxs, whose tx_body keys are [0,1,2,20]). Envelope shape is [6, tag24(tx)],
-- exactly as the codec hands 'mutateTxField'.
baseTermGov :: Term
baseTermGov =
    let govTx =
            TList
                [ TMap
                    [ (TInt 0, TListI [TList [TBytes "txin", TInt 0]])            -- inputs
                    , (TInt 20, TListI [TList [TInt 500, TBytes "gov-action"]])   -- proposal_procedures
                    ]
                , TMap [(TInt 0, TListI [TList [TBytes "vkey", TBytes "sig"]])]   -- witness_set
                , TBool True                                                      -- is_valid
                , TNull                                                           -- auxiliary_data (none)
                ]
    in  TList
            [ TInt 6
            , TTagged 24 (TBytes (LBS.toStrict (toLazyByteString (encodeTerm govTx))))
            ]

-- A wire GenTx carrying a Plutus witness: the inner Conway tx array's witness_set
-- (index 1) map has redeemers at key 5. Envelope shape is [6, tag24(tx)], exactly
-- as the codec hands 'mutateTxField'. tx_body carries key 0 so 'locateTxArray'
-- finds the tx array; witness_set carries key 5 so the plutus shape engages.
baseTermPlutus :: Term
baseTermPlutus =
    let plutusTx =
            TList
                [ TMap [(TInt 0, TListI [TList [TBytes "txin", TInt 0]])]      -- tx_body: inputs (key 0)
                , TMap [(TInt 5, TListI [TList [TInt 0, TBytes "redeemer"]])]  -- witness_set: redeemers (key 5)
                , TBool True                                                   -- is_valid
                , TNull                                                        -- auxiliary_data (none)
                ]
    in  TList
            [ TInt 6
            , TTagged 24 (TBytes (LBS.toStrict (toLazyByteString (encodeTerm plutusTx))))
            ]

-- A wire GenTx whose witness_set carries the FULL Plutus set: key 4 = datums,
-- key 5 = redeemers, key 7 = PlutusV3 scripts. Used to check that the plutus shape
-- spreads across all three keys, not just redeemers.
baseTermPlutusFull :: Term
baseTermPlutusFull =
    let plutusTx =
            TList
                [ TMap [(TInt 0, TListI [TList [TBytes "txin", TInt 0]])]         -- tx_body: inputs (key 0)
                , TMap
                    [ (TInt 4, TListI [TBytes "datum-aaaaaaaa"])                  -- 4 = datums
                    , (TInt 5, TListI [TList [TInt 0, TBytes "redeemer-bbbb"]])   -- 5 = redeemers
                    , (TInt 7, TListI [TBytes "plutus-v3-script-cccc"])           -- 7 = PlutusV3 scripts
                    ]
                , TBool True                                                     -- is_valid
                , TNull                                                          -- auxiliary_data (none)
                ]
    in  TList
            [ TInt 6
            , TTagged 24 (TBytes (LBS.toStrict (toLazyByteString (encodeTerm plutusTx))))
            ]

-- | The witness_set (tx-array index 1) map key/value pairs of a wire GenTx
-- @[6, tag24(tx)]@, or @[]@ if the shape does not match.
witnessKVs :: Term -> [(Term, Term)]
witnessKVs t = case t of
    TList [_, TTagged 24 (TBytes bs)] ->
        case deserialiseFromBytes decodeTerm (LBS.fromStrict bs) of
            Right (rest, tx) | LBS.null rest -> witsOf tx
            _ -> []
    _ -> []
  where
    witsOf (TList (_ : w : _)) = mapKVs w
    witsOf (TListI (_ : w : _)) = mapKVs w
    witsOf _ = []
    mapKVs (TMap kvs) = kvs
    mapKVs (TMapI kvs) = kvs
    mapKVs _ = []

-- | Which witness_set key's value changed between the original and the mutated
-- wire GenTx (the plutus shape mutates exactly one witness_set key's value).
changedWitnessKey :: Term -> Term -> Maybe Term
changedWitnessKey orig mutated =
    listToMaybe
        [ k
        | (k, ov) <- witnessKVs orig
        , Just mv <- [lookup k (witnessKVs mutated)]
        , ov /= mv
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

    -- A block-shaped witness (header + body + nested tx-ish lists) for the
    -- block-fetch path: the same engine must mutate it without a Haskell
    -- exception and the result must stay encodable.
    describe "mutateTerm on a block-shaped Term" $
        it "mutates without crashing and stays encodable, any seed" $
            property $ \(seed :: Int) ->
                let blockTerm =
                        TList
                            [ TList [TInt 1, TBytes "header-hash", TInt 7]
                            , TListI
                                [ TList [TBytes "tx0", TInt 100]
                                , TList [TBytes "tx1", TInt 200]
                                ]
                            , TBytes "auxiliary"
                            ]
                    (t', _) = mutateTerm (mkStdGen seed) 1.0 blockTerm
                in  LBS.length (toLazyByteString (encodeTerm t')) `seq` True

    -- A representative Conway-tx-shaped Term: [tx_body(map; certs at key 4),
    -- witness_set, is_valid, auxiliary_data]. Used to test sub-field targeting.
    describe "mutateTxField targeting" $ do
        let sampleTx =
                TList
                    [ TMap
                        [ (TInt 0, TListI [TList [TBytes "txin", TInt 0]])
                        , (TInt 4, TListI [TList [TInt 0, TBytes "poolid"]])
                        ]
                    , TMap [(TInt 0, TListI [TList [TBytes "vkey-32-byte-stand-in-aaaaaaaaaa", TBytes "sig-64-byte-stand-in-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]])]
                    , TBool True
                    , TMap [(TInt 0, TBytes "metadata")]
                    ]
            -- The HardFork N2N GenTx envelope as it arrives off the wire:
            -- [eraIndex, CBOR-in-CBOR (tag 24) holding the real tx]. This is what
            -- the codec actually hands mutateTxField — the bare array never is.
            envelope tx =
                TList
                    [ TInt 6
                    , TTagged 24 (TBytes (LBS.toStrict (toLazyByteString (encodeTerm tx))))
                    ]
            reEncodes t = LBS.length (toLazyByteString (encodeTerm t)) `seq` True
        it "WholeTx mutates the tx_body and stays encodable" $ property $ \(s :: Int) ->
            reEncodes (fst (mutateTxField WholeTx (mkStdGen s) 1.0 sampleTx))
        it "Certificate targets the certs sub-term (tagged cert:)" $ do
            let (t, info) = mutateTxField Certificate (mkStdGen 3) 1.0 sampleTx
            t `shouldNotBe` sampleTx
            T.isPrefixOf "cert:" (miKind info) `shouldBe` True
        it "AuxData targets the aux-data element (tagged aux:)" $ do
            let (t, info) = mutateTxField AuxData (mkStdGen 5) 1.0 sampleTx
            t `shouldNotBe` sampleTx
            T.isPrefixOf "aux:" (miKind info) `shouldBe` True
        -- Regression for the silent-fallback bug (FU4): on the ENVELOPED GenTx
        -- the targeting must unwrap the era tag + tag-24 CBOR-in-CBOR and still
        -- engage the cert / aux sub-field, not fall back to the envelope.
        it "Certificate engages on the enveloped GenTx (cert:, not fallback)" $ do
            let env = envelope sampleTx
                (t, info) = mutateTxField Certificate (mkStdGen 3) 1.0 env
            T.isPrefixOf "cert:" (miKind info) `shouldBe` True
            t `shouldNotBe` env
            reEncodes t `shouldBe` True
        it "AuxData engages on the enveloped GenTx (aux:, not fallback)" $ do
            let env = envelope sampleTx
                (_, info) = mutateTxField AuxData (mkStdGen 5) 1.0 env
            T.isPrefixOf "aux:" (miKind info) `shouldBe` True
        it "enveloped mutation stays a decodable wire GenTx" $ do
            let env = envelope sampleTx
                (t, _) = mutateTxField Certificate (mkStdGen 3) 1.0 env
                bytes = toLazyByteString (encodeTerm t)
            case deserialiseFromBytes decodeTerm bytes of
                Right (rest, _) -> LBS.null rest `shouldBe` True
                Left e -> expectationFailure (show e)
        it "missing field falls back to whole-tx (no crash)" $ do
            let (t, info) = mutateTxField Certificate (mkStdGen 1) 1.0 (TList [TMap [], TBool True])
            T.isPrefixOf "fallback:" (miKind info) `shouldBe` True
            reEncodes t `shouldBe` True
        -- FU3c-deep: Witness tampering must change the signature but leave tx_body
        -- byte-identical (txid stable → node accepts into mempool → ledger rejects).
        it "Witness flips a sig byte, leaves tx_body intact (tagged wit:)" $ do
            let (t, info) = mutateTxField Witness (mkStdGen 4) 1.0 sampleTx
            T.isPrefixOf "wit:" (miKind info) `shouldBe` True
            t `shouldNotBe` sampleTx
            case (sampleTx, t) of
                (TList (b0 : _), TList (b0' : _)) -> b0' `shouldBe` b0 -- tx_body unchanged
                _ -> expectationFailure "unexpected tx shape"
            reEncodes t `shouldBe` True
        it "Witness engages on the enveloped GenTx (wit:, not fallback)" $ do
            let env = envelope sampleTx
                (t, info) = mutateTxField Witness (mkStdGen 4) 1.0 env
            T.isPrefixOf "wit:" (miKind info) `shouldBe` True
            t `shouldNotBe` env
            reEncodes t `shouldBe` True

    describe "governance shape targeting" $ do
        it "engages on proposal_procedures (key 20), tagging gov: not fallback:" $ do
            -- baseTermGov: a wire GenTx Term whose tx_body map has TInt 20 => proposal_procedures
            let (t', info) = mutateTxField GovAction (mkStdGen 11) 1.0 baseTermGov
            (T.isPrefixOf "gov:" (miKind info)) `shouldBe` True
            (t' /= baseTermGov) `shouldBe` True

    describe "plutus shape targeting" $
        it "engages on redeemers (witness_set key 5), tagging plutus: not fallback:" $ do
            let (t', info) = mutateTxField PlutusWitness (mkStdGen 13) 1.0 baseTermPlutus
            T.isPrefixOf "plutus:" (miKind info) `shouldBe` True
            (t' /= baseTermPlutus) `shouldBe` True

    describe "plutus shape targeting (whole witness set)" $ do
        it "engages on the full witness set (keys 4/5/7 present), tagging plutus:" $ do
            let (t', info) = mutateTxField PlutusWitness (mkStdGen 21) 1.0 baseTermPlutusFull
            T.isPrefixOf "plutus:" (miKind info) `shouldBe` True
            (t' /= baseTermPlutusFull) `shouldBe` True
        it "spreads across witness_set keys 4/5/7 (not pinned to key 5)" $ do
            let ks =
                    Set.fromList
                        [ k
                        | s <- [1 .. 60 :: Int]
                        , let (t', _) = mutateTxField PlutusWitness (mkStdGen s) 1.0 baseTermPlutusFull
                        , Just k <- [changedWitnessKey baseTermPlutusFull t']
                        ]
            Set.size ks `shouldSatisfy` (> 1)
