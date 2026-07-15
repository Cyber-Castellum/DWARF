#!/usr/bin/env bash
# SP3a-eclipse local validation gate. Brings up the cardano_node_dwarf testnet
# with the ECLIPSE override (docker-compose.eclipse.yml): the node under test
# (relay2) is on an isolated network reaching ONLY the dwarf-adversary, which
# bridges both nets (default to chain-sync its upstream p1, eclipse to serve
# relay2). The adversary runs --protocol blockfetch: it serves VALID headers
# (relay2 adopts -> the from-origin Praos nonce bootstrap works under eclipse,
# 0 VRFKeyBadProof) and MUTATED block bodies, which relay2 — having no other
# peer — block-fetches and decodes (dwarf_served_mutated_block). This is the
# SP3a fuzzing seam that approach-B dual-peer could not fire (relay2 fetched
# bodies only from the trusted producers). SUCCESS = dwarf_served_mutated_block
# > 0 with RestartCount 0 and relay2 reaching exactly 1 peer (the adversary).
# Run on cardano-box. Arg: adversary image tag (default 0.8.0).
set -uo pipefail
TAG="${1:-0.9.0}"
cd /home/nigel/dwarf-v4/antithesis/cardano_node_dwarf
export INTERNAL_NETWORK=false
DC="docker compose -f docker-compose.yaml -f docker-compose.eclipse.yml"
cp docker-compose.yaml /tmp/sp3a_eclipse_compose.bak
restore() { cp /tmp/sp3a_eclipse_compose.bak docker-compose.yaml; }
trap restore EXIT
sed -i "s#dwarf-adversary:0\.[0-9]*\.[0-9]*#dwarf-adversary:${TAG}#" docker-compose.yaml
# blockfetch mode (mutated bodies); keep mutation-rate as set in compose (0.5)
sed -i 's#^\( *- "\)txsubmission"#\1blockfetch"#; s#^\( *- "\)tx-body"#\1block"#' docker-compose.yaml
echo "eclipse SP3a: adversary ${TAG} blockfetch, relay2 isolated; full reset…"
$DC down --remove-orphans >/dev/null 2>&1
docker volume rm cardano_node_dwarf_relay1-state cardano_node_dwarf_relay2-state >/dev/null 2>&1
$DC up -d configurator tracer tracer-sidecar p1 p2 p3 relay1 dwarf-adversary relay2 >/dev/null 2>&1
NET=$(docker inspect relay2 --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')
echo "relay2 network: $NET (expect *_eclipse only)"
echo "observing 230s…"; sleep 230
PEERS=$(docker exec relay2 sh -lc "cat /proc/net/tcp" 2>/dev/null | awk '$4=="01"{c++}END{print c+0}')
SERVED=$(docker exec dwarf-adversary sh -lc "grep -c dwarf_served_mutated_block /tmp/sdk.jsonl 2>/dev/null || echo 0")
VRF=$(docker exec tracer cat /opt/cardano-tracer/logs/relay2.example_3001/node.json 2>/dev/null | grep -c VRFKeyBadProof)
RC=$(docker inspect dwarf-adversary --format '{{.RestartCount}}')
echo "relay2 peers=$PEERS  dwarf_served_mutated_block=$SERVED  relay2 VRFKeyBadProof=$VRF  adversary RestartCount=$RC"
if [ "$SERVED" -gt 0 ] && [ "$RC" -eq 0 ]; then
  echo "OK: SP3a seam fired under eclipse (node decoded $SERVED mutated blocks from its sole peer)."
else
  echo "FAIL: SP3a seam did not fire (served=$SERVED RC=$RC)."; exit 1
fi
