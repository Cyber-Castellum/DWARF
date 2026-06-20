#!/usr/bin/env bash
# SP3a block-fetch selftest: build the adversary and run the block-fetch
# selftest — the combined responder (chain-sync #2 + block-fetch #3) completes
# the N2N handshake and our own block-fetch client drives mini-protocol #3
# against it. Proves the block-fetch wiring locally before spending Antithesis
# time (the mutated-block serve+decode is proven on Antithesis with real
# in-bundle blocks). Expect: handshake completes, a clean client result, NO
# crash/panic. Run on build-host (needs the ghcup ghc-9.6.7 toolchain).
set -uo pipefail
cd "$(dirname "$0")/../antithesis/components/dwarf-adversary"
export PATH="$HOME/.ghcup/bin:$PATH"

cabal build exe:dwarf-adversary || { echo "FAIL build"; exit 1; }
BIN=$(cabal list-bin dwarf-adversary)
LOG=/tmp/sp3a-selftest.log

timeout 60 "$BIN" --selftest --protocol blockfetch --cbor-shape block \
  --listen-port 3999 --seed 0x1 --mutation-rate 0.5 2>&1 | tee "$LOG"

if grep -qiE "panic|<<loop>>|internal error|segfault|MuxError.*Bearer|uncaught" "$LOG"; then
  echo "FAIL crash detected"; exit 1
fi
if grep -qiE "selftest\(blockfetch\): (no blocks served|received a block)" "$LOG"; then
  echo "OK selftest"
else
  echo "FAIL selftest (no clean client result)"; exit 1
fi
echo "sp3a selftest done"
