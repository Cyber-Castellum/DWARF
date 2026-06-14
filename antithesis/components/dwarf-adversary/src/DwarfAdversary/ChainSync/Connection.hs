module DwarfAdversary.ChainSync.Connection
    ( runChainSyncApplication
    , runChainSyncServer
    , runAdversaryServerIR
    , runBlockFetchApplication
    , fetchBlock
    , servingBlockFetchResponder
    , servingBlockFetchResponderMap
    , onDemandBlockFetchResponder
    , blockFetchResponder
    , plainBlockFetchCodec
    , plainTxSubmissionCodec
    , HeaderHash
    , ChainSyncApplication
    )
where

import DwarfAdversary.ChainSync.Codec
    ( Block
    , GenTx
    , GenTxId
    , Header
    , Point
    , Tip
    , codecChainSync
    , decBlock
    , decBlockPoint
    , decTx
    , decTxId
    , encBlock
    , encBlockPoint
    , encTx
    , encTxId
    )
import Codec.Serialise (DeserialiseFailure)
import Control.Concurrent.Class.MonadSTM.Strict
    ( MonadSTM (..)
    , StrictTVar
    , newTVarIO
    , readTVar
    , writeTVar
    )
import Control.Concurrent (threadDelay)
import Control.Exception (SomeException, try)
import Control.Monad (forever)
import Data.Map.Strict (Map)
import Data.Map.Strict qualified as Map
import Control.Monad.Class.MonadAsync (wait)
import Control.Tracer (nullTracer)
import Data.ByteString.Lazy (LazyByteString)
import Data.List.NonEmpty qualified as NE
import Data.Void (Void)
import Network.Mux qualified as Mx
import Network.Socket
    ( AddrInfo (..)
    , AddrInfoFlag (AI_PASSIVE)
    , PortNumber
    , SocketType (Stream)
    , defaultHints
    , getAddrInfo
    )
import Network.TypedProtocol.Codec (Codec)
import Ouroboros.Consensus.Block (headerPoint)
import Ouroboros.Consensus.Protocol.Praos.Header ()
import Ouroboros.Network.Mock.Chain (Chain)
import Ouroboros.Network.Mock.Chain qualified as Chain
import Ouroboros.Consensus.Shelley.Ledger.NetworkProtocolVersion ()
import Ouroboros.Consensus.Shelley.Ledger.SupportsProtocol ()
import Ouroboros.Network.Block qualified as Network
import Ouroboros.Network.Diffusion.Configuration
    ( PeerSharing (PeerSharingDisabled)
    )
import Ouroboros.Network.ErrorPolicy (nullErrorPolicies)
import Ouroboros.Network.IOManager (withIOManager)
import Ouroboros.Network.Magic (NetworkMagic (..))
import Ouroboros.Network.Mux
    ( MiniProtocol (..)
    , MiniProtocolLimits (..)
    , MiniProtocolNum (MiniProtocolNum)
    , OuroborosApplication (..)
    , OuroborosApplicationWithMinimalCtx
    , MiniProtocolCb (MiniProtocolCb)
    , RunMiniProtocol (InitiatorAndResponderProtocol, InitiatorProtocolOnly, ResponderProtocolOnly)
    , StartOnDemandOrEagerly (StartEagerly, StartOnDemand)
    , mkMiniProtocolCbFromPeer
    , mkMiniProtocolCbFromPeerPipelined
    )
import Ouroboros.Network.NodeToNode
    ( DiffusionMode (InitiatorAndResponderDiffusionMode, InitiatorOnlyDiffusionMode)
    , NodeToNodeVersion (NodeToNodeV_14)
    , NodeToNodeVersionData (..)
    , nodeToNodeCodecCBORTerm
    )
import Ouroboros.Network.Protocol.ChainSync.Client
    ( ChainSyncClient (..)
    )
import Ouroboros.Network.Protocol.ChainSync.Client qualified as ChainSync
import Ouroboros.Network.Protocol.ChainSync.Server
    ( ChainSyncServer
    , chainSyncServerPeer
    )
