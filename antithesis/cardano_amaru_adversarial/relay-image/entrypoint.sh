#!/bin/sh
# DWARF baked-store Amaru relay for the adversarial mixed bundle.
#   - amaru v10.11.20260807 (epoch-transition rewards crash FIXED, so the honest
#     control relay survives past epoch 4 — the whole point of this image)
#   - baked testnet_42 store bootstrapped from the d807adv cardano cluster (k=20)
#   - serve-only: upstream #736 blocks single-peer forward-sync, so the relay serves
#     its store and is attacked via block-fetch by dwarf-adversary.
set -eu
BAKED=/opt/amaru-baked/store
STORE="${AMARU_STORE:-/tmp/amaru-run}"
if [ ! -d "$STORE/ledger.testnet_42.db" ]; then
  mkdir -p "$STORE"
  cp -a "$BAKED/." "$STORE/"
  find "$STORE" -name LOCK -delete 2>/dev/null || true
fi
# k=20 / f=0.2 / epochLength=400 globals (must match the baked store)
export AMARU_GLOBAL_CONSENSUS_SECURITY_PARAM="${AMARU_GLOBAL_CONSENSUS_SECURITY_PARAM:-20}"
export AMARU_GLOBAL_ACTIVE_SLOT_COEFF_INVERSE="${AMARU_GLOBAL_ACTIVE_SLOT_COEFF_INVERSE:-5}"
export AMARU_GLOBAL_EPOCH_LENGTH_SCALE_FACTOR="${AMARU_GLOBAL_EPOCH_LENGTH_SCALE_FACTOR:-4}"
export AMARU_GLOBAL_MAX_LOVELACE_SUPPLY="${AMARU_GLOBAL_MAX_LOVELACE_SUPPLY:-45000000000000000}"
export AMARU_GLOBAL_SLOTS_PER_KES_PERIOD="${AMARU_GLOBAL_SLOTS_PER_KES_PERIOD:-129600}"
export AMARU_GLOBAL_MAX_KES_EVOLUTION="${AMARU_GLOBAL_MAX_KES_EVOLUTION:-62}"
export AMARU_LOG="${AMARU_LOG:-error,amaru=info}"
[ -n "${AMARU_GLOBAL_SYSTEM_START:-}" ] && export AMARU_GLOBAL_SYSTEM_START
# AMARU_PEER_ADDRESS may list SEVERAL peers (comma or space separated) so one relay
# can hold an HONEST upstream and a MALICIOUS one at the same time. That is the
# realistic attack model: the node syncs the real chain from the honest peer while
# the adversary offers forged blocks, making "does it ever prefer the forged chain?"
# a live question instead of a vacuous one.
PEERS=""
for _p in $(echo "${AMARU_PEER_ADDRESS:-10.255.255.1:3001}" | tr ',' ' '); do
  PEERS="$PEERS --peer-address $_p"
done
exec /usr/local/bin/amaru node run --network testnet_42 \
  --era-history "$STORE/era-history.json" \
  --ledger-dir "$STORE/ledger.testnet_42.db" \
  --chain-dir  "$STORE/chain.testnet_42.db" \
  --listen-address "${AMARU_LISTEN_ADDRESS:-0.0.0.0:3001}" \
  $PEERS \
  --peer-removal-cooldown-secs "${AMARU_PEER_REMOVAL_COOLDOWN_SECS:-86400}"
