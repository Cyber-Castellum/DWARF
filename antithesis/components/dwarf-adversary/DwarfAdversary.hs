{-# OPTIONS_GHC -Wno-orphans #-}

-- |
-- Module: DwarfAdversary
--
-- Chain-point parsing and random-pick helpers used by the
-- @cardano-adversary@ executable. The chain-sync attack itself
-- lives in 'DwarfAdversary.Application'.
module DwarfAdversary
    ( -- * Chain points
      Point
    , originPoint
    , readChainPoint
    , showChainPoint
    , ChainPointSamples (..)
    , parseChainPointSamples

      -- * Seeded random pick
    , pickOne
    )
where

import DwarfAdversary.ChainSync.Codec (Point)
import DwarfAdversary.ChainSync.Connection (HeaderHash)
import Data.Aeson (FromJSON, ToJSON, withText)
import Data.Aeson qualified as Aeson
import Data.ByteString.Base16 qualified as B16
import Data.ByteString.Short qualified as SBS
import Data.List.NonEmpty (NonEmpty)
import Data.List.NonEmpty qualified as NE
import Data.Maybe (fromMaybe)
import Data.Text qualified as T
import Data.Text.Encoding qualified as T
import Ouroboros.Consensus.HardFork.Combinator qualified as Consensus
import Ouroboros.Network.Block (SlotNo (..))
import Ouroboros.Network.Block qualified as Network
import Ouroboros.Network.Point (WithOrigin (..))
import Ouroboros.Network.Point qualified as Point
import System.Random (StdGen, randomR)
import Text.Read (readMaybe)

originPoint :: Point
originPoint = Network.Point Origin

instance ToJSON Point where
    toJSON = Aeson.toJSON . showChainPoint

instance FromJSON Point where
    parseJSON = withText "point" $ \t ->
        maybe
            (fail $ "not a point: " <> T.unpack t)
            pure
            (readChainPoint $ T.unpack t)

newtype ChainPointSamples = ChainPointSamples (NonEmpty Point)
    deriving (Eq, Show)

-- | One chain point per line, plus an implicit 'originPoint' at the
-- head so a freshly-started tracer-sidecar (zero entries) still
-- yields a usable sample.
parseChainPointSamples :: String -> Maybe ChainPointSamples
parseChainPointSamples =
    fmap (ChainPointSamples . (originPoint NE.:|))
        . mapM readChainPoint
        . lines

readChainPoint :: String -> Maybe Point
readChainPoint "origin" = Just originPoint
readChainPoint str = case split (== '@') str of
    [blockHashStr, slotNoStr] -> do
        (hash :: HeaderHash) <-
            Consensus.OneEraHash . SBS.toShort
                <$> either
                    (const Nothing)
                    Just
                    ( B16.decode
                        $ T.encodeUtf8
                        $ T.pack blockHashStr
                    )
        slot <- SlotNo <$> readMaybe slotNoStr
        return $ Network.Point $ At $ Point.Block slot hash
    _ -> Nothing
  where
    split f = map T.unpack . T.split f . T.pack

showChainPoint :: Point -> String
showChainPoint (Network.Point Origin) = "origin"
showChainPoint (Network.Point (At (Point.Block (SlotNo slot) hash))) =
    show hash <> "@" <> show slot

-- | Uniformly pick one element from a non-empty list using the
-- provided generator.
pickOne :: StdGen -> NonEmpty a -> a
pickOne g xs =
    let (i, _) = randomR (0, length xs - 1) g
    in  xs NE.!! i
