#!/usr/bin/env bash
# Serve the false-leadership attack chain and run both victims.
#   - cardano-node victim: fresh, network-syncs the chain and validates every header.
#   - Amaru victim: bootstrapped on the honest chain, then peers the relay.
set -eu
W=/tmp/fl2
NET=fl2net
AIMG=ghcr.io/lambdasistemi/amaru-bootstrap-producer:03d2727b71e8d1fe7c793d5036dce3c3ce294f6c
CN_IMG=dwarf/cardano-node:10.7.1
RT=/home/dwarf/cardano-node-antithesis/testnets/cardano_amaru/amaru-runtime

docker network create $NET 2>/dev/null || true

# --- relay serving the attack chain (db-synthesizer dbs need a protocolMagicId marker) ---
rm -rf $W/relay-db; cp -r $W/db_attack3 $W/relay-db; printf 42 > $W/relay-db/protocolMagicId
printf '%s' '{"localRoots":[{"accessPoints":[],"advertise":false,"trustable":true,"valency":0}],"publicRoots":[],"useLedgerAfterSlot":-1}' > $W/relay-topo.json
docker rm -f fl2-relay 2>/dev/null || true
docker run -d --name fl2-relay --network $NET --network-alias relay.example -p 3021:3001 \
  -v $W/relay-db:/state -v $W/configs:/configs/configs:ro -v $W/relay-topo.json:/configs/configs/topology.json:ro \
  $CN_IMG run --topology /configs/configs/topology.json --config /configs/configs/config.json \
  --database-path /state --socket-path /state/node.socket --port 3001 --host-addr 0.0.0.0

# --- Amaru bundle from the HONEST chain (needs the 'lock' marker + epoch>=3) ---
rm -rf $W/honest-src3; cp -r $W/db_honest2 $W/honest-src3
printf 42 > $W/honest-src3/protocolMagicId; touch $W/honest-src3/lock
rm -rf $W/bundle3; mkdir -p $W/bundle3
docker rm -f fl2-boot 2>/dev/null || true
docker run --name fl2-boot \
  -v $W/honest-src3:/cardano/state -v $W/configs:/cardano/config/configs:ro -v $W/bundle3:/srv/amaru \
  --entrypoint /bin/bootstrap-producer $AIMG \
  /cardano/state /cardano/config/configs /srv/amaru testnet_42

# --- Amaru victim: bootstrapped at slot 646, peers the relay ---
docker rm -f fl2-amaru 2>/dev/null || true
docker run -d --name fl2-amaru --network $NET --network-alias amaru.example \
  -e AMARU_LOG=info \
  -e AMARU_GLOBAL_CONSENSUS_SECURITY_PARAM=5 -e AMARU_GLOBAL_ACTIVE_SLOT_COEFF_INVERSE=5 \
  -e AMARU_GLOBAL_EPOCH_LENGTH_SCALE_FACTOR=5 -e AMARU_GLOBAL_MAX_LOVELACE_SUPPLY=45000000000000000 \
  -e AMARU_GLOBAL_SLOTS_PER_KES_PERIOD=129600 -e AMARU_GLOBAL_MAX_KES_EVOLUTION=62 -e AMARU_GLOBAL_SYSTEM_START=0 \
  -v $W/bundle3:/srv/amaru -v $RT:/amaru-runtime:ro \
  --entrypoint /bin/amaru $AIMG \
  run --network testnet_42 \
  --ledger-dir /srv/amaru/testnet_42/ledger.testnet_42.db \
  --chain-dir /srv/amaru/testnet_42/chain.testnet_42.db \
  --era-history /amaru-runtime/era-history.json \
  --listen-address 0.0.0.0:3001 --peer-address relay.example:3001

sleep 20
echo '=== Amaru verdict (expect: Insufficient leader stake, never adopts past 646) ==='
docker logs fl2-amaru 2>&1 | grep -iE 'Insufficient leader stake|adopted tip'

# --- cardano-node victim (control): fresh, network-syncs from a host relay on the same chain ---
# (run a host cardano-node relay on db_attack3 at 127.0.0.1:3020, then:)
#   cardano-node run --config config.json (WITH DijkstraGenesisFile) \
#     --topology <points at 127.0.0.1:3020> --database-path <fresh> --port 3011
# Expect repeated: VRFLeaderValueTooBig <leaderVal> (sigma) (ActiveSlotCoeff f)
