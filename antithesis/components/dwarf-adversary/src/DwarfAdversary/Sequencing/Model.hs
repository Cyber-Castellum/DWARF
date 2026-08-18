{-# LANGUAGE OverloadedStrings #-}

-- | M3 state-machine models embedded into the binary: the LEGAL transition
-- graph + real N2N wire-frame alphabet per mini-protocol. The models carry only
-- happy-paths (every transition expect:ok); the Generate engine synthesizes the
-- illegal departures from this scaffold. Agency is protocol law (not in the
-- JSON) and is supplied here as a small static table per protocol.
module DwarfAdversary.Sequencing.Model
  ( State
  , Agency (..)
  , Frame (..)
  , ProtocolModel (..)
  , allModels
  ) where

import Data.Aeson (FromJSON (..), eitherDecodeStrict', withObject, (.:))
import Data.ByteString (ByteString)
import qualified Data.ByteString.Base16 as B16
import qualified Data.ByteString.Lazy as LBS
import Data.Map.Strict (Map)
import qualified Data.Map.Strict as Map
import Data.Set (Set)
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as T
import qualified Data.Text.Encoding as TE

import DwarfAdversary.Sequencing.Embedded
  (rawBlockFetch, rawChainSync, rawKeepAlive, rawTxSubmission)

type State = Text

data Agency = ClientAgency | ServerAgency | TerminalAgency
  deriving (Eq, Show)

data Frame = Frame
  { frName  :: Text
  , frBytes :: LBS.ByteString
  , frFrom  :: State
  , frTo    :: State
  } deriving (Eq, Show)

data ProtocolModel = ProtocolModel
  { pmProtocol :: Text
  , pmInitial  :: State
  , pmFrames   :: [Frame]              -- legal alphabet (flattened)
  , pmByState  :: Map State [Frame]    -- legal frames available from each state
  , pmTerminal :: Set State            -- states with no outgoing legal transition
  , pmAgency   :: Map State Agency     -- protocol-law agency per state
  } deriving (Show)

-- ---- raw JSON shapes -------------------------------------------------------

data RawModel = RawModel { rProtocol :: Text, rSequences :: [RawSeq] }
data RawSeq   = RawSeq   { rInitial :: State, rTransitions :: [RawTrans] }
data RawTrans = RawTrans { rFrom :: State, rTo :: State, rMsg :: RawMsg }
data RawMsg   = RawMsg   { rmName :: Text, rmHex :: Text }

instance FromJSON RawModel where
  parseJSON = withObject "model" $ \o -> RawModel <$> o .: "protocol" <*> o .: "sequences"
instance FromJSON RawSeq where
  parseJSON = withObject "seq" $ \o -> RawSeq <$> o .: "initial_state" <*> o .: "transitions"
instance FromJSON RawTrans where
  parseJSON = withObject "trans" $ \o -> RawTrans <$> o .: "from" <*> o .: "to" <*> o .: "message"
instance FromJSON RawMsg where
  parseJSON = withObject "msg" $ \o -> RawMsg <$> o .: "name" <*> o .: "hex"

-- ---- build ProtocolModel ---------------------------------------------------

decodeHex :: Text -> LBS.ByteString
decodeHex t = case B16.decode (TE.encodeUtf8 t) of
  Right bs -> LBS.fromStrict bs
  Left e   -> error ("Sequencing.Model: bad hex " <> T.unpack t <> ": " <> e)

buildModel :: ByteString -> ProtocolModel
buildModel raw =
  case eitherDecodeStrict' raw of
    Left e  -> error ("Sequencing.Model: parse error: " <> e)
    Right RawModel{rProtocol, rSequences} ->
      let frames =
            [ Frame (rmName rMsg) (decodeHex (rmHex rMsg)) rFrom rTo
            | RawSeq{rTransitions} <- rSequences
            , RawTrans{rFrom, rTo, rMsg} <- rTransitions
            ]
          byState = Map.fromListWith (flip (++)) [ (frFrom f, [f]) | f <- frames ]
          allStates = Set.fromList (concatMap (\f -> [frFrom f, frTo f]) frames)
          terminal = Set.filter (\s -> not (Map.member s byState)) allStates
          initial = case rSequences of (s:_) -> rInitial s; [] -> "idle"
      in ProtocolModel
           { pmProtocol = rProtocol
           , pmInitial  = initial
           , pmFrames   = frames
           , pmByState  = byState
           , pmTerminal = terminal
           , pmAgency   = agencyTable rProtocol
           }

-- Protocol-law agency per state (the M3 JSON does not encode it). Keys are
-- reconciled to the ACTUAL state spellings in data/m3/*.json (verified against
-- the embedded models, 2026-06-28) and the agency is per the ouroboros-network
-- 'Protocol' instances. Any state not listed defaults to ServerAgency
-- (conservative: the adversary plays the responder/server).
agencyTable :: Text -> Map State Agency
agencyTable "chainsync" = Map.fromList
  -- ClientHasAgency StIdle; ServerHasAgency StCanAwait/StMustReply/StIntersect.
  [ ("idle", ClientAgency), ("can-await", ServerAgency), ("must-reply", ServerAgency)
  , ("awaiting-intersect-reply", ServerAgency), ("done", TerminalAgency) ]
agencyTable "blockfetch" = Map.fromList
  -- ClientHasAgency StIdle; ServerHasAgency StBusy(batch-requested)/StStreaming(batch-open).
  [ ("idle", ClientAgency), ("batch-requested", ServerAgency), ("batch-open", ServerAgency)
  , ("done", TerminalAgency) ]
agencyTable "txsubmission" = Map.fromList
  -- TxSubmission2: ServerHasAgency StIdle (server requests txids/txs/done);
  -- ClientHasAgency StTxIds(requesting-txids)/StTxs(requesting-txs). The model's
  -- idle/initialized/txids-replied/txs-replied all sit at StIdle (server's turn).
  [ ("idle", ServerAgency), ("initialized", ServerAgency)
  , ("requesting-txids", ClientAgency), ("requesting-txs", ClientAgency)
  , ("txids-replied", ServerAgency), ("txs-replied", ServerAgency)
  , ("done", TerminalAgency) ]
agencyTable "keepalive" = Map.fromList
  -- ClientHasAgency StClient(idle: client sends MsgKeepAlive/MsgDone);
  -- ServerHasAgency StServer(await-response: server sends MsgKeepAliveResponse).
  [ ("idle", ClientAgency), ("await-response", ServerAgency), ("done", TerminalAgency) ]
agencyTable _ = Map.empty

allModels :: Map Text ProtocolModel
allModels = Map.fromList
  [ ("chainsync",    buildModel rawChainSync)
  , ("blockfetch",   buildModel rawBlockFetch)
  , ("txsubmission", buildModel rawTxSubmission)
  , ("keepalive",    buildModel rawKeepAlive)
  ]
