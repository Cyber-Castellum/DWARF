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
module DwarfAdversary.StateMachine
    ( scriptedChainSyncResponder
    , smScenarioName
    , smScenarioCount
    ) where

import Control.Concurrent (threadDelay)
import Control.Monad (forM_)
import Data.ByteString.Lazy qualified as LBS
import Data.IORef (IORef, atomicModifyIORef')
import Data.Word (Word64)
import Ouroboros.Network.Channel (Channel (..))
import Ouroboros.Network.Mux (MiniProtocolCb (..))

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
        forM_ (smFrames sel) $ \f -> do
            send chan f
            threadDelay 100000
        drain chan (20 :: Int)
        pure ((), Nothing)
  where
    drain _    0 = pure ()
    drain chan n = do
        m <- recv chan
        case m of
            Nothing -> pure ()
            Just _  -> threadDelay 100000 >> drain chan (n - 1)
