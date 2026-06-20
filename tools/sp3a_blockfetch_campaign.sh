#!/usr/bin/env bash
# SP3a block-fetch LOCAL exhaustive campaign (long soak).
#
# Brings up the topology-eclipse stack on the single default network — relay2
# eclipsed by topology (sole peer = the dwarf-adversary), adversary 0.11.0 in
# --protocol blockfetch with --seed random — and runs for HOURS, serving a
# continuous stream of structurally-mutated block bodies to relay2 and logging
# metrics periodically.
#
# Bug oracle: a malformed-CBOR REJECTION by the node is EXPECTED and fine; a
# CRASH / unexpected exit / restart of relay2 is a FINDING. We track relay2's
# RestartCount + status, the adversary RestartCount, served count, and VRF.
#
# Note: locally the adversary draws ONE random seed at startup and runs the whole
# soak with it (no per-timeline seed explosion — that is Antithesis's job). The
# local value is a long soak against EVOLVING real-chain content + crash watch.
#
# Run on build-host. Args: TAG (default 0.11.0) HOURS (default 8).
set -uo pipefail
TAG="${1:-0.11.0}"
HOURS="${2:-8}"
LEVEL="${3:-struct}"   # struct | bytes | both (byte-level = malformed CBOR)
cd ${DWARF_ROOT}/antithesis/cardano_node_dwarf
export INTERNAL_NETWORK=false
DC="docker compose -f docker-compose.yaml"
cp docker-compose.yaml /tmp/sp3a_campaign_compose.bak
restore() { cp /tmp/sp3a_campaign_compose.bak docker-compose.yaml; }
trap restore EXIT
# blockfetch/block + eclipse topology + --seed random + --mutation-level
sed -i "s#dwarf-adversary:0\.[0-9]*\.[0-9]*#dwarf-adversary:${TAG}#" docker-compose.yaml
sed -i 's#^\( *- "\)txsubmission"#\1blockfetch"#; s#^\( *- "\)tx-body"#\1block"#' docker-compose.yaml
sed -i 's#\./relay-dwarf-topology\.json:/configs/configs/topology\.json#./relay-eclipse-topology.json:/configs/configs/topology.json#' docker-compose.yaml
perl -0pi -e 's/(- "--seed"\n\s*- ")0x1(")/${1}random${2}/' docker-compose.yaml
perl -0pi -e "s/(- \"--cbor-shape\"\n\s*- \"block\")/\$1\n      - \"--mutation-level\"\n      - \"${LEVEL}\"/" docker-compose.yaml

LOG=/tmp/sp3a_campaign.log
echo "$(date -u +%FT%TZ) CAMPAIGN START: adversary ${TAG} blockfetch eclipse, ${HOURS}h" | tee "$LOG"
$DC down --remove-orphans >/dev/null 2>&1
docker volume rm cardano_node_dwarf_relay1-state cardano_node_dwarf_relay2-state >/dev/null 2>&1
$DC up -d configurator tracer tracer-sidecar p1 p2 p3 relay1 dwarf-adversary relay2 >/dev/null 2>&1
restore  # args are baked into the running containers; restore the repo file now

END=$(( $(date +%s) + HOURS*3600 ))
sleep 60
SEED=$(docker logs dwarf-adversary 2>&1 | grep -oE 'reproduce with --seed 0x[0-9a-f]+' | head -1)
echo "$(date -u +%FT%TZ) seed: ${SEED:-<not logged yet>}" | tee -a "$LOG"

while [ "$(date +%s)" -lt "$END" ]; do
  SERVED=$(docker exec dwarf-adversary sh -lc "grep -c dwarf_served_mutated_block /tmp/sdk.jsonl 2>/dev/null || echo 0" 2>/dev/null)
  VRF=$(docker exec tracer cat /opt/cardano-tracer/logs/relay2.example_3001/node.json 2>/dev/null | grep -c VRFKeyBadProof)
  ARC=$(docker inspect dwarf-adversary --format '{{.RestartCount}}' 2>/dev/null)
  RRC=$(docker inspect relay2 --format '{{.RestartCount}}' 2>/dev/null)
  RST=$(docker inspect relay2 --format '{{.State.Status}}' 2>/dev/null)
  FLAG=""; [ "${RRC:-0}" -gt 0 ] 2>/dev/null && FLAG="  <-- relay2 RESTARTED (investigate)"
  echo "$(date -u +%FT%TZ) served=${SERVED:-?} VRF=${VRF:-?} advRestart=${ARC:-?} relay2Restart=${RRC:-?} relay2=${RST:-?}${FLAG}" | tee -a "$LOG"
  sleep 900
done

SERVED=$(docker exec dwarf-adversary sh -lc "grep -c dwarf_served_mutated_block /tmp/sdk.jsonl 2>/dev/null || echo 0" 2>/dev/null)
RRC=$(docker inspect relay2 --format '{{.RestartCount}}' 2>/dev/null)
ARC=$(docker inspect dwarf-adversary --format '{{.RestartCount}}' 2>/dev/null)
VERDICT="CLEAN (no relay2 crash)"; [ "${RRC:-0}" -gt 0 ] 2>/dev/null && VERDICT="FINDING: relay2 restarted ${RRC}x"
echo "$(date -u +%FT%TZ) CAMPAIGN DONE: served=${SERVED} relay2Restart=${RRC} advRestart=${ARC} -> ${VERDICT}" | tee -a "$LOG"
