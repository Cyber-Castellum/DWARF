#!/usr/bin/env bash
# SP3a block-adoption fix — local testnet repro / verification gate.
# Brings up the cardano_node_dwarf testnet subset with the block-fetch adversary
# and relay2 RESET TO GENESIS (matching a fresh Antithesis run), then checks that
# relay2 ADVANCES: it block-fetches bodies from the adversary, decodes them, and
# adds blocks to its ChainDB (AddedBlockToVolatileDB / ValidCandidate) — instead
# of the pre-fix loop (FindIntersect -> 3x RequestNext -> reset, 0 blocks added).
# Run on cardano-box. Set ADV_TAG to the adversary image tag to test.
set -uo pipefail
ADV_TAG="${1:-0.4.0}"
cd /home/nigel/dwarf-v4/antithesis/cardano_node_dwarf
export INTERNAL_NETWORK=false
sed -i "s#dwarf-adversary:0\.[0-9]*\.[0-9]*#dwarf-adversary:${ADV_TAG}#" docker-compose.yaml
docker compose up -d configurator tracer tracer-sidecar p1 p2 p3 >/dev/null 2>&1
docker compose up -d --force-recreate dwarf-adversary >/dev/null 2>&1
docker compose rm -sf relay2 >/dev/null 2>&1
docker volume rm cardano_node_dwarf_relay2-state >/dev/null 2>&1
docker compose up -d relay2 >/dev/null 2>&1
echo "adversary ${ADV_TAG} + fresh relay2 up; waiting for block-fetch…"
for i in $(seq 1 40); do
  n=$(docker logs relay2 2>&1 | grep -ciE "AddedBlockToVolatileDB|AddedToCurrentChain|ValidCandidate")
  [ "$n" -gt 0 ] && break
  sleep 6
done
ADDED=$(docker logs relay2 2>&1 | grep -ciE "AddedBlockToVolatileDB|AddedToCurrentChain|ValidCandidate")
REQ=$(docker logs dwarf-adversary 2>&1 | grep -c "MsgRequestNext")
echo "relay2 blocks added: $ADDED   adversary MsgRequestNext: $REQ"
if [ "$ADDED" -gt 0 ]; then
  echo "OK repro: relay2 adopts + block-fetches + decodes (gate passed)"
else
  echo "FAIL repro: relay2 added no blocks (still not block-fetching)"; exit 1
fi