import Ouroboros.Network.Protocol.ChainSync.Type (ChainSync)
import Ouroboros.Network.Protocol.BlockFetch.Client
    ( BlockFetchClient (BlockFetchClient)
    , BlockFetchReceiver (BlockFetchReceiver, handleBatchDone, handleBlock)
    , BlockFetchRequest (SendMsgClientDone, SendMsgRequestRange)
    , BlockFetchResponse (BlockFetchResponse, handleNoBlocks, handleStartBatch)
    , blockFetchClientPeer
    )
import Ouroboros.Network.Protocol.BlockFetch.Codec (codecBlockFetch)
import Ouroboros.Network.Protocol.BlockFetch.Server
    ( BlockFetchBlockSender (SendMsgNoBlocks, SendMsgStartBatch)
    , BlockFetchSendBlocks (SendMsgBatchDone, SendMsgBlock)
    , BlockFetchServer (BlockFetchServer)
    , blockFetchServerPeer
    )
import Ouroboros.Network.Protocol.BlockFetch.Type (BlockFetch, ChainRange (ChainRange))
import Ouroboros.Network.Protocol.KeepAlive.Client
    ( KeepAliveClient (KeepAliveClient)
    , KeepAliveClientSt (SendMsgKeepAlive)
    , keepAliveClientPeer
    )
import Ouroboros.Network.Protocol.KeepAlive.Codec (codecKeepAlive_v2)
import Ouroboros.Network.Protocol.KeepAlive.Server
    ( KeepAliveServer (KeepAliveServer, recvMsgDone, recvMsgKeepAlive)
    , keepAliveServerPeer
    )
import Ouroboros.Network.Protocol.KeepAlive.Type (Cookie (Cookie))
import Ouroboros.Network.Protocol.TxSubmission2.Client
    ( TxSubmissionClient
    , txSubmissionClientPeer
    )
import Ouroboros.Network.Protocol.TxSubmission2.Codec (codecTxSubmission2)
import Ouroboros.Network.Protocol.TxSubmission2.Server
    ( ServerStIdle (SendMsgRequestTxIdsBlocking)
    , TxSubmissionServerPipelined (TxSubmissionServerPipelined)
    , txSubmissionServerPeerPipelined
    )
import Ouroboros.Network.Protocol.TxSubmission2.Type
    ( NumTxIdsToAck (NumTxIdsToAck)
    , NumTxIdsToReq (NumTxIdsToReq)
    , TxSubmission2
    )
import Network.TypedProtocol.Core (N (Z))
import Data.Word (Word16)
import Ouroboros.Network.Protocol.Handshake.Codec
    ( cborTermVersionDataCodec
    , noTimeLimitsHandshake
    , nodeToNodeHandshakeCodec
    )
import Ouroboros.Network.Protocol.Handshake.Version
    ( Acceptable (acceptableVersion)
    , Queryable (queryVersion)
    , simpleSingletonVersions
    )
import Ouroboros.Network.Server.RateLimiting
    ( AcceptedConnectionsLimit (..)
    )
import Ouroboros.Network.Snocket
    ( makeSocketBearer
    , socketSnocket
    )
import Ouroboros.Network.Socket
    ( ConnectToArgs (..)
    , HandshakeCallbacks (..)
    , SomeResponderApplication (..)
    , connectToNode
    , newNetworkMutableState
    , nullNetworkConnectTracers
    , nullNetworkServerTracers
    , withServerNode
    )

-- | The application type for a chain sync client
type ChainSyncApplication = ChainSyncClient Header Point Tip IO ()

-- | The header hash type used in the chain sync connection
type HeaderHash = Network.HeaderHash Block

-- | Connect to a node-to-node chain sync server and run the given application
-- (initiator role — retained for header capture).
runChainSyncApplication
    :: NetworkMagic
    -- ^ The network magic
    -> String
    -- ^ host
    -> PortNumber
    -- ^ port
    -> (NodeToNodeVersionData -> ChainSyncApplication)
    -- ^ application
    -> IO (Either SomeException (Either () Void))
