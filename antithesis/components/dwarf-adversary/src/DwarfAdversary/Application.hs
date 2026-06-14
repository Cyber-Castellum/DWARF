{-# OPTIONS_GHC -Wno-unrecognised-pragmas #-}

{-# HLINT ignore "Use const" #-}
module DwarfAdversary.Application
    ( Limit (..)
    , adversaryApplication
    , runChainProducerInto
    , syncHeaders
    , ChainSyncApplication
    , repeatedDwarfAdversaryApplication
    )
where

import DwarfAdversary (originPoint)
import DwarfAdversary.ChainSync.Codec (Header, Point, Tip)
import DwarfAdversary.ChainSync.Connection
    ( ChainSyncApplication
    , runChainSyncApplication
    )
import Control.Concurrent (threadDelay)
import Control.Concurrent.Async
    ( mapConcurrently_
    )
import Control.Concurrent.Class.MonadSTM.Strict
    ( MonadSTM (..)
    , StrictTVar
    , modifyTVar
    , newTVarIO
    , readTVar
    , writeTVar
    )
import Control.Exception (SomeException, try)
import Control.Tracer (Tracer, traceWith)
import Data.Function (fix)
import Data.List.NonEmpty (NonEmpty)
import Data.List.NonEmpty qualified as NE
import Data.Maybe (fromMaybe)
import Data.Word (Word32)
import Network.Socket (PortNumber)
import Ouroboros.Consensus.Block (headerPoint)
import Ouroboros.Consensus.Protocol.Praos.Header ()
import Ouroboros.Consensus.Shelley.Ledger.NetworkProtocolVersion ()
import Ouroboros.Consensus.Shelley.Ledger.SupportsProtocol ()
import Ouroboros.Network.Block (getTipPoint)
import Ouroboros.Network.Magic (NetworkMagic)
import Ouroboros.Network.Mock.Chain (Chain)
import Ouroboros.Network.Mock.Chain qualified as Chain
import Ouroboros.Network.NodeToNode
    ( ControlMessage (..)
    , ControlMessageSTM
    )
import Ouroboros.Network.Protocol.ChainSync.Client
    ( ChainSyncClient (..)
    , ClientStIdle (..)
    , ClientStIntersect (..)
    , ClientStNext (..)
    )

newtype State = State
    { chainVar :: StrictTVar IO (Chain Header)
    }

onChainVar :: State -> (Chain Header -> Chain Header) -> IO ()
onChainVar state f = atomically $ modifyTVar (chainVar state) f

readChainVar :: State -> STM IO (Chain Header)
readChainVar state = readTVar $ chainVar state

readChainVarIO :: State -> IO (Chain Header)
readChainVarIO = atomically . readChainVar

rollForward :: State -> Header -> IO ()
rollForward state b = onChainVar state $ \(!chain) ->
    Chain.addBlock b chain

-- We will fail to roll back iff `p` doesn't exist in `chain`
-- This will happen when we're asked to roll back to `startingPoint`,
-- which we can check for, or any point before, which we can't
-- check for. Hence we ignore all failures to rollback and replace the
-- chain with an empty one if we do.
rollBackward :: State -> Point -> IO ()
rollBackward state p = onChainVar state $ \(!chain) ->
    fromMaybe Chain.Genesis $ Chain.rollback p chain

-- | A limit on the number of blocks to sync
newtype Limit = Limit {limit :: Word32}
    deriving newtype (Show, Read, Eq, Ord)

-- the way to step the protocol
type StepProtocol = IO (Maybe Protocol)

-- Internal protocol state machine
data Protocol = Protocol
    { onRollBackward :: Point -> Tip -> StepProtocol
    , onRollForward :: Header -> StepProtocol
    , points :: [Point] -> StepProtocol
    }

-- A protocol controlled by control messages
controlledProtocol
    :: ControlMessageSTM IO -- control message source
    -> Protocol
controlledProtocol controlMessageSTM = fix $ \client ->
    let react ctrl = case ctrl of
            Continue -> Just client
            Quiesce -> error "controlledClient: unexpected Quiesce"
            Terminate -> Nothing
    in  Protocol
            { onRollBackward = \_point _tip ->
                react <$> atomically controlMessageSTM
            , onRollForward = \_header ->
                react <$> atomically controlMessageSTM
            , points = \_points -> pure $ Just client
            }

-- a control message source that terminates after syncing 'limit' blocks
terminateAfterCount
    :: State
    -> Limit
    -> ControlMessageSTM IO
terminateAfterCount stateVar limit = do
    chainLength <-
        Limit . fromIntegral . Chain.length <$> readChainVar stateVar
    pure
        $ if chainLength < limit
            then Continue
            else Terminate

-- initialize the protocol with a control message source
mkProtocol
    :: State -- the mock chain
    -> Limit -- limit of blocks to sync
    -> Protocol
mkProtocol stateVar limit =
    controlledProtocol $ terminateAfterCount stateVar limit

-- The idle state of the chain sync client
type ChainSyncIdle = ClientStIdle Header Point Tip IO ()

-- when the protocols returns Nothing, we're done as a N2N client
nothingToDone
    :: Maybe Protocol
    -> (Protocol -> ChainSyncIdle)
    -> ChainSyncIdle
nothingToDone Nothing _ = SendMsgDone ()
nothingToDone (Just next) cont = cont next

-- boots the protocol and step into initialise
mkChainSyncApplication
    :: State
    -- ^ the mock chain
    -> Point
    -- ^ starting point
    -> Limit
    -- ^ limit of blocks to sync
    -> ChainSyncApplication
    -- ^ the chain sync client application
mkChainSyncApplication stateVar startingPoint limit = ChainSyncClient $ do
    ps <- points (mkProtocol stateVar limit) [startingPoint]
    pure $ nothingToDone ps $ initialise stateVar startingPoint

-- In this consumer example, we do not care about whether the server
-- found an intersection or not. If not, we'll just sync from genesis.
--
-- Alternative policies here include:
--  iteratively finding the best intersection
--  rejecting the server if there is no intersection in the last K blocks
--
initialise
    :: State -- the mock chain
    -> Point -- starting point
    -> Protocol -- previous client state machine
    -> ChainSyncIdle
initialise stateVar startingPoint prev =
    let next =
            ChainSyncClient
                { runChainSyncClient = pure $ requestNext stateVar prev
                }
    in  SendMsgFindIntersect [startingPoint]
            $ ClientStIntersect
                { recvMsgIntersectFound = \_point _tip -> next
                , recvMsgIntersectNotFound = \_tip -> next
                }

requestNext
    :: State -- the mock chain
    -> Protocol -- this client state machine
    -> ChainSyncIdle
requestNext stateVar prev =
    SendMsgRequestNext
        (pure ())
        ClientStNext
            { recvMsgRollForward = \header tip -> ChainSyncClient $ do
                rollForward stateVar header
                choice <-
                    stoppingOnTip
                        (headerPoint header)
                        (getTipPoint tip)
                        $ onRollForward prev header
                pure $ nothingToDone choice $ requestNext stateVar
            , recvMsgRollBackward = \pIntersect tip -> ChainSyncClient $ do
                rollBackward stateVar pIntersect
                choice <- onRollBackward prev pIntersect tip
                pure $ nothingToDone choice $ requestNext stateVar
            }

stoppingOnTip
    :: Ord a
    => a
    -> a
    -> StepProtocol
    -> StepProtocol
stoppingOnTip h t stepProtocol
    | h >= t = pure Nothing
    | otherwise = stepProtocol

-- | Run an adversary application that connects to a node and syncs
-- blocks starting from the given point, up to the given limit.
adversaryApplication
    :: NetworkMagic
    -- ^ network magic
    -> String
    -- ^ peer host
    -> PortNumber
    -- ^ peer port
    -> Point
    -- ^ starting point
    -> Limit
    -- ^ limit of blocks to sync
    -> IO (Either SomeException (Chain.Point Header))
adversaryApplication magic peerName peerPort startingPoint limit = do
    chainVar <- newTVarIO (Chain.Genesis :: Chain Header)
    let stateVar = State{chainVar}
    res <-
        -- To gracefully handle the node getting killed it seems we need
        -- the outer 'try', even if connectToNode already returns 'Either
        -- SomeException'.
        try
            $ runChainSyncApplication
                magic
                peerName
                peerPort
                (const $ mkChainSyncApplication stateVar startingPoint limit)
    case res of
        Left e -> return $ Left e
        Right _ -> pure . Chain.headPoint <$> readChainVarIO stateVar

-- | Run the chain-sync CLIENT against @(host,port)@ that NEVER terminates: it
-- keeps receiving roll-forwards into the caller-supplied @chainVar@ as the
-- upstream chain grows (and applies roll-backs), so a server reading @chainVar@
-- can present a RECENT, advancing tip — the precondition for the downstream
-- node to reach + hold GSM CaughtUp. Returns only on connection error; the
-- caller restarts it. Unlike 'adversaryApplication' there is no 'Limit' and no
-- stop-on-tip: at the tip it blocks in MsgRequestNext awaiting new headers.
runChainProducerInto
    :: StrictTVar IO (Chain Header)
    -> NetworkMagic
    -> String
    -> PortNumber
    -> IO (Either SomeException ())
runChainProducerInto chainVar magic host port = do
    -- Reset to Genesis on every (re)connect. We FindIntersect from origin and
    -- rebuild via roll-forward; reusing a stale, non-empty chainVar would make
    -- the first roll-forward 'addBlock' a block whose predecessor is not the
    -- current head, which errors and pins the chain (the "stuck at 1 block"
    -- warmup stall). A fresh Genesis makes each reconnect rebuild cleanly.
    atomically $ writeTVar chainVar Chain.Genesis
    let stateVar = State{chainVar}
    res <-
        try
            $ runChainSyncApplication
                magic
                host
                port
                (const $ foreverChainSyncApplication stateVar originPoint)
    pure $ case res of
        Left e -> Left (e :: SomeException)
        Right _ -> Right ()

-- | A chain-sync client application that syncs forever (no 'Limit', no
-- stop-on-tip): fills @chainVar@ with every roll-forward and tracks roll-backs.
foreverChainSyncApplication :: State -> Point -> ChainSyncApplication
foreverChainSyncApplication stateVar startingPoint =
    ChainSyncClient
        $ pure
        $ SendMsgFindIntersect [startingPoint]
        $ ClientStIntersect
            { recvMsgIntersectFound = \_point _tip ->
                ChainSyncClient (pure (requestNextForever stateVar))
            , recvMsgIntersectNotFound = \_tip ->
                ChainSyncClient (pure (requestNextForever stateVar))
            }

-- | Request the next header forever, never sending MsgDone (so the node treats
-- us as a live, advancing upstream peer).
requestNextForever :: State -> ChainSyncIdle
requestNextForever stateVar =
    SendMsgRequestNext
        (pure ())
        ClientStNext
            { recvMsgRollForward = \header _tip -> ChainSyncClient $ do
                rollForward stateVar header
                pure (requestNextForever stateVar)
            , recvMsgRollBackward = \pIntersect _tip -> ChainSyncClient $ do
                rollBackward stateVar pIntersect
                pure (requestNextForever stateVar)
            }

-- | Like 'adversaryApplication', but return the synced headers
-- (oldest-first) instead of just the head point. Used by HeaderSource
-- to capture decodable base headers to mutate and serve back.
syncHeaders
    :: NetworkMagic
    -> String
    -> PortNumber
    -> Point
    -> Limit
    -> IO (Either SomeException [Header])
syncHeaders magic peerName peerPort startingPoint limit = do
    chainVar <- newTVarIO (Chain.Genesis :: Chain Header)
    let stateVar = State{chainVar}
    res <-
        try
            $ runChainSyncApplication
                magic
                peerName
                peerPort
                (const $ mkChainSyncApplication stateVar startingPoint limit)
    case res of
        Left e -> return $ Left e
        Right _ -> Right . Chain.toOldestFirst <$> readChainVarIO stateVar

repeatedDwarfAdversaryApplication
    :: Tracer IO String -- thread safe logger
    -> Int
    -> NetworkMagic
    -> [String]
    -> PortNumber
    -> NonEmpty Point
    -- ^ must be infinite list
    -> Limit
    -> IO ()
repeatedDwarfAdversaryApplication tracer nConns magic peerNames peerPort startingPoints limit = do
    let write = traceWith tracer
    let singleRun (_i, peerName, startingPoint) = do
            write
                $ "Starting adversary application against "
                    <> peerName
                    <> " from point "
                    <> show startingPoint
            result <-
                (startingPoint,)
                    <$> adversaryApplication magic peerName peerPort startingPoint limit
            write
                $ "Completed adversary application against "
                    <> peerName
                    <> " from point "
                    <> show startingPoint
                    <> " with result "
                    <> show result
    mapConcurrently_
        singleRun
        $ zip3 [1 .. nConns] (cycle peerNames) (NE.toList startingPoints)
    threadDelay 1000000 -- wait for logging to complete
