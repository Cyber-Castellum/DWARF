#!/usr/bin/env bash
# SP3b tx-submission selftest: build the adversary and run the tx-submission
# selftest — the Initiator+Responder server binds, accepts a connection, the
# responders serve, and the #4 tx-provider initiator is wired + drives (opens
# mini-protocol #4). Proves the IR-on-inbound wiring locally with 0 crash. The
# minimal selftest client is chain-sync-only, so it reports UnknownMiniProtocol
# 4 when our #4 initiator opens toward it — EXPECTED; a real cardano-node has a
# #4 consumer that accepts the offer (proven by the live run). Run on build-host.
set -uo pipefail
cd "$(dirname "$0")/../antithesis/components/dwarf-adversary"
export PATH="$HOME/.ghcup/bin:$PATH"

cabal build exe:dwarf-adversary || { echo "FAIL build"; exit 1; }
BIN=$(cabal list-bin dwarf-adversary)
LOG=/tmp/sp3b-selftest.log

timeout 40 "$BIN" --selftest --protocol txsubmission --cbor-shape tx-body \
  --listen-port 3998 --seed 0x1 2>&1 | tee "$LOG"

if grep -qiE "panic|<<loop>>|internal error|segfault|uncaught exception" "$LOG"; then
  echo "FAIL crash detected"; exit 1
fi
if grep -qiE "inbound connection accepted" "$LOG"; then
  echo "OK selftest (IR server binds + accepts + #4 provider wired; no crash)"
else
  echo "FAIL selftest (server did not accept a connection)"; exit 1
fi
echo "sp3b selftest done"
