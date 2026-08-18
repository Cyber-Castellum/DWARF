{-# LANGUAGE OverloadedStrings #-}

-- | Pure, deterministic generator: given a 'ProtocolModel' and a 'Word64'
-- selector (= seed + connection-index), walk a legal prefix then inject ONE
-- illegal departure. The frame BYTES are well-formed (they decode); the
-- violation is the protocol state / agency / order / multiplicity. Same selector
-- => same output (Antithesis reproduces any timeline from @--seed@).
--
-- Seeding uses the splitmix-backed 'StdGen' from @random@ (already a dependency)
-- rather than a hand-rolled LCG: the only contract the tests enforce is
-- determinism + distinctness, and 'mkStdGen' mixes consecutive selectors well.
--
-- Taxonomy invariant: for every class EXCEPT the multiplicity classes
-- ('Flood' / 'Duplicate'), the first departure frame is drawn from the set of
-- frames whose bytes are NOT legal in the walked state, so the departure is
-- genuinely illegal there by construction (see 'departureIsIllegal').
module DwarfAdversary.Sequencing.Generate
  ( DepartureClass (..)
  , GeneratedSequence (..)
  , AdversaryRole (..)
  , generateIllegalSequence
  , generateIllegalSequenceRole
  , departureIsIllegal      -- test support
  , departureFromAgency     -- test support
  ) where

import qualified Data.ByteString.Lazy as LBS
import Data.List (find)
import qualified Data.Map.Strict as Map
import qualified Data.Set as Set
import Data.Word (Word64)
import System.Random (StdGen, mkStdGen, randomR)

import DwarfAdversary.Sequencing.Model

data DepartureClass
  = WrongAgency | OutOfState | PrematureTerminal | PostTerminal | Flood | Duplicate
  deriving (Eq, Ord, Show, Enum, Bounded)

data GeneratedSequence = GeneratedSequence
  { gsFrames    :: [LBS.ByteString]   -- raw frames to send, in order
  , gsDeparture :: DepartureClass     -- which illegal class (for SDK naming)
  , gsLegalLen  :: Int                -- length of the legal prefix
  } deriving (Eq, Show)

-- | Which side of the mini-protocol the adversary is driving. This fixes the
-- agency of the adversary's OWN messages, hence which frames constitute a
-- wrong-agency violation. The server-responder path (the original soaked
-- surface) is 'AsResponder'; the concurrent initiator pool is 'AsInitiator'.
data AdversaryRole = AsInitiator | AsResponder
  deriving (Eq, Show)

-- | The OTHER party's agency for a given adversary role — the agency a
-- wrong-agency departure frame must carry (a server commits WrongAgency by
-- emitting a client-agency frame, and vice-versa).
otherParty :: AdversaryRole -> Agency
otherParty AsResponder = ClientAgency
otherParty AsInitiator = ServerAgency

-- | Generate a distinct illegal sequence for the selector, as the responder
-- (the adversary's default/server role). Preserves the existing call sites.
generateIllegalSequence :: ProtocolModel -> Word64 -> GeneratedSequence
generateIllegalSequence = generateIllegalSequenceRole AsResponder

-- | Role-parameterized generator. Identical to the single-role engine except the
-- WrongAgency class draws from the OTHER party's frames for @role@
-- ('otherAgencyFrames'); every other class is role-independent.
generateIllegalSequenceRole :: AdversaryRole -> ProtocolModel -> Word64 -> GeneratedSequence
generateIllegalSequenceRole role m sel =
  let g0                = mkStdGen (fromIntegral sel)
      (prefLen, g1)     = randomR (0, 3 :: Int) g0
      (pref, stEnd, g2) = walk m prefLen g1
      cands             = applicableClasses role m stEnd
      (cls, clsFs, g3)  = case cands of
        [] -> (OutOfState, [], g2)
        _  -> let (i, gA)  = randomR (0, length cands - 1) g2
                  (c, fs)  = cands !! i
              in  (c, fs, gA)
      depart            = synthesize m cls clsFs g3
      frames            = map frBytes pref ++ depart
      frames'           = if null frames
                            then take 1 (map frBytes (pmFrames m))
                            else frames
  in GeneratedSequence
       { gsFrames    = frames'
       , gsDeparture = cls
       , gsLegalLen  = length pref
       }

-- | The departure classes the model can express from state @st@, paired with the
-- candidate frame set each one draws from. Only classes with a non-empty
-- candidate set are returned, so 'generateIllegalSequence' never picks an
-- inexpressible class.
applicableClasses :: AdversaryRole -> ProtocolModel -> State -> [(DepartureClass, [Frame])]
applicableClasses role m st =
  let here       = legalHere m st
      hereBytes  = map frBytes here
      illegalAt  = [ f | f <- pmFrames m, frBytes f `notElem` hereBytes ]
      wrongAg    = otherAgencyFrames role m st
      terminalFs = [ f | f <- illegalAt, frTo f `Set.member` pmTerminal m ]
  in  [ (c, fs)
      | (c, fs) <-
          [ (WrongAgency,       wrongAg)
          , (OutOfState,        illegalAt)
          , (PrematureTerminal, terminalFs)
          , (PostTerminal,      terminalFs)
          , (Flood,             here)
          , (Duplicate,         here)
          ]
      , not (null fs) ]

-- | Synthesize the offending frame(s) for the chosen class from its candidates.
synthesize :: ProtocolModel -> DepartureClass -> [Frame] -> StdGen -> [LBS.ByteString]
synthesize m cls fs g = case cls of
  Flood        -> case pickG g fs of Just (f, _) -> replicate 8 (frBytes f); Nothing -> []
  Duplicate    -> case pickG g fs of Just (f, _) -> [frBytes f, frBytes f];  Nothing -> []
  PostTerminal -> case pickG g fs of
                    Just (f, _) -> frBytes f : take 1 (map frBytes (pmFrames m))
                    Nothing     -> []
  _            -> case pickG g fs of Just (f, _) -> [frBytes f]; Nothing -> []

-- ---- helpers ---------------------------------------------------------------

-- | Walk legal transitions from the initial state, up to @maxLen@ steps,
-- returning the visited frames, the final state, and the advanced generator.
walk :: ProtocolModel -> Int -> StdGen -> ([Frame], State, StdGen)
walk m maxLen = go (pmInitial m) 0 []
  where
    go st n acc g
      | n >= maxLen = (reverse acc, st, g)
      | otherwise   = case legalHere m st of
          [] -> (reverse acc, st, g)
          fs -> case pickG g fs of
            Just (f, g') -> go (frTo f) (n + 1) (f : acc) g'
            Nothing      -> (reverse acc, st, g)

pickG :: StdGen -> [a] -> Maybe (a, StdGen)
pickG _ [] = Nothing
pickG g xs = let (i, g') = randomR (0, length xs - 1) g in Just (xs !! i, g')

legalHere :: ProtocolModel -> State -> [Frame]
legalHere m st = Map.findWithDefault [] st (pmByState m)

-- | Frames that constitute a wrong-agency violation from state @st@ for @role@:
-- frames whose SOURCE state carries the OTHER party's agency AND whose bytes are
-- not legal in @st@ (so the departure is also genuinely illegal there). This is
-- role-absolute, not relative to @st@'s agency.
otherAgencyFrames :: AdversaryRole -> ProtocolModel -> State -> [Frame]
otherAgencyFrames role m st =
  [ f | f <- pmFrames m
      , frBytes f `notElem` map frBytes (legalHere m st)
      , Map.lookup (frFrom f) (pmAgency m) == Just (otherParty role) ]

-- | Test support: a single-frame departure must not be a legal transition from
-- the state reached by replaying the (model-legal) prefix.
departureIsIllegal :: ProtocolModel -> GeneratedSequence -> Bool
departureIsIllegal m g =
  case drop (gsLegalLen g) (gsFrames g) of
    (b:_) ->
      let stEnd      = stateAfter m (take (gsLegalLen g) (gsFrames g))
          legalBytes = map frBytes (legalHere m stEnd)
      in  b `notElem` legalBytes
    [] -> False

-- | Replay a byte-prefix back into a state (best-effort; prefix is model-legal).
stateAfter :: ProtocolModel -> [LBS.ByteString] -> State
stateAfter m = foldl step (pmInitial m)
  where step st b = maybe st frTo (find ((== b) . frBytes) (legalHere m st))

-- | Test support: the agency of the SOURCE state of the first post-prefix
-- departure frame (looked up by its bytes). For a WrongAgency departure this is
-- the OTHER party's agency relative to the adversary's role.
departureFromAgency :: ProtocolModel -> GeneratedSequence -> Maybe Agency
departureFromAgency m g =
  case drop (gsLegalLen g) (gsFrames g) of
    (b:_) -> case find ((== b) . frBytes) (pmFrames m) of
               Just f  -> Map.lookup (frFrom f) (pmAgency m)
               Nothing -> Nothing
    [] -> Nothing
