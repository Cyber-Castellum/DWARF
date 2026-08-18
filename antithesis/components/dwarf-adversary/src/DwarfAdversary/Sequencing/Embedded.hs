{-# LANGUAGE TemplateHaskell #-}

-- | The four target M3 models embedded into the binary as raw bytes.
--
-- This module is deliberately TINY and imports ONLY @Data.FileEmbed@ +
-- @Data.ByteString@. Reason: on this box the dependency store is built with
-- native SanitizerCoverage instrumentation, so loading aeson's transitive dep
-- @OneTuple@ into GHC's TemplateHaskell session fails with
-- @undefined symbol: __sanitizer_cov_trace_pc_guard_init@. Keeping the TH splice
-- (@embedFile@) in a module that does NOT import aeson means the TH session only
-- links @file-embed@'s deps, never the instrumented @OneTuple@. The aeson parse
-- lives in "DwarfAdversary.Sequencing.Model", which uses no TH.
module DwarfAdversary.Sequencing.Embedded
  ( rawChainSync
  , rawBlockFetch
  , rawTxSubmission
  , rawKeepAlive
  ) where

import Data.ByteString (ByteString)
import Data.FileEmbed (embedFile)

rawChainSync, rawBlockFetch, rawTxSubmission, rawKeepAlive :: ByteString
rawChainSync    = $(embedFile "data/m3/chainsync-state-machine.json")
rawBlockFetch   = $(embedFile "data/m3/blockfetch-state-machine.json")
rawTxSubmission = $(embedFile "data/m3/txsubmission-state-machine.json")
rawKeepAlive    = $(embedFile "data/m3/keepalive-state-machine.json")
