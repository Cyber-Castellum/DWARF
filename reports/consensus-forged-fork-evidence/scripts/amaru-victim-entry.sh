#!/bin/sh
set -eu
if [ ! -d /srv/amaru/ledger.testnet_42.db ] || [ ! -d /srv/amaru/chain.testnet_42.db ]; then
  until [ -d /bundle/testnet_42/ledger.testnet_42.db ] && [ -d /bundle/testnet_42/chain.testnet_42.db ]; do sleep 5; done
  find /srv/amaru -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  cp -rL /bundle/testnet_42/. /srv/amaru/
fi
export AMARU_GLOBAL_CONSENSUS_SECURITY_PARAM=5
export AMARU_GLOBAL_ACTIVE_SLOT_COEFF_INVERSE=5
export AMARU_GLOBAL_EPOCH_LENGTH_SCALE_FACTOR=5
export AMARU_GLOBAL_MAX_LOVELACE_SUPPLY=45000000000000000
export AMARU_GLOBAL_SLOTS_PER_KES_PERIOD=129600
export AMARU_GLOBAL_MAX_KES_EVOLUTION=62
export AMARU_GLOBAL_SYSTEM_START=0
echo "amaru-victim: peering with honest-relay + attacker-relay (Praos longest-chain choice)"
exec /bin/amaru run --network testnet_42 \
  --ledger-dir /srv/amaru/ledger.testnet_42.db \
  --chain-dir /srv/amaru/chain.testnet_42.db \
  --era-history /amaru-runtime/era-history.json \
  --listen-address 0.0.0.0:3001 \
  --peer-address attacker-relay.example:3001
