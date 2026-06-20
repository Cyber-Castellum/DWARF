{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE RankNTypes #-}
{-# LANGUAGE TypeApplications #-}
{-# LANGUAGE DataKinds #-}

-- |
-- Shared fuzz-surface dispatch for the cardano-node (Haskell) decode + ledger
-- surfaces, selected by name (the DWARF_DECODER value). Used by BOTH the
-- fork-per-exec AFL harness (dwarf-decode-any) and the persistent-mode AFL
-- harness (dwarf-decode-persist), so the two stay in lock-step.
--
-- 'runSurf' forces the surface and returns True on a clean run; a clean reject
-- (DeserialiseFailure / Left / trailing bytes) is False. Any *other* uncaught
-- exception propagates (the harness turns it into SIGABRT = an AFL crash).
module DwarfAdversary.Surfaces (runSurf) where

import Codec.CBOR.Decoding (Decoder)
import Codec.CBOR.Encoding (Encoding)
import Codec.CBOR.Read (deserialiseFromBytes)
import Codec.CBOR.Write (toLazyByteString)
import Control.Exception (evaluate)
import qualified Data.ByteString.Lazy as LBS

import DwarfAdversary.ApplyBlock (ApplyBlockOutcome (..), applyBlockOutcome, ledgerGlobals)
import DwarfAdversary.ChainSync.Codec
  ( decBlock, decHeader, decPoint, decTip, decTx, decTxId
  , encBlock, encHeader, encPoint, encTip, encTx, encTxId
  )
import DwarfAdversary.MiniProtocolDecode (decodeHandshake, decodeKeepAlive, decodeTxSubmission2)

import Cardano.Ledger.Api (Tx, TxBody)
import Cardano.Ledger.Api.Era (ConwayEra, eraProtVerLow)
import Cardano.Ledger.Api.PParams (emptyPParams)
import Cardano.Ledger.Binary (DecCBOR (decCBOR))
import Cardano.Ledger.Binary.Decoding (decodeFullAnnotator)
import qualified Cardano.Ledger.Coin as Coin
import Cardano.Ledger.Conway.UTxO (conwayProducedValue)
import Cardano.Ledger.Core (mkBasicTx)
import Cardano.Ledger.Shelley.API.Mempool (applyTx)
import Cardano.Ledger.Shelley.LedgerState (LedgerState)
import Cardano.Ledger.Shelley.Rules (LedgerEnv (..))
import Cardano.Ledger.UTxO (UTxO (..), getConsumedValue, getMinFeeTxUtxo)
import Cardano.Ledger.Val (coin)
import Cardano.Slotting.Slot (EpochNo (..), SlotNo (..))
import Data.Default (def)
import qualified Data.Map.Strict as Map

-- decode surface = (decoder, re-encoder); force by re-encoding + no trailing bytes.
runSurface :: (forall s. Decoder s a) -> (a -> Encoding) -> LBS.ByteString -> Bool
runSurface dec enc b = case deserialiseFromBytes dec b of
    Right (rest, x) -> LBS.length (toLazyByteString (enc x)) `seq` LBS.null rest
    Left _ -> False

forceTxBody :: LBS.ByteString -> Bool
forceTxBody b = case decodeFullAnnotator (eraProtVerLow @ConwayEra) "TxBody" decCBOR b of
    Left _ -> False
    Right (tb :: TxBody ConwayEra) -> tb `seq` True

forceLedger :: LBS.ByteString -> Bool
forceLedger b = case decodeFullAnnotator (eraProtVerLow @ConwayEra) "TxBody" decCBOR b of
    Left _ -> False
    Right (txBody :: TxBody ConwayEra) ->
        let tx = mkBasicTx txBody
            Coin.Coin mf = getMinFeeTxUtxo emptyPParams tx (UTxO Map.empty)
         in mf `seq` True

forceProduced :: LBS.ByteString -> Bool
forceProduced b = case decodeFullAnnotator (eraProtVerLow @ConwayEra) "TxBody" decCBOR b of
    Left _ -> False
    Right (txBody :: TxBody ConwayEra) ->
        let Coin.Coin v = coin (conwayProducedValue emptyPParams (const False) txBody)
         in v `seq` True

forceConsumed :: LBS.ByteString -> Bool
forceConsumed b = case decodeFullAnnotator (eraProtVerLow @ConwayEra) "TxBody" decCBOR b of
    Left _ -> False
    Right (txBody :: TxBody ConwayEra) ->
        let Coin.Coin v = coin (getConsumedValue emptyPParams (const Nothing) (const Nothing) (UTxO Map.empty) txBody)
         in v `seq` True

forceApplyTx :: LBS.ByteString -> Bool
forceApplyTx b = case decodeFullAnnotator (eraProtVerLow @ConwayEra) "Tx" decCBOR b of
    Left _ -> False
    Right (tx :: Tx ConwayEra) ->
        let st  = def :: LedgerState ConwayEra
            env = LedgerEnv (SlotNo 0) (Just (EpochNo 0)) minBound emptyPParams def
         in case applyTx ledgerGlobals env st tx of
                Left e  -> e `seq` True
                Right r -> r `seq` True

forceApplyBlock :: LBS.ByteString -> Bool
forceApplyBlock b = case applyBlockOutcome b of
    AbTxDecodeFail -> False
    AbRejected s   -> s `seq` True
    AbAccepted     -> True

forceFor :: String -> LBS.ByteString -> Bool
forceFor s = case s of
    "block"      -> runSurface decBlock encBlock
    "header"     -> runSurface decHeader encHeader
    "tip"        -> runSurface decTip encTip
    "txid"       -> runSurface decTxId encTxId
    "point"      -> runSurface decPoint encPoint
    "txbody"     -> forceTxBody
    "ledger"     -> forceLedger
    "produced"   -> forceProduced
    "consumed"   -> forceConsumed
    "applytx"    -> forceApplyTx
    "applyblock" -> forceApplyBlock
    _            -> runSurface decTx encTx

-- IO-valued surfaces (mini-protocol codecs decode in IO); pure surfaces via evaluate.
runSurf :: String -> LBS.ByteString -> IO Bool
runSurf s b = case s of
    "keepalive" -> decodeKeepAlive b
    "txsub"     -> decodeTxSubmission2 b
    "handshake" -> decodeHandshake b
    _           -> evaluate (forceFor s b)
