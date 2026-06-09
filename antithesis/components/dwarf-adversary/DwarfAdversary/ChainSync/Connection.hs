module DwarfAdversary.ChainSync.Connection
    ( runChainSyncApplication
    , runChainSyncServer
    , HeaderHash
    , ChainSyncApplication
    )
where

import DwarfAdversary.ChainSync.Codec
    ( Block
    , Header
    , Point
    , Tip
    , codecChainSync
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
    -> Codec (ChainSync Header Point Tip) DeserialiseFailure IO LazyByteString
    -> ChainSyncServer Header Point Tip IO ()
    -> IO Void
runChainSyncServer magic port codec server = withIOManager $ \iocp -> do
    AddrInfo{addrAddress} <- resolveBind port
    mutableState <- newNetworkMutableState
    withServerNode
        (socketSnocket iocp)
        makeSocketBearer
        (\_fd _addr -> pure ()) -- configure socket: no-op
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
        ResponderProtocolOnly
            $ mkMiniProtocolCbFromPeer
            $ \_ctx ->
                ( nullTracer
                , codec
                , chainSyncServerPeer server
                )
