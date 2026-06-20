#!/usr/bin/env bash
# SP3a topology-eclipse local validation gate (Antithesis-COMPATIBLE).
#
# Unlike sp3a_eclipse_repro.sh (which uses docker-compose.eclipse.yml to put
# relay2 on an ISOLATED docker network — a trick Antithesis rejects because it
# owns networking), this gate proves the eclipse holds on the SINGLE default
# network using TOPOLOGY ALONE:
#   - relay2 mounts relay-eclipse-topology.json  (sole local root =
#     dwarf-adversary; useLedgerAfterSlot:-1 -> no ledger peers)
#   - the dwarf-adversary does NOT implement peer-sharing gossip, so relay2
#     learns no producer addresses, and the producers never learn relay2's
#     address to dial it inbound -> relay2's only block source is the adversary.
#   - dwarf-adversary runs --protocol blockfetch (advancing serve): it
#     chain-syncs upstream p1 for VALID headers (relay2 adopts -> from-origin
#     Praos nonce bootstrap works, 0 VRFKeyBadProof) and serves MUTATED bodies.
#
# This is the EXACT bundle shape we ship live (full harness, single network),
# minus the harness driver. SUCCESS = dwarf_served_mutated_block > 0,
# RestartCount 0, relay2 VRFKeyBadProof 0, and relay2's established upstream is
# the adversary (NOT a producer).
#
# Run on build-host. Arg: adversary image tag (default 0.10.0).
set -uo pipefail
TAG="${1:-0.10.0}"
LEVEL="${2:-struct}"   # struct | bytes | both  (byte-level = malformed CBOR)
cd ${DWARF_ROOT}/antithesis/cardano_node_dwarf
export INTERNAL_NETWORK=false
DC="docker compose -f docker-compose.yaml"   # NO eclipse override -> single net
cp docker-compose.yaml /tmp/sp3a_topo_compose.bak
restore() { cp /tmp/sp3a_topo_compose.bak docker-compose.yaml; }
trap restore EXIT
# 1) adversary tag + blockfetch/block mode (keep --upstream p1, mutation 0.5)
sed -i "s#dwarf-adversary:0\.[0-9]*\.[0-9]*#dwarf-adversary:${TAG}#" docker-compose.yaml
sed -i 's#^\( *- "\)txsubmission"#\1blockfetch"#; s#^\( *- "\)tx-body"#\1block"#' docker-compose.yaml
# 1b) inject --mutation-level after the cbor-shape value
perl -0pi -e "s/(- \"--cbor-shape\"\n\s*- \"block\")/\$1\n      - \"--mutation-level\"\n      - \"${LEVEL}\"/" docker-compose.yaml
# 2) relay2 topology: dwarf (dual-peer) -> eclipse (adversary-only)
sed -i 's#\./relay-dwarf-topology\.json:/configs/configs/topology\.json#./relay-eclipse-topology.json:/configs/configs/topology.json#' docker-compose.yaml

echo "topology-eclipse SP3a: adversary ${TAG} blockfetch, relay2=eclipse topo, SINGLE net; full reset…"
$DC down --remove-orphans >/dev/null 2>&1
docker volume rm cardano_node_dwarf_relay1-state cardano_node_dwarf_relay2-state >/dev/null 2>&1
$DC up -d configurator tracer tracer-sidecar p1 p2 p3 relay1 dwarf-adversary relay2 >/dev/null 2>&1
NET=$(docker inspect relay2 --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}')
echo "relay2 networks: $NET (expect a single default net)"
echo "observing 230s (producers warm -> adversary advances -> relay2 CaughtUp -> block-fetch)…"; sleep 230

# eclipse integrity: list relay2's ESTABLISHED outbound peers on node port 3001
# and resolve each remote IP to a container name. Should be the adversary only.
echo "=== relay2 established peers (remote ip -> container) ==="
docker exec relay2 sh -lc "cat /proc/net/tcp" 2>/dev/null \
  | awk '$4=="01"{print $3}' | sed 's/:.*//' \
  | sort -u | while read hx; do
      [ -z "$hx" ] && continue
      ip=$(printf "%d.%d.%d.%d" 0x${hx:6:2} 0x${hx:4:2} 0x${hx:2:2} 0x${hx:0:2})
      name=$(docker ps --format '{{.Names}}' | while read c; do
               cip=$(docker inspect "$c" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null)
               [ "$cip" = "$ip" ] && echo "$c"; done)
      echo "  $ip -> ${name:-<external>}"
    done

SERVED=$(docker exec dwarf-adversary sh -lc "grep -c dwarf_served_mutated_block /tmp/sdk.jsonl 2>/dev/null || echo 0")
VRF=$(docker exec tracer cat /opt/cardano-tracer/logs/relay2.example_3001/node.json 2>/dev/null | grep -c VRFKeyBadProof)
RC=$(docker inspect dwarf-adversary --format '{{.RestartCount}}')
echo "dwarf_served_mutated_block=$SERVED  relay2 VRFKeyBadProof=$VRF  adversary RestartCount=$RC"
if [ "$SERVED" -gt 0 ] && [ "$RC" -eq 0 ] && [ "$VRF" -eq 0 ]; then
  echo "OK: SP3a seam fired under TOPOLOGY eclipse on a single network (Antithesis-compatible)."
else
  echo "FAIL: served=$SERVED RC=$RC VRF=$VRF"; exit 1
fi
