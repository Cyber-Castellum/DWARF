#!/bin/bash
ssh cardano-box 'cd /tmp/occ/ouroboros-consensus-cardano-0.25.1.0
export PATH=$HOME/.ghcup/bin:$PATH
cabal build -w ghc-9.6.7 exe:db-synthesizer exe:db-truncater exe:immdb-server 2>&1 | tail -30
echo "CABAL_EXIT=${PIPESTATUS[0]}"
for x in db-synthesizer db-truncater immdb-server; do
  B=$(find dist-newstyle -name "$x" -type f 2>/dev/null | head -1)
  [ -x "$B" ] && echo "OK $x -> $B" || echo "MISSING $x"
done'
