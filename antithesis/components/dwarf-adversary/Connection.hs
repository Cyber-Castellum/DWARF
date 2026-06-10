module DwarfAdversary.ChainSync.Connection
    ( runChainSyncApplication
    , runChainSyncServer
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
import Control.Exception (SomeException)
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
import Ouroboros.Consensus.Protocol.Praos.Header ()
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
    , RunMiniProtocol (InitiatorProtocolOnly, ResponderProtocolOnly)
    , StartOnDemandOrEagerly (StartOnDemand)
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
import Ouroboros.Network.Protocol.BlockFetch.Codec (codecBlockFetch)
import Ouroboros.Network.Protocol.BlockFetch.Server
    ( BlockFetchBlockSender (SendMsgNoBlocks)
    , BlockFetchServer (BlockFetchServer)
    , blockFetchServerPeer
    )
import Ouroboros.Network.Protocol.KeepAlive.Codec (codecKeepAlive_v2)
import Ouroboros.Network.Protocol.KeepAlive.Server
    ( KeepAliveServer (KeepAliveServer, recvMsgDone, recvMsgKeepAlive)
    , keepAliveServerPeer
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
    -> IO Void
runChainSyncServer magic port onAccept codec server = withIOManager $ \iocp -> do
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
            (\_ -> SomeResponderApplication (chainSyncToResponder codec server))
        )
        nullErrorPolicies
        (\_addr serverAsync -> wait serverAsync)

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

-- | The responder application a hot N2N peer expects: chain-sync (#2,
-- fuzzed), plus the other mini-protocols the node opens on a hot
-- connection — block-fetch (#3, trivial "no blocks") and keep-alive
-- (#8) — so the node does not tear down the bearer before chain-sync
-- delivers a (mutated) header. (Peer-sharing #10 is not opened: we
-- negotiate PeerSharingDisabled. tx-submission #4 is added if needed.)
chainSyncToResponder
    :: Codec (ChainSync Header Point Tip) DeserialiseFailure IO LazyByteString
    -> ChainSyncServer Header Point Tip IO ()
    -> OuroborosApplicationWithMinimalCtx
        Mx.ResponderMode
        addr
        LazyByteString
        IO
        Void
        ()
chainSyncToResponder codec server =
    OuroborosApplication
        { getOuroborosApplication =
            [ responder 2 $
                mkMiniProtocolCbFromPeer
                    (const (nullTracer, codec, chainSyncServerPeer server))
            , responder 3 $
                mkMiniProtocolCbFromPeer
                    ( const
                        ( nullTracer
                        , codecBlockFetch encBlock decBlock encBlockPoint decBlockPoint
                        , blockFetchServerPeer blockFetchResponder
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

-- | A block-fetch responder that always reports "no blocks" — we serve
-- headers via chain-sync only; we never hand over bodies.
blockFetchResponder :: BlockFetchServer Block (Network.Point Block) IO ()
blockFetchResponder =
    BlockFetchServer
        (\_range -> pure (SendMsgNoBlocks (pure blockFetchResponder)))
        ()

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
