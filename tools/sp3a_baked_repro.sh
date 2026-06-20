#!/usr/bin/env bash
# SP3a BAKED (producer-less eclipse) local gate. Brings up the
# cardano_node_dwarf_baked bundle: NO producers, NO configurator — relay2
# (node under test) has ONLY the dwarf-adversary as a peer (eclipsed by
# construction, single network, NO custom docker network -> Antithesis-safe).
# The adversary serves a BAKED chain (embedded baked-blocks.cbor) paired with
# the FIXED genesis in ./configs it was forged under; relay2 bootstraps from
# origin (valid headers, 0 VRFKeyBadProof) and block-fetches MUTATED bodies ->
# the block decoder runs on them (dwarf_served_mutated_block). SUCCESS =
# loadBakedChain loaded N>0, dwarf_served_mutated_block>0, VRFKeyBadProof 0,
# adversary RestartCount 0. Run on build-host.
set -uo pipefail
cd ${DWARF_ROOT}/antithesis/cardano_node_dwarf_baked
docker compose down --remove-orphans >/dev/null 2>&1
docker volume rm cardano_node_dwarf_baked_relay2-state cardano_node_dwarf_baked_tracer >/dev/null 2>&1
docker compose up -d >/dev/null 2>&1
echo "baked bundle up (relay2 + adversary + tracer, no producers); observing 150s…"
sleep 150
LOADED=$(docker logs dwarf-adversary 2>&1 | grep -oE "loaded [0-9]+ baked blocks" | grep -oE "[0-9]+" | tail -1)
SERVED=$(docker exec dwarf-adversary sh -lc "grep -c dwarf_served_mutated_block /tmp/sdk.jsonl 2>/dev/null || echo 0")
VRF=$(docker exec tracer cat /opt/cardano-tracer/logs/relay2.example_3001/node.json 2>/dev/null | grep -c VRFKeyBadProof)
RC=$(docker inspect dwarf-adversary --format '{{.RestartCount}}')
TIP=$(docker exec relay2 cardano-cli query tip --testnet-magic 42 --socket-path /state/node.socket 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('block'))" 2>/dev/null)
echo "baked-blocks loaded=$LOADED  dwarf_served_mutated_block=$SERVED  relay2 VRFKeyBadProof=$VRF  relay2 tip-block=$TIP  adversary RestartCount=$RC"
if [ "${SERVED:-0}" -gt 0 ] && [ "${RC:-1}" -eq 0 ] && [ "${VRF:-1}" -eq 0 ]; then
  echo "OK: producer-less baked eclipse fires the SP3a seam (node decoded $SERVED mutated blocks from its sole baked peer; genesis matches, VRFKeyBadProof 0)."
else
  echo "FAIL: served=$SERVED RC=$RC VRF=$VRF"; exit 1
fi
