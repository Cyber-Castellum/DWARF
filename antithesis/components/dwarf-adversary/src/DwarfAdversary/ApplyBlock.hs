{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE TypeApplications #-}
{-# LANGUAGE DataKinds #-}
{-# LANGUAGE PatternSynonyms #-}

-- |
-- Shared applyBlock surface for both fuzzing backends:
--   * the AFL/SanCov harness (dwarf-decode-any, DWARF_DECODER=applyblock)
--   * the Antithesis in-process SDK harness (dwarf-decoder-fuzz --target applyblock)
--
-- Decodes a Conway 'Tx', wraps it as a single-tx block body, builds a
-- 'Block' 'BHeaderView' whose body hash and size MATCH (so the BBODY structural
-- checks pass and execution reaches the per-tx LEDGERS rules), and runs the full
-- BBODY STS (-> LEDGERS -> per-tx LEDGER: UTXOW/UTXO/CERTS ...) via
-- 'applyBlockEither' over a genesis-initialised Conway 'NewEpochState'.
--
-- The initial NewEpochState is built once per process (NOINLINE CAF) from the
-- real devnet genesis files (Shelley+Alonzo+Conway, Conway-hard-fork-at-0) via
-- the same transition-config path the node uses. Genesis dir from
-- DWARF_GENESIS_DIR (default "genesis").
module DwarfAdversary.ApplyBlock
    ( ApplyBlockOutcome (..)
    , applyBlockOutcome
    , conwayInitialNES
    , ledgerGlobals
    ) where

import qualified Cardano.Crypto.Hash as Hash
import Cardano.Ledger.Alonzo.Genesis (AlonzoGenesis)
import Cardano.Ledger.Api (Tx)
import Cardano.Ledger.Api.Era (ConwayEra, eraProtVerLow)
import Cardano.Ledger.Api.PParams (emptyPParams)
import Cardano.Ledger.Api.Transition (createInitialState, mkLatestTransitionConfig)
import Cardano.Ledger.BaseTypes
    ( Globals (..)
    , Network (Testnet)
    , ProtVer
    , boundRational
    , mkActiveSlotCoeff
    )
import Cardano.Ledger.BaseTypes.NonZero (unsafeNonZero)
import Cardano.Ledger.BHeaderView (BHeaderView (..))
import Cardano.Ledger.Binary (DecCBOR (decCBOR))
import Cardano.Ledger.Binary.Decoding (decodeFullAnnotator)
import Cardano.Ledger.Block (Block, pattern UnsafeUnserialisedBlock)
import Cardano.Ledger.Conway.Genesis (ConwayGenesis)
import Cardano.Ledger.Core (bBodySize, hashTxSeq, ppProtocolVersionL, toTxSeq)
import Cardano.Ledger.Hashes (KeyHash (KeyHash))
import Cardano.Ledger.Shelley.API.Validation (applyBlockEither)
import Cardano.Ledger.Shelley.Genesis (ShelleyGenesis)
import Cardano.Ledger.Shelley.LedgerState (NewEpochState, curPParamsEpochStateL, nesEs)
import Cardano.Slotting.EpochInfo (fixedEpochInfo, hoistEpochInfo)
import Cardano.Slotting.Slot (EpochSize (..), SlotNo (..))
import Cardano.Slotting.Time (SystemStart (..), mkSlotLength)
import Control.State.Transition.Extended (SingEP (EPDiscard), ValidationPolicy (ValidateAll))
import qualified Data.Aeson as Aeson
import qualified Data.ByteString.Char8 as BSC
import qualified Data.ByteString.Lazy as LBS
import Data.Functor.Identity (runIdentity)
import Data.Maybe (fromJust, fromMaybe)
import Data.Ratio ((%))
import qualified Data.Sequence.Strict as StrictSeq
import Data.Time (UTCTime (..), fromGregorian)
import Lens.Micro ((^.))
import System.Environment (lookupEnv)
import System.FilePath ((</>))
import System.IO.Unsafe (unsafePerformIO)

-- | Outcome of applying a (mutated) input as a Conway block body.
data ApplyBlockOutcome
    = AbTxDecodeFail
    -- ^ input is not a decodable Conway Tx (clean reject)
    | AbRejected !String
    -- ^ BBODY/LEDGERS rejected the block (clean reject; detail truncated)
    | AbAccepted
    -- ^ block applied without predicate failures (rare with the empty genesis UTxO)

-- Fixed Conway globals (preprod-ish constants) for the rule pipeline.
ledgerGlobals :: Globals
ledgerGlobals = Globals
    { epochInfo = hoistEpochInfo (Right . runIdentity)
        (fixedEpochInfo (EpochSize 432000) (mkSlotLength 1))
    , slotsPerKESPeriod = 129600
    , stabilityWindow = 2160
    , randomnessStabilisationWindow = 2160
    , securityParameter = unsafeNonZero 2160
    , maxKESEvo = 62
    , quorum = 5
    , maxLovelaceSupply = 45000000000000000
    , activeSlotCoeff = mkActiveSlotCoeff (fromJust (boundRational (1 % 20)))
    , networkId = Testnet
    , systemStart = SystemStart (UTCTime (fromGregorian 2017 9 23) 0)
    }

-- | The initial Conway NewEpochState, built once per process from genesis.
{-# NOINLINE conwayInitialNES #-}
conwayInitialNES :: NewEpochState ConwayEra
conwayInitialNES = unsafePerformIO $ do
    dir <- fromMaybe "genesis" <$> lookupEnv "DWARF_GENESIS_DIR"
    sg <- decodeGenesis @ShelleyGenesis (dir </> "shelley-genesis.json")
    ag <- decodeGenesis @AlonzoGenesis (dir </> "alonzo-genesis.json")
    cg <- decodeGenesis @ConwayGenesis (dir </> "conway-genesis.json")
    pure $! createInitialState (mkLatestTransitionConfig sg ag cg)
  where
    decodeGenesis :: Aeson.FromJSON a => FilePath -> IO a
    decodeGenesis fp = do
        r <- Aeson.eitherDecodeFileStrict' fp
        case r of
            Right x -> pure x
            Left e -> error ("genesis decode " <> fp <> ": " <> e)

-- | Decode the bytes as a Conway Tx and run them through the full BBODY STS.
applyBlockOutcome :: LBS.ByteString -> ApplyBlockOutcome
applyBlockOutcome b = case decodeFullAnnotator (eraProtVerLow @ConwayEra) "Tx" decCBOR b of
    Left _ -> AbTxDecodeFail
    Right (tx :: Tx ConwayEra) ->
        let txSeq = toTxSeq @ConwayEra (StrictSeq.fromList [tx])
            nes = conwayInitialNES
            protVer :: ProtVer
            protVer = nesEs nes ^. curPParamsEpochStateL . ppProtocolVersionL
            bh = BHeaderView
                { bhviewID = KeyHash (Hash.castHash (Hash.hashWith id (BSC.pack "dwarf")))
                , bhviewBSize = fromIntegral (bBodySize protVer txSeq)
                , bhviewHSize = 0
                , bhviewBHash = hashTxSeq @ConwayEra txSeq
                , bhviewSlot = SlotNo 0
                }
            block :: Block BHeaderView ConwayEra
            block = UnsafeUnserialisedBlock bh txSeq
         in case applyBlockEither EPDiscard ValidateAll ledgerGlobals nes block of
                Left e -> AbRejected (take 240 (show e))
                Right _ -> AbAccepted
