#!/usr/bin/env bash
# Build the false-leadership-patched db-synthesizer.
# The patch (../patch/db-synthesizer-false-leadership.patch) adds an env gate
# DWARF_FALSE_LEADERSHIP=1 that raises the per-era active-slot coefficient
# (praosLeaderF / tpraosLeaderF) to ~0.99 in the consensus config used to forge
# and to validate the forged blocks, leaving praosRandomnessStabilisationWindow
# and the genesis config untouched (so the forged chain still connects to the
# real genesis root).
set -eu
OCC=/tmp/occ/ouroboros-consensus-cardano-0.25.1.0

# apply patch to Run.hs (idempotent-ish; keep a .orig backup first)
cd "$OCC/src/unstable-cardano-tools/Cardano/Tools/DBSynthesizer"
[ -f Run.hs.orig ] || cp Run.hs Run.hs.orig
patch -p0 < /path/to/patch/db-synthesizer-false-leadership.patch || true

# IMPORTANT: build with ghcup's GHC 9.6.7, NOT the system ghc (9.4.7).
# The system compiler recomputes the whole plan and fails on the dep tree.
export PATH="$HOME/.ghcup/bin:$PATH"
cd "$OCC"
cabal build db-synthesizer -w ghc-9.6.7
cabal list-bin db-synthesizer -w ghc-9.6.7
