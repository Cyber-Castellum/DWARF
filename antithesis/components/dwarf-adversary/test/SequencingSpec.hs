{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE ScopedTypeVariables #-}

module SequencingSpec (spec) where

import qualified Data.ByteString.Lazy as LBS
import qualified Data.Map.Strict as Map
import qualified Data.Set as Set
import Data.Maybe (fromJust)
import Data.Word (Word64)
import Test.Hspec
import Test.QuickCheck (property)
import DwarfAdversary.Sequencing.Model
import DwarfAdversary.Sequencing.Generate
import DwarfAdversary.StateMachine
  (connSelector, protocolSalt, acceptedFrameThreshold, isAcceptedIllegal)

spec :: Spec
spec = do
  describe "M3 model embedding + parse" $ do
    it "exposes all four target protocols" $
      Map.keys allModels `shouldBe` ["blockfetch", "chainsync", "keepalive", "txsubmission"]

    it "chainsync parses with the known idle opener frame" $ do
      let m = fromJust (Map.lookup "chainsync" allModels)
      pmInitial m `shouldBe` "idle"
      -- request-next (8100) is a legal frame from idle
      let idleFrames = map frBytes (Map.findWithDefault [] "idle" (pmByState m))
      LBS.pack [0x81, 0x00] `elem` idleFrames `shouldBe` True

    it "every frame's hex base16-decoded to non-empty bytes" $
      all (\m -> all (not . LBS.null . frBytes) (pmFrames m)) (Map.elems allModels)
        `shouldBe` True

    it "every model has a reachable terminal state" $
      all (not . null . pmTerminal) (Map.elems allModels) `shouldBe` True

  describe "generateIllegalSequence" $ do
    let cs = fromJust (Map.lookup "chainsync" allModels)

    it "is deterministic: same selector -> identical frames + class" $
      property $ \(w :: Word64) ->
        let a = generateIllegalSequence cs w
            b = generateIllegalSequence cs w
        in  gsFrames a == gsFrames b && gsDeparture a == gsDeparture b

    it "is distinct across selectors (sampled)" $ do
      let seqs = [ gsFrames (generateIllegalSequence cs w) | w <- [0..63] ]
      length (Set.fromList seqs) `shouldSatisfy` (> 12)

    it "always emits at least one frame" $
      property $ \(w :: Word64) ->
        not (null (gsFrames (generateIllegalSequence cs w)))

    it "single-frame departures are genuinely illegal from the walked state" $
      property $ \(w :: Word64) ->
        let g = generateIllegalSequence cs w
        in  gsDeparture g `elem` [Flood, Duplicate]   -- multiplicity classes exempt
              || departureIsIllegal cs g

    it "covers every departure class across a sample" $ do
      let classes = Set.fromList [ gsDeparture (generateIllegalSequence cs w) | w <- [0..255] ]
      classes `shouldSatisfy` (\s -> Set.size s >= 4)

  describe "responder seeding (connSelector / protocolSalt)" $ do
    let protoNames = map pmProtocol (Map.elems allModels)   -- the 4 real protocols

    it "protocolSalt is distinct per protocol (length alone collides chainsync/keepalive)" $
      length (Set.fromList (map protocolSalt protoNames)) `shouldBe` length protoNames

    it "connSelector decorrelates the protocols at a fixed seed+index" $
      property $ \(seed :: Word64) (i :: Int) ->
        length (Set.fromList [ connSelector seed i n | n <- protoNames ]) == length protoNames

    it "connSelector is deterministic for the same inputs" $
      property $ \(seed :: Word64) (i :: Int) (k :: Int) ->
        let n = protoNames !! (k `mod` length protoNames)
        in  connSelector seed i n == connSelector seed i n

  describe "accepted-illegal heuristic (isAcceptedIllegal)" $ do
    it "is conservative: prefix-justified replies alone never trip it" $ do
      -- exactly legalPrefixLen replies (in-flight prefix acks) is NOT acceptance
      isAcceptedIllegal 3 3 `shouldBe` False
      isAcceptedIllegal 0 0 `shouldBe` False

    it "trips only past prefix + threshold margin" $ do
      isAcceptedIllegal 0 acceptedFrameThreshold `shouldBe` True
      isAcceptedIllegal 0 (acceptedFrameThreshold - 1) `shouldBe` False
      isAcceptedIllegal 2 (2 + acceptedFrameThreshold) `shouldBe` True
      isAcceptedIllegal 2 (2 + acceptedFrameThreshold - 1) `shouldBe` False

    it "threshold is conservative (>= 3)" $
      acceptedFrameThreshold `shouldSatisfy` (>= 3)

  describe "role-relative departures" $ do
    let cs = fromJust (Map.lookup "chainsync" allModels)
    it "AsResponder WrongAgency emits a CLIENT-agency frame (illegal for a server)" $
      property $ \(w :: Word64) ->
        let g = generateIllegalSequenceRole AsResponder cs w
        in  gsDeparture g /= WrongAgency || departureFromAgency cs g == Just ClientAgency
    it "AsInitiator WrongAgency emits a SERVER-agency frame (illegal for a client)" $
      property $ \(w :: Word64) ->
        let g = generateIllegalSequenceRole AsInitiator cs w
        in  gsDeparture g /= WrongAgency || departureFromAgency cs g == Just ServerAgency
    it "legacy generateIllegalSequence == AsResponder (no behavior change)" $
      property $ \(w :: Word64) ->
        gsFrames (generateIllegalSequence cs w) == gsFrames (generateIllegalSequenceRole AsResponder cs w)
