module DwarfAdversary.ChainSync.Codec
    ( codecChainSync
    , Block
    , Header
    , Tip
    , Point
    ) where

import Cardano.Chain.Slotting (EpochSlots (EpochSlots))
import Codec.Serialise (DeserialiseFailure, Serialise (..))
import Codec.Serialise.Decoding (Decoder)
import Codec.Serialise.Encoding (Encoding)
import Data.ByteString.Lazy qualified as LBS
import Data.Data (Proxy (Proxy))
import Network.TypedProtocol.Codec (Codec)
import Ouroboros.Consensus.Block.Abstract
    ( decodeRawHash
    , encodeRawHash
    )
import Ouroboros.Consensus.Byron.Ledger (ByronBlock, CodecConfig (..))
import Ouroboros.Consensus.Cardano.Block
    ( CodecConfig (CardanoCodecConfig)
    )
import Ouroboros.Consensus.Cardano.Block qualified as Consensus
import Ouroboros.Consensus.Cardano.Node
    ( pattern CardanoNodeToNodeVersion2
    )
import Ouroboros.Consensus.HardFork.Combinator.NetworkVersion
    ( HardForkNodeToNodeVersion
    )
import Ouroboros.Consensus.Node.Serialisation
    ( decodeNodeToNode
    , encodeNodeToNode
    )
import Ouroboros.Consensus.Protocol.Praos.Header ()
import Ouroboros.Consensus.Shelley.Ledger
    ( CodecConfig (ShelleyCodecConfig)
    )
import Ouroboros.Consensus.Shelley.Ledger.NetworkProtocolVersion ()
import Ouroboros.Consensus.Shelley.Ledger.SupportsProtocol ()
import Ouroboros.Network.Block
    ( decodeTip
    , encodeTip
    )
import Ouroboros.Network.Block qualified as Network
import Ouroboros.Network.Protocol.ChainSync.Codec qualified as ChainSync
import Ouroboros.Network.Protocol.ChainSync.Type (ChainSync)

-- | Real Cardano Block type
type Block = Consensus.CardanoBlock Consensus.StandardCrypto

-- | Real Cardano Header type
type Header = Consensus.Header Block

-- | Real Cardano Tip type
type Tip = Network.Tip Block

-- | Real Cardano Point type
type Point = Network.Point Header

-- The ChainSync codec for our Block, Point, and Tip types
codecChainSync
    :: Codec
        (ChainSync Header Point Tip)
        DeserialiseFailure
        IO
        LBS.ByteString
codecChainSync =
    ChainSync.codecChainSync
        encHeader
        decHeader
        encPoint
        decPoint
        encTip
        decTip

----- Encoding and Decoding Headers -----
encHeader :: Header -> Encoding
encHeader = encodeNodeToNode @Block ccfg version

decHeader :: Decoder s Header
decHeader = decodeNodeToNode @Block ccfg version

version
    :: HardForkNodeToNodeVersion
        (ByronBlock : Consensus.CardanoShelleyEras c)
version = CardanoNodeToNodeVersion2

ccfg :: Consensus.CardanoCodecConfig c
ccfg =
    CardanoCodecConfig
        (ByronCodecConfig $ EpochSlots 42)
        ShelleyCodecConfig
        ShelleyCodecConfig
        ShelleyCodecConfig
        ShelleyCodecConfig
        ShelleyCodecConfig
        ShelleyCodecConfig

--- Encoding and Decoding Points -----
encPoint :: Point -> Encoding
encPoint = encode
decPoint :: Decoder s Point
decPoint = decode

--- Encoding and Decoding Tips -----
encTip :: Network.Tip Block -> Encoding
encTip = encodeTip (encodeRawHash (Proxy @Block))
decTip :: Decoder s (Network.Tip Block)
decTip = decodeTip (decodeRawHash (Proxy @Block))