runChainSyncApplication magic peerName peerPort application = withIOManager $ \iocp -> do
    AddrInfo{addrAddress} <- resolve peerName peerPort
    connectToNode -- withNode
        (socketSnocket iocp) -- TCP
        makeSocketBearer
        ConnectToArgs
            { ctaHandshakeCodec = nodeToNodeHandshakeCodec
            , ctaHandshakeTimeLimits = noTimeLimitsHandshake
            , ctaVersionDataCodec =
                cborTermVersionDataCodec
                    nodeToNodeCodecCBORTerm
            , ctaConnectTracers = nullNetworkConnectTracers
            , ctaHandshakeCallbacks =
                HandshakeCallbacks
                    { acceptCb = acceptableVersion
                    , queryCb = queryVersion
                    }
            }
        mempty -- socket options
        ( simpleSingletonVersions
            NodeToNodeV_14
            ( NodeToNodeVersionData
                { networkMagic = magic
                , diffusionMode = InitiatorOnlyDiffusionMode
                , peerSharing = PeerSharingDisabled
                , query = False
                }
            )
            (chainSyncToOuroboros . application) -- application
        )
        Nothing
        addrAddress

-- | Connect to an in-bundle node as a block-fetch /client/ (initiator on
-- mini-protocol #3) and run the given block-fetch application. Used only
-- to capture one real block to later serve mutated. Mirrors
-- 'runChainSyncApplication'.
runBlockFetchApplication
    :: NetworkMagic
    -> String
    -- ^ host
    -> PortNumber
    -- ^ port
    -> (NodeToNodeVersionData -> BlockFetchClient Block (Network.Point Block) IO a)
    -> IO (Either SomeException (Either a Void))
runBlockFetchApplication magic peerName peerPort application = withIOManager $ \iocp -> do
    AddrInfo{addrAddress} <- resolve peerName peerPort
    connectToNode
        (socketSnocket iocp)
        makeSocketBearer
        ConnectToArgs
            { ctaHandshakeCodec = nodeToNodeHandshakeCodec
            , ctaHandshakeTimeLimits = noTimeLimitsHandshake
            , ctaVersionDataCodec = cborTermVersionDataCodec nodeToNodeCodecCBORTerm
            , ctaConnectTracers = nullNetworkConnectTracers
            , ctaHandshakeCallbacks =
                HandshakeCallbacks
                    { acceptCb = acceptableVersion
                    , queryCb = queryVersion
                    }
            }
        mempty
        ( simpleSingletonVersions
            NodeToNodeV_14
            ( NodeToNodeVersionData
                { networkMagic = magic
                , diffusionMode = InitiatorOnlyDiffusionMode
                , peerSharing = PeerSharingDisabled
                , query = False
                }
            )
            (blockFetchToOuroboros . application)
        )
        Nothing
        addrAddress

-- | Initiator-only Ouroboros application running our block-fetch client on
-- mini-protocol #3. Mirrors 'chainSyncToOuroboros'.
blockFetchToOuroboros
    :: BlockFetchClient Block (Network.Point Block) IO a
    -> OuroborosApplicationWithMinimalCtx
        Mx.InitiatorMode
        addr
        LazyByteString
        IO
        a
        Void
blockFetchToOuroboros app =
    OuroborosApplication
        { getOuroborosApplication =
            [ MiniProtocol
                { miniProtocolNum = MiniProtocolNum 3
                , miniProtocolStart = StartOnDemand
                , miniProtocolLimits = maximumMiniProtocolLimits
                , miniProtocolRun =
                    InitiatorProtocolOnly
                        $ mkMiniProtocolCbFromPeer
                        $ \_ctx ->
                            ( nullTracer
                            , codecBlockFetch encBlock decBlock encBlockPoint decBlockPoint
                            , blockFetchClientPeer app
                            )
                }
            ]
        }

-- | Fetch the block at @point@ from an in-bundle node via block-fetch.
-- Returns the real, unmutated block (or Nothing if the node served none).
-- Hermetic: the host is an in-bundle node, never external.
fetchBlock
    :: NetworkMagic
    -> String
    -> PortNumber
    -> Network.Point Block
    -> IO (Either SomeException (Maybe Block))
fetchBlock magic host port point = do
    resultVar <- newTVarIO Nothing
    -- After the response completes, the continuation (3rd arg of
    -- SendMsgRequestRange) sends ClientDone. handleBatchDone/handleNoBlocks
    -- terminate the response with m ().
    let done = BlockFetchClient (pure (SendMsgClientDone ()))
        receiver =
            BlockFetchReceiver
                { handleBlock = \blk -> do
                    atomically (writeTVar resultVar (Just blk))
                    pure receiver
                , handleBatchDone = pure ()
                }
        response =
            BlockFetchResponse
                { handleStartBatch = pure receiver
                , handleNoBlocks = pure ()
                }
        client =
            BlockFetchClient
                (pure (SendMsgRequestRange (ChainRange point point) response done))
    res <- try $ runBlockFetchApplication magic host port (const client)
    case res of
        Left e -> pure (Left e)
        Right _ -> Right <$> atomically (readTVar resultVar)

-- | Bind and listen as a chain-sync /server/ (responder role): accept
-- inbound N2N connections, negotiate the handshake, and run the given
-- chain-sync server peer with @codec@ on the chain-sync mini-protocol
-- (num 2). Blocks forever serving connections.
--
-- @codec@ is either the plain 'codecChainSync' (spike) or the mutating
-- codec (fuzzing). @server@ is built from the captured base headers.
runChainSyncServer
    :: NetworkMagic
    -> PortNumber
    -> (String -> IO ())
    -- ^ onAccept: called per inbound connection (peer addr) — observability
    -> Codec (ChainSync Header Point Tip) DeserialiseFailure IO LazyByteString
    -> ChainSyncServer Header Point Tip IO ()
    -> Codec (BlockFetch Block (Network.Point Block)) DeserialiseFailure IO LazyByteString
    -- ^ block-fetch (#3) codec: plain for chain-sync mode, mutating for block-fetch mode
    -> BlockFetchServer Block (Network.Point Block) IO ()
    -- ^ block-fetch (#3) responder: no-blocks for chain-sync mode, serving for block-fetch mode
    -> IO Void
runChainSyncServer magic port onAccept codec server bfCodec bfServer = withIOManager $ \iocp -> do
    AddrInfo{addrAddress} <- resolveBind port
    mutableState <- newNetworkMutableState
    withServerNode
        (socketSnocket iocp)
        makeSocketBearer
        (\_fd peerAddr -> onAccept (show peerAddr)) -- per inbound connection
        nullNetworkServerTracers
        mutableState
        acceptedConnectionsLimit
        addrAddress
        nodeToNodeHandshakeCodec
        noTimeLimitsHandshake
        (cborTermVersionDataCodec nodeToNodeCodecCBORTerm)
        ( HandshakeCallbacks
            { acceptCb = acceptableVersion
            , queryCb = queryVersion
            }
        )
        ( simpleSingletonVersions
            NodeToNodeV_14
            ( NodeToNodeVersionData
                { networkMagic = magic
                , diffusionMode = InitiatorAndResponderDiffusionMode
                , peerSharing = PeerSharingDisabled
                , query = False
                }
            )
            (\_ -> SomeResponderApplication (chainSyncToResponder codec server bfCodec bfServer))
        )
        nullErrorPolicies
        (\_addr serverAsync -> wait serverAsync)

-- | Idle initiator: occupies the initiator slot of a mini-protocol we only
-- serve as a responder (chain-sync, block-fetch, keep-alive) so the app can be
-- InitiatorResponderMode. It never sends, so the node drives those protocols
-- toward our responder.
idleInitiator :: MiniProtocolCb ctx LazyByteString IO a
idleInitiator = MiniProtocolCb (\_ctx _channel -> forever (threadDelay 3600000000))

-- | Bind and listen as an Initiator+Responder server (tx-submission mode):
-- responders for #2/#3/#8 (+ #4 consumer) keep the bearer alive, and an
-- INITIATOR #4 runs our tx provider so the node consumes + decodes the
-- (mutated) tx. 'withServerNode' accepts this IR app because
-- 'SomeResponderApplication' only requires @HasResponder mode@, which
-- @InitiatorResponderMode@ satisfies.
runAdversaryServerIR
    :: NetworkMagic
    -> PortNumber
    -> (String -> IO ())
    -> Codec (ChainSync Header Point Tip) DeserialiseFailure IO LazyByteString
    -> ChainSyncServer Header Point Tip IO ()
    -> Codec (BlockFetch Block (Network.Point Block)) DeserialiseFailure IO LazyByteString
    -> BlockFetchServer Block (Network.Point Block) IO ()
    -> Codec (TxSubmission2 (GenTxId Block) (GenTx Block)) DeserialiseFailure IO LazyByteString
    -> TxSubmissionClient (GenTxId Block) (GenTx Block) IO ()
    -> IO Void
runAdversaryServerIR magic port onAccept csCodec csServer bfCodec bfServer txCodec txProvider =
    withIOManager $ \iocp -> do
        AddrInfo{addrAddress} <- resolveBind port
        mutableState <- newNetworkMutableState
        withServerNode
            (socketSnocket iocp)
            makeSocketBearer
            (\_fd peerAddr -> onAccept (show peerAddr))
            nullNetworkServerTracers
            mutableState
            acceptedConnectionsLimit
            addrAddress
            nodeToNodeHandshakeCodec
            noTimeLimitsHandshake
            (cborTermVersionDataCodec nodeToNodeCodecCBORTerm)
            (HandshakeCallbacks {acceptCb = acceptableVersion, queryCb = queryVersion})
            ( simpleSingletonVersions
                NodeToNodeV_14
                ( NodeToNodeVersionData
                    { networkMagic = magic
                    , diffusionMode = InitiatorAndResponderDiffusionMode
                    , peerSharing = PeerSharingDisabled
                    , query = False
                    }
                )
                ( \_ ->
                    SomeResponderApplication
                        (adversaryIRApp csCodec csServer bfCodec bfServer txCodec txProvider)
                )
            )
            nullErrorPolicies
            (\_addr serverAsync -> wait serverAsync)

-- | The Initiator+Responder application: #2 chain-sync (responder), #3
-- block-fetch (responder), #4 tx-submission (INITIATOR provider + responder
-- consumer), #8 keep-alive (responder). Unused initiator slots idle.
adversaryIRApp
    :: Codec (ChainSync Header Point Tip) DeserialiseFailure IO LazyByteString
    -> ChainSyncServer Header Point Tip IO ()
    -> Codec (BlockFetch Block (Network.Point Block)) DeserialiseFailure IO LazyByteString
    -> BlockFetchServer Block (Network.Point Block) IO ()
    -> Codec (TxSubmission2 (GenTxId Block) (GenTx Block)) DeserialiseFailure IO LazyByteString
    -> TxSubmissionClient (GenTxId Block) (GenTx Block) IO ()
    -> OuroborosApplicationWithMinimalCtx Mx.InitiatorResponderMode addr LazyByteString IO () ()
adversaryIRApp csCodec csServer bfCodec bfServer txCodec txProvider =
    OuroborosApplication
        { getOuroborosApplication =
            [ both 2 idleInitiator $
                mkMiniProtocolCbFromPeer (const (nullTracer, csCodec, chainSyncServerPeer csServer))
            , both 3 idleInitiator $
                mkMiniProtocolCbFromPeer (const (nullTracer, bfCodec, blockFetchServerPeer bfServer))
            , both 4
                (mkMiniProtocolCbFromPeer (const (nullTracer, txCodec, txSubmissionClientPeer txProvider)))
                (mkMiniProtocolCbFromPeerPipelined (const (nullTracer, txCodec, txSubmissionServerPeerPipelined txSubmissionResponder)))
            , both 8 idleInitiator $
                mkMiniProtocolCbFromPeer (const (nullTracer, codecKeepAlive_v2, keepAliveServerPeer keepAliveResponder))
            ]
        }
  where
    both num ini res =
        MiniProtocol
            { miniProtocolNum = MiniProtocolNum num
            , miniProtocolStart = StartOnDemand
            , miniProtocolLimits = maximumMiniProtocolLimits
            , miniProtocolRun = InitiatorAndResponderProtocol ini res
            }

acceptedConnectionsLimit :: AcceptedConnectionsLimit
acceptedConnectionsLimit =
    AcceptedConnectionsLimit
        { acceptedConnectionsHardLimit = 512
        , acceptedConnectionsSoftLimit = 384
        , acceptedConnectionsDelay = 0
        }

resolve :: String -> PortNumber -> IO AddrInfo
resolve peerName peerPort = do
    let hints =
            defaultHints
                { addrFlags = [AI_PASSIVE]
                , addrSocketType = Stream
                }
    NE.head
        <$> getAddrInfo (Just hints) (Just peerName) (Just $ show peerPort)

resolveBind :: PortNumber -> IO AddrInfo
resolveBind port = do
    let hints =
            defaultHints
                { addrFlags = [AI_PASSIVE]
                , addrSocketType = Stream
                }
    NE.head
        <$> getAddrInfo (Just hints) (Just "0.0.0.0") (Just $ show port)

-- TODO: provide sensible limits
-- https://github.com/intersectmbo/ouroboros-network/issues/575
maximumMiniProtocolLimits :: MiniProtocolLimits
maximumMiniProtocolLimits =
    MiniProtocolLimits
        { maximumIngressQueue = maxBound
        }

chainSyncToOuroboros
    :: ChainSyncApplication
    -- ^ chainSync
    -> OuroborosApplicationWithMinimalCtx
        Mx.InitiatorMode
        addr
        LazyByteString
        IO
        ()
        Void
chainSyncToOuroboros chainSyncApp =
    OuroborosApplication
        { getOuroborosApplication =
            [ MiniProtocol
                { miniProtocolNum = MiniProtocolNum 2
                , miniProtocolStart = StartOnDemand
                , miniProtocolLimits = maximumMiniProtocolLimits
                , miniProtocolRun = run
                }
            , -- Keep-alive INITIATOR (#8). A long-lived chain-sync client that
              -- parks at the upstream's tip must still ping keep-alive, or the
              -- upstream node's keep-alive responder times out (~97s) and
              -- tears the bearer down (ExceededTimeLimit (KeepAlive)) — which
              -- on a slow-forging chain reaps us before we accumulate a chain
              -- to serve. Start EAGERLY (nothing else demands it) and ping
              -- forever.
              MiniProtocol
                { miniProtocolNum = MiniProtocolNum 8
                , miniProtocolStart = StartEagerly
                , miniProtocolLimits = maximumMiniProtocolLimits
                , miniProtocolRun =
                    InitiatorProtocolOnly
                        $ mkMiniProtocolCbFromPeer
                        $ \_ctx ->
                            ( nullTracer
                            , codecKeepAlive_v2
                            , keepAliveClientPeer keepAliveInitiator
                            )
                }
            ]
        }
  where
    run =
        InitiatorProtocolOnly
            $ mkMiniProtocolCbFromPeer
            $ \_ctx ->
                ( nullTracer
                , codecChainSync
                , ChainSync.chainSyncClientPeer chainSyncApp
                )

-- | A keep-alive client that pings forever (~20s cadence, well under the
-- upstream's keep-alive timeout) so a parked chain-sync initiator connection
-- stays alive instead of being reaped.
keepAliveInitiator :: KeepAliveClient IO ()
keepAliveInitiator = KeepAliveClient (go 0)
  where
    go :: Word16 -> IO (KeepAliveClientSt IO ())
    go c = pure (SendMsgKeepAlive (Cookie c) (threadDelay 20000000 >> go (c + 1)))

-- | The responder application a hot N2N peer expects: chain-sync (#2,
-- fuzzed), plus the other mini-protocols the node opens on a hot
-- connection — block-fetch (#3, trivial "no blocks") and keep-alive
-- (#8) — so the node does not tear down the bearer before chain-sync
-- delivers a (mutated) header. (Peer-sharing #10 is not opened: we
-- negotiate PeerSharingDisabled. tx-submission #4 is added if needed.)
chainSyncToResponder
    :: Codec (ChainSync Header Point Tip) DeserialiseFailure IO LazyByteString
    -> ChainSyncServer Header Point Tip IO ()
    -> Codec (BlockFetch Block (Network.Point Block)) DeserialiseFailure IO LazyByteString
    -> BlockFetchServer Block (Network.Point Block) IO ()
    -> OuroborosApplicationWithMinimalCtx
        Mx.ResponderMode
        addr
        LazyByteString
        IO
        Void
        ()
chainSyncToResponder codec server bfCodec bfServer =
    OuroborosApplication
        { getOuroborosApplication =
            [ responder 2 $
                mkMiniProtocolCbFromPeer
                    (const (nullTracer, codec, chainSyncServerPeer server))
            , responder 3 $
                mkMiniProtocolCbFromPeer
                    ( const
                        ( nullTracer
                        , bfCodec
                        , blockFetchServerPeer bfServer
                        )
                    )
            , responder 8 $
                mkMiniProtocolCbFromPeer
                    ( const
                        ( nullTracer
                        , codecKeepAlive_v2
                        , keepAliveServerPeer keepAliveResponder
                        )
                    )
            , responder 4 $
                mkMiniProtocolCbFromPeerPipelined
                    ( const
                        ( nullTracer
                        , codecTxSubmission2 encTxId decTxId encTx decTx
                        , txSubmissionServerPeerPipelined txSubmissionResponder
                        )
                    )
            ]
        }
  where
    responder num cb =
        MiniProtocol
            { miniProtocolNum = MiniProtocolNum num
            , miniProtocolStart = StartOnDemand
            , miniProtocolLimits = maximumMiniProtocolLimits
            , miniProtocolRun = ResponderProtocolOnly cb
            }

-- | A keep-alive responder: answer every keep-alive forever.
keepAliveResponder :: KeepAliveServer IO ()
keepAliveResponder =
    KeepAliveServer
        { recvMsgKeepAlive = pure keepAliveResponder
        , recvMsgDone = pure ()
        }

-- | A block-fetch responder that always reports "no blocks" — used in
-- chain-sync mode, where we serve headers via chain-sync only and never
-- hand over bodies.
blockFetchResponder :: BlockFetchServer Block (Network.Point Block) IO ()
blockFetchResponder =
    BlockFetchServer
        (\_range -> pure (SendMsgNoBlocks (pure blockFetchResponder)))
        ()

-- | The plain (non-mutating) block-fetch codec, paired with
-- 'blockFetchResponder' for chain-sync mode.
plainBlockFetchCodec
    :: Codec (BlockFetch Block (Network.Point Block)) DeserialiseFailure IO LazyByteString
plainBlockFetchCodec = codecBlockFetch encBlock decBlock encBlockPoint decBlockPoint

-- | The plain tx-submission2 codec for our GenTx/GenTxId types, exposed so the
-- executable can build the tx-submission server mode without depending on
-- ouroboros-network-protocols directly.
plainTxSubmissionCodec
    :: Codec (TxSubmission2 (GenTxId Block) (GenTx Block)) DeserialiseFailure IO LazyByteString
plainTxSubmissionCodec = codecTxSubmission2 encTxId decTxId encTx decTx

-- | A block-fetch responder that serves the captured base block once for
-- the first requested range (the mutating codec fuzzes its bytes on the
-- wire), then reports no-blocks for any further ranges. Used in
-- block-fetch mode so the node runs its real block-body decoder on the
-- structurally-mutated block.
servingBlockFetchResponder
    :: (Block -> IO ())
    -- ^ onServe: observability hook, called with the (unmutated) block
    -> Block
    -> BlockFetchServer Block (Network.Point Block) IO ()
servingBlockFetchResponder onServe blk = serveOnce
  where
    serveOnce =
        BlockFetchServer
            ( \_range -> do
                onServe blk
                pure
                    ( SendMsgStartBatch
                        (pure (SendMsgBlock blk (pure (SendMsgBatchDone (pure noBlocks)))))
                    )
            )
            ()
    noBlocks =
        BlockFetchServer (\_ -> pure (SendMsgNoBlocks (pure noBlocks))) ()

-- | A block-fetch responder that serves the CORRECT body for each requested
-- point, looked up in a captured point->Block map (the mutating block-fetch
-- codec perturbs the bodies on the wire). For a requested @ChainRange lo hi@ it
-- serves every captured block whose point falls in [lo, hi], in order; no-blocks
-- if none match. This replaces the fixed-block responder for block-fetch mode so
-- the node receives the body it actually asked for and can advance its chain.
servingBlockFetchResponderMap
    :: (Block -> IO ())
    -- ^ onServe: observability hook per served (unmutated) block
    -> Map (Network.Point Block) Block
    -> [Network.Point Block]
    -- ^ ordered chain points (oldest-first) for range expansion
    -> BlockFetchServer Block (Network.Point Block) IO ()
servingBlockFetchResponderMap onServe blocks orderedPts = server
  where
    server = BlockFetchServer handleRange ()
    handleRange (ChainRange lo hi) =
        let inRange = takeWhile (<= hi) (dropWhile (< lo) orderedPts)
            blks = [b | p <- inRange, Just b <- [Map.lookup p blocks]]
        in  case blks of
                [] -> pure (SendMsgNoBlocks (pure server))
                _ -> pure (SendMsgStartBatch (sendBlocks blks))
    sendBlocks [] = pure (SendMsgBatchDone (pure server))
    sendBlocks (b : bs) = do
        onServe b
        pure (SendMsgBlock b (sendBlocks bs))

-- | A block-fetch responder for the ADVANCING chain: for a requested
-- @ChainRange lo hi@ it reads the shared growing chain, finds the points in
-- [lo, hi] (oldest-first), and fetches each body from upstream ON DEMAND via
-- 'fetchBlock', serving them in order. Unlike 'servingBlockFetchResponderMap'
-- it needs no precaptured body map, so it works with the unbounded advancing
-- chain. The node block-fetches these bodies, validates them, and ADOPTS the
-- chain — the precondition for reaching GSM CaughtUp (and thus running
-- tx-submission). Bodies are real; mutation (block mode) is on the codec.
onDemandBlockFetchResponder
    :: (String -> IO ())
    -- ^ logger (diagnostic: did the node block-fetch?)
    -> (Block -> IO ())
    -- ^ onServe: observability hook per served block
    -> NetworkMagic
    -> (String, Int)
    -- ^ upstream (host, port) to fetch bodies from
    -> StrictTVar IO (Chain Header)
    -> BlockFetchServer Block (Network.Point Block) IO ()
onDemandBlockFetchResponder log_ onServe magic (host, port) chainVar = server
  where
    server = BlockFetchServer handleRange ()
    handleRange (ChainRange lo hi) = do
        hdrs <- Chain.toOldestFirst <$> atomically (readTVar chainVar)
        let pts = map (Network.castPoint . headerPoint) hdrs
            inRange = takeWhile (<= hi) (dropWhile (< lo) pts)
        log_ ("block-fetch(on-demand): MsgRequestRange -> " <> show (length inRange) <> " points in range")
        case inRange of
            [] -> pure (SendMsgNoBlocks (pure server))
            _ -> pure (SendMsgStartBatch (sendBlocks inRange))
    sendBlocks [] = pure (SendMsgBatchDone (pure server))
    sendBlocks (p : ps) = do
        r <- fetchBlock magic host (fromIntegral port) p
        case r of
            Right (Just b) -> do
                onServe b
                log_ "block-fetch(on-demand): served a body"
                pure (SendMsgBlock b (sendBlocks ps))
            _ -> do
                log_ "block-fetch(on-demand): body fetch miss; skipping"
                sendBlocks ps

-- | A minimal tx-submission2 responder: blockingly request txids and
-- loop (acking what we receive, never fetching tx bodies). Its only
-- job is to keep mini-protocol #4 alive so the node does not tear down
-- the hot connection — without it the mux raises UnknownMiniProtocol 4
-- and kills chain-sync before a header is served.
txSubmissionResponder :: TxSubmissionServerPipelined (GenTxId Block) (GenTx Block) IO ()
txSubmissionResponder = TxSubmissionServerPipelined (pure (idle 0))
  where
    idle :: Word16 -> ServerStIdle Z (GenTxId Block) (GenTx Block) IO ()
    idle ack =
        SendMsgRequestTxIdsBlocking
            (NumTxIdsToAck ack)
            (NumTxIdsToReq 1)
            (pure ())
            (\txids -> pure (idle (fromIntegral (NE.length txids))))
