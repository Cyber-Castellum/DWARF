#!/bin/sh
# Boot a baked-store Amaru as a serve-only node (no upstream sync).
# Verified locally: loads ledger (build_ledger tip.hash=...), starts submit-api on
# 3011, listens on 3001, stays up (restarts=0). The only logged ERROR is the
# cosmetic "timeout fetching blocks" (pragma-org/amaru #1050 — normal with no peer).
set -eu

BAKED=/opt/amaru-baked/store
STORE=/srv/amaru

# Copy the baked template to a writable location once (RocksDB needs write).
if [ ! -d "$STORE/ledger.testnet_42.db" ] || [ ! -d "$STORE/chain.testnet_42.db" ]; then
  mkdir -p "$STORE"
  find "$STORE" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
  cp -a "$BAKED/." "$STORE/"
fi

# testnet_42 global parameters (k=5, f=0.2, epoch scale 5) — must match the store.
export AMARU_GLOBAL_CONSENSUS_SECURITY_PARAM=5
export AMARU_GLOBAL_ACTIVE_SLOT_COEFF_INVERSE=5
export AMARU_GLOBAL_EPOCH_LENGTH_SCALE_FACTOR=5
export AMARU_GLOBAL_MAX_LOVELACE_SUPPLY=45000000000000000
export AMARU_GLOBAL_SLOTS_PER_KES_PERIOD=129600
export AMARU_GLOBAL_MAX_KES_EVOLUTION=62
export AMARU_GLOBAL_SYSTEM_START=0

# A dummy peer keeps the peer-selection stage happy without a real upstream. The
# long removal cooldown stops a tight reconnect loop against the unreachable addr.
DUMMY_PEER="${AMARU_DUMMY_PEER:-10.255.255.1:3001}"

# Binary path differs by base image (upstream pragma-org = /usr/local/bin/amaru;
# older lambdasistemi = /bin/amaru). Resolve whichever exists.
AMARU_BIN="$(command -v amaru || echo /usr/local/bin/amaru)"

exec "$AMARU_BIN" run \
  --network testnet_42 \
  --ledger-dir "$STORE/ledger.testnet_42.db" \
  --chain-dir "$STORE/chain.testnet_42.db" \
  --era-history "$STORE/era-history.json" \
  --listen-address 0.0.0.0:3001 \
  --submit-api-address 0.0.0.0:3011 \
  --peer-address "$DUMMY_PEER" \
  --peer-removal-cooldown-secs 86400
