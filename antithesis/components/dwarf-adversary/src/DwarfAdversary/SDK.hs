{-# LANGUAGE OverloadedStrings #-}

-- |
-- Module: DwarfAdversary.SDK
--
-- Antithesis Fallback SDK emitter for the @cardano-adversary@ CLI.
--
-- The Antithesis runtime watches @\$ANTITHESIS_OUTPUT_DIR/sdk.jsonl@
-- (default @\/tmp\/sdk.jsonl@) and ingests one JSON event per line.
-- Each line carries an @antithesis_assert@ object whose shape is
-- documented at <https://antithesis.com/docs/using_antithesis/sdk/fallback/>.
--
-- This module exposes only the assertion shapes the adversary CLI
-- needs:
--
-- * 'reachable' — fired once per invocation to prove the binary ran.
-- * 'sometimes' — emitted with structured details so the report's
--   per-bucket counters quantify the perturbation profile.
--
-- We deliberately *do not* emit @always@ or @unreachable@ from the
-- attacker. The adversary is allowed to be killed mid-run by fault
-- injection; an @always@ that flips false on chaos would create
-- false positives.
module DwarfAdversary.SDK (
    reachable,
    sometimes,
    always,
) where

import Prelude hiding (always)

import Control.Exception (try)
import Data.Aeson (Value, encode, object, (.=))
import Data.ByteString.Lazy.Char8 qualified as LBS
import Data.Text (Text)
import System.Directory (createDirectoryIfMissing)
import System.Environment (lookupEnv)
import System.FilePath ((</>))
import System.IO (BufferMode (..), IOMode (..), hSetBuffering, withFile)
import System.IO.Error (IOError)

-- | @reachable id details@ — emit a Reachable assertion. Hits exactly
-- once per call site per invocation; useful to prove a code path was
-- entered. @details@ may be 'Data.Aeson.Null' if no payload is needed.
reachable :: Text -> Value -> IO ()
reachable assertId details =
    emit $
        mkEvent
            "reachability"
            "Reachable"
            True
            assertId
            details

-- | @sometimes condition id details@ — emit a Sometimes assertion. The
-- report's Sometimes-true vs Sometimes-false bucket counts how often
-- the condition held. Use this for perturbation metrics where neither
-- 'always' nor 'unreachable' fits.
sometimes :: Bool -> Text -> Value -> IO ()
sometimes condition assertId details =
    emit $
        mkEvent
            "sometimes"
            "Sometimes"
            condition
            assertId
            details

-- | @always condition id details@ — emit an Always assertion (must hold every
-- time it is hit; a single false is a failure). Use this ONLY where there is no
-- fault-injection that could legitimately flip it false — e.g. the in-process
-- decoder-fuzz bug oracle ("the decoder never threw an uncaught exception / never
-- hung"), which runs deterministically and is not killed mid-evaluation.
always :: Bool -> Text -> Value -> IO ()
always condition assertId details =
    emit $
        mkEvent
            "always"
            "Always"
            condition
            assertId
            details

-- | Build a single-line NDJSON SDK event.
mkEvent :: Text -> Text -> Bool -> Text -> Value -> Value
mkEvent assertType displayType condition assertId details =
    object
        [ "antithesis_assert"
            .= object
                [ "id" .= assertId
                , "message" .= assertId
                , "condition" .= condition
                , "display_type" .= displayType
                , "hit" .= True
                , "must_hit" .= True
                , "assert_type" .= assertType
                , "details" .= details
                , "location"
                    .= object
                        [ "file" .= ("" :: Text)
                        , "function" .= ("" :: Text)
                        , "class" .= ("" :: Text)
                        , "begin_line" .= (0 :: Int)
                        , "begin_column" .= (0 :: Int)
                        ]
                ]
        ]

-- | Append one event to @\$ANTITHESIS_OUTPUT_DIR/sdk.jsonl@. Failures
-- are swallowed: the assertion stream is best-effort and must never
-- abort the attack itself.
emit :: Value -> IO ()
emit ev = do
    dir <- maybe "/tmp" id <$> lookupEnv "ANTITHESIS_OUTPUT_DIR"
    let path = dir </> "sdk.jsonl"
    _ <- try @IOError $ do
        createDirectoryIfMissing True dir
        withFile path AppendMode $ \h -> do
            hSetBuffering h LineBuffering
            LBS.hPutStrLn h (encode ev)
    pure ()
