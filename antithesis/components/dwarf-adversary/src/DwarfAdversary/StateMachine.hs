{-# LANGUAGE OverloadedStrings #-}

-- |
-- Module: DwarfAdversary.StateMachine
--
-- SP4 state-machine / sequencing fuzz (roadmap R7). Distinct from every codec
-- mutation mode (struct / bytes / semantic / grammar), which corrupt message
-- BYTES: here we send WELL-FORMED ChainSync messages in ILLEGAL protocol states
-- / wrong agency. The bytes decode cleanly; what must reject them is the
-- mini-protocol STATE MACHINE (agency + transition enforcement) and the mux
-- driver — NOT the CBOR decoder. So this exercises a surface none of the
-- decoder fuzzing reaches.
--
-- We drive the raw mux 'Channel' directly, bypassing the typed-protocol peer
-- (which would forbid these sequences at compile time): per connection we pick
-- a scripted illegal sequence, emit it, then drain the node's replies until it
-- tears the connection down (the expected, SAFE outcome of a protocol
-- violation). Determinism: scenario = f(seed, connection-index), so a run
-- cycles all scenarios and Antithesis reproduces any one from the seed.
--
-- The original ChainSync responder ('scriptedChainSyncResponder') is the
-- hand-coded 6-scenario regression-seed path. SP4 adds a generalized,
-- model-driven responder ('scriptedSequenceResponder') that synthesizes a
-- distinct illegal sequence per connection from any protocol's M3 model.
module DwarfAdversary.StateMachine
    ( scriptedChainSyncResponder
    , scriptedSequenceResponder
    , scriptedSequenceInitiator
    , connSelector
    , protocolSalt
    , acceptedFrameThreshold
    , isAcceptedIllegal
    , smScenarioName
    , smScenarioCount
    ) where

import Control.Monad (forM_, when)
import Data.Bits (xor)
import Data.ByteString.Lazy qualified as LBS
import Data.IORef (IORef, atomicModifyIORef')
import Data.Text qualified as T
import Data.Word (Word64)
import Ouroboros.Network.Channel (Channel (..))
import Ouroboros.Network.Mux (MiniProtocolCb (..))
import System.Timeout (timeout)

import DwarfAdversary.Sequencing.Generate
    ( AdversaryRole (..)
    , DepartureClass
    , GeneratedSequence (..)
    , generateIllegalSequence
    , generateIllegalSequenceRole
    )
import DwarfAdversary.Sequencing.Model (ProtocolModel, pmProtocol)

-- Exact ChainSync N2N wire frames (Ouroboros.Network.Protocol.ChainSync.Codec):
--   MsgRequestNext = listLen 1 <> word 0  ->  0x81 0x00   (CLIENT message)
--   MsgAwaitReply  = listLen 1 <> word 1  ->  0x81 0x01   (SERVER message)
--   MsgDone        = listLen 1 <> word 7  ->  0x81 0x07   (CLIENT message)
-- All payload-free, so each decodes cleanly; the violation is purely the
-- protocol state / agency in which we (the server) emit them.
msgRequestNext, msgAwaitReply, msgDone :: LBS.ByteString
msgRequestNext = LBS.pack [0x81, 0x00]
msgAwaitReply  = LBS.pack [0x81, 0x01]
msgDone        = LBS.pack [0x81, 0x07]

smScenarioCount :: Int
smScenarioCount = 6

-- | Human-readable scenario name (also the SDK assertion detail).
smScenarioName :: Int -> String
smScenarioName i = case i `mod` smScenarioCount of
    0 -> "wrong-agency-requestnext"  -- server emits a CLIENT message (tag 0)
    1 -> "wrong-agency-done"         -- server emits MsgDone (client tag 7)
    2 -> "double-awaitreply"         -- 2nd MsgAwaitReply illegal in MustReply
    3 -> "awaitreply-storm"          -- many MsgAwaitReply back-to-back
    4 -> "requestnext-flood"         -- flood the client with its own message
    _ -> "done-then-more"            -- a message AFTER MsgDone

smFrames :: Int -> [LBS.ByteString]
smFrames i = case i `mod` smScenarioCount of
    0 -> [msgRequestNext]
    1 -> [msgDone]
    2 -> [msgAwaitReply, msgAwaitReply]
    3 -> replicate 8 msgAwaitReply
    4 -> replicate 8 msgRequestNext
    _ -> [msgDone, msgAwaitReply]

-- | Fast-drain cap: how many post-departure frames to read while waiting for the
-- node to tear the connection down, with NO per-frame sleep. The old 100ms
-- per-frame/per-drain pacing was a holdover from the human-observable 6-scenario
-- responder and throttled throughput to ~12 injections/min. 8 >= the maximum
-- accepted-illegal threshold (legalPrefixLen <= 3, + acceptedFrameThreshold 3 = 6),
-- so the accepted-illegal detector still has headroom to observe acceptance.
maxDrainFrames :: Int
maxDrainFrames = 8

-- | Per-recv cap for the initiator drain (microseconds). A silent node ends the
-- drain after one quiescent window rather than blocking; the outer per-connection
-- timeout in 'runSMInitiatorOnce' is the hard ceiling, this just keeps the cb
-- itself self-bounded and snappy.
drainRecvTimeoutMicros :: Int
drainRecvTimeoutMicros = 100000

-- | Raw responder for ChainSync (#2): consume the node's opening message, then
-- emit a per-connection illegal sequence and drain its replies. @ctr@ advances
-- the scenario each connection (one run cycles all scenarios); @seed@ phases the
-- starting scenario per timeline.
scriptedChainSyncResponder
    :: (String -> IO ())   -- ^ logger
    -> (String -> IO ())   -- ^ onScenario (SDK hook)
    -> IORef Int           -- ^ per-connection counter
    -> Word64              -- ^ seed (per-timeline phase)
    -> MiniProtocolCb ctx LBS.ByteString IO ()
scriptedChainSyncResponder logMsg onScenario ctr seed =
    MiniProtocolCb $ \_ctx chan -> do
        i <- atomicModifyIORef' ctr (\n -> (n + 1, n))
        let sel  = fromIntegral seed + i
            name = smScenarioName sel
        opened <- recv chan
        logMsg
            ( "state-machine: node opened chainsync ("
                <> maybe "no-msg" (const "msg") opened
                <> "); injecting illegal-sequence scenario = "
                <> name
            )
        onScenario name
        forM_ (smFrames sel) (send chan)
        drain chan maxDrainFrames
        pure ((), Nothing)
  where
    -- Fast bounded drain: read up to maxDrainFrames replies with NO per-iteration
    -- sleep, stopping at the first Nothing (the node tore the connection down).
    drain _    0 = pure ()
    drain chan n = do
        m <- recv chan
        case m of
            Nothing -> pure ()
            Just _  -> drain chan (n - 1)

-- ---------------------------------------------------------------------------
-- Generalized model-driven responder (SP4 generative sequencing)
-- ---------------------------------------------------------------------------

-- | FNV-1a 64-bit hash of a protocol name, used as the per-protocol selector
-- salt: distinct protocols must get distinct salts so their generated sequences
-- decorrelate deterministically. Name LENGTH alone is insufficient — e.g.
-- "chainsync" and "keepalive" are both 9 characters and would collide.
protocolSalt :: T.Text -> Word64
protocolSalt =
    T.foldl' (\h c -> (h `xor` fromIntegral (fromEnum c)) * 0x100000001b3)
             0xcbf29ce484222325

-- | Pure per-connection, per-protocol selector fed to 'generateIllegalSequence'.
-- @seed@ phases the whole timeline; @i@ advances per connection; the protocol
-- salt decorrelates concurrently-running protocols. A pure function of its
-- inputs, so Antithesis reproduces any connection's sequence from the seed.
connSelector :: Word64 -> Int -> T.Text -> Word64
connSelector seed i name = seed + fromIntegral i + protocolSalt name

-- | Conservative margin for the accepted-illegal heuristic: the number of
-- post-departure frames, BEYOND those the legal prefix could justify, that the
-- node must keep exchanging before we treat the illegal departure as POSSIBLY
-- accepted. Kept small but non-trivial; see 'isAcceptedIllegal'.
acceptedFrameThreshold :: Int
acceptedFrameThreshold = 3

-- | Heuristic: did the node look like it ACCEPTED the illegal departure rather
-- than merely replying to the legal prefix that preceded it?
--
-- We send the legal prefix and then the illegal departure before draining, so by
-- drain time the node may have in-flight replies to the (legal) prefix frames —
-- that is NOT acceptance. So we discount up to @legalPrefixLen@ such replies and
-- only flag acceptance when the node keeps exchanging a further
-- 'acceptedFrameThreshold' frames beyond that.
--
-- This is deliberately a HEURISTIC and can false-positive (e.g. a chatty node, or
-- pipelined prefix replies). It therefore only ever drives a @reachable@
-- EXPLORATION signal — never a pass/fail oracle. The real win/lose oracle for
-- "node accepted an illegal sequence" is node LIVENESS, asserted as an @Always@
-- by the soak harness / Antithesis bundle (which can see the node's tip), not by
-- the adversary here.
isAcceptedIllegal :: Int -> Int -> Bool
isAcceptedIllegal legalPrefixLen postDepartureFrames =
    postDepartureFrames >= legalPrefixLen + acceptedFrameThreshold

-- | Model-driven raw responder for ANY mini-protocol: per connection, generate a
-- distinct illegal sequence from the protocol's M3 model, emit it, drain the
-- node's replies. Same recv->send->drain shape as 'scriptedChainSyncResponder';
-- @ctr@ advances the selector per connection, @seed@ phases per timeline, and the
-- protocol id (via 'connSelector') salts the selector so concurrent protocols
-- decorrelate. @onDeparture@ is the SDK hook (protocol, departure class);
-- @onAccepted@ fires the heuristic accepted-illegal exploration signal (see
-- 'isAcceptedIllegal' — exploration only, NOT a pass/fail oracle).
scriptedSequenceResponder
    :: ProtocolModel
    -> (String -> IO ())                    -- ^ logger
    -> (T.Text -> DepartureClass -> IO ())  -- ^ onDeparture (SDK hook): protocol, class
    -> (T.Text -> IO ())                    -- ^ onAccepted (heuristic exploration signal): protocol
    -> IORef Int                            -- ^ per-connection counter
    -> Word64                               -- ^ seed (per-timeline phase)
    -> MiniProtocolCb ctx LBS.ByteString IO ()
scriptedSequenceResponder model logMsg onDeparture onAccepted ctr seed =
    MiniProtocolCb $ \_ctx chan -> do
        i <- atomicModifyIORef' ctr (\n -> (n + 1, n))
        let sel   = connSelector seed i (pmProtocol model)
            gs    = generateIllegalSequence model sel
            proto = pmProtocol model
        opened <- recv chan
        logMsg
            ( "state-machine[" <> T.unpack proto <> "]: node opened ("
                <> maybe "no-msg" (const "msg") opened
                <> "); injecting departure=" <> show (gsDeparture gs)
                <> " legalPrefix=" <> show (gsLegalLen gs)
                <> " frames=" <> show (length (gsFrames gs))
            )
        onDeparture proto (gsDeparture gs)
        forM_ (gsFrames gs) (send chan)
        -- Count frames the node sends AFTER our illegal departure; if it keeps
        -- exchanging well past what the legal prefix could justify, that is a
        -- heuristic accepted-illegal exploration signal (never a fail oracle).
        postDeparture <- drain chan maxDrainFrames 0
        when (isAcceptedIllegal (gsLegalLen gs) postDeparture) (onAccepted proto)
        pure ((), Nothing)
  where
    -- Fast bounded drain: read up to maxDrainFrames replies with NO per-iteration
    -- sleep, stopping at the first Nothing (teardown). Returns the post-departure
    -- frame COUNT, preserved for the accepted-illegal heuristic (isAcceptedIllegal).
    drain _    0 acc = pure acc
    drain chan n acc = do
        m <- recv chan
        case m of
            Nothing -> pure acc
            Just _  -> drain chan (n - 1) (acc + 1)

-- | Initiator-side raw injector: the adversary DIALS the node (it is the mux
-- initiator) and drives an illegal sequence generated 'AsInitiator' (so the
-- WrongAgency class emits the node-server's frames — see 'generateIllegalSequenceRole').
--
-- Unlike 'scriptedSequenceResponder' it does NOT wait for the node to open: every
-- live N2N mini-protocol is *initiator-opens* (chainsync MsgFindIntersect/
-- RequestNext, blockfetch MsgRequestRange, txsubmission MsgInit, keepalive
-- MsgKeepAlive), so as the mux initiator we send first and never block on a
-- leading recv. (Gating a leading recv on @pmAgency (pmInitial)@ would misfire for
-- txsubmission, whose M3 @idle@ is labelled ServerAgency for the StIdle request
-- phase while its real opener is the client's MsgInit — a leading recv there would
-- wait for a node that is itself waiting for us. Hence: send first, always.)
-- Otherwise identical to the responder: emit @gsFrames@ back-to-back, fast-drain
-- replies, fire @onDeparture@/@onAccepted@.
scriptedSequenceInitiator
    :: ProtocolModel
    -> (String -> IO ())                    -- ^ logger
    -> (T.Text -> DepartureClass -> IO ())  -- ^ onDeparture (SDK hook): protocol, class
    -> (T.Text -> IO ())                    -- ^ onAccepted (heuristic exploration signal): protocol
    -> IORef Int                            -- ^ per-connection counter
    -> Word64                               -- ^ seed (per-timeline phase)
    -> MiniProtocolCb ctx LBS.ByteString IO ()
scriptedSequenceInitiator model logMsg onDeparture onAccepted ctr seed =
    MiniProtocolCb $ \_ctx chan -> do
        i <- atomicModifyIORef' ctr (\n -> (n + 1, n))
        let sel   = connSelector seed i (pmProtocol model)
            gs    = generateIllegalSequenceRole AsInitiator model sel
            proto = pmProtocol model
        logMsg
            ( "state-machine-init[" <> T.unpack proto <> "]: dialing; injecting departure="
                <> show (gsDeparture gs)
                <> " legalPrefix=" <> show (gsLegalLen gs)
                <> " frames=" <> show (length (gsFrames gs))
            )
        onDeparture proto (gsDeparture gs)
        forM_ (gsFrames gs) (send chan)
        postDeparture <- drain chan maxDrainFrames 0
        when (isAcceptedIllegal (gsLegalLen gs) postDeparture) (onAccepted proto)
        pure ((), Nothing)
  where
    -- Fast bounded drain with a per-recv timeout so it cannot block past budget on
    -- a silent node: up to maxDrainFrames frames, stopping at the first quiescent
    -- window (Nothing from timeout) or teardown (Nothing from the channel). Returns
    -- the post-departure frame count for the accepted-illegal heuristic (unchanged).
    drain _    0 acc = pure acc
    drain chan n acc = do
        m <- timeout drainRecvTimeoutMicros (recv chan)
        case m of
            Nothing       -> pure acc   -- no frame within the window: node quiescent
            Just Nothing  -> pure acc   -- channel closed: teardown
            Just (Just _) -> drain chan (n - 1) (acc + 1)
