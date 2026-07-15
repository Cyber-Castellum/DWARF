#!/usr/bin/env bash
# SP3-foundation local validation: does the advancing CaughtUp peer make the
# node start tx-submission? Brings up the tx adversary (advancing chain-sync
# producer + roll-forward server) + a fresh relay2 (sole peer = adversary).
# The producer syncs the upstream chain to its RECENT tip, the advancing server
# keeps relay2 at that tip -> relay2 reaches GSM CaughtUp -> after the 60s
# tx-submission init delay it sends MsgRequestTxIds. The adversary logs that as
# "tx-submission: offering" (txs present) or "batch exhausted; parking" (empty
# batch — local chain has no txs, but the REQUEST arriving is the proof).
#
# SUCCESS = RequestTxIds-received > 0 with RestartCount 0. Before this fix
# (5-header static chain) it was 0 (relay2 looped FindIntersect->reset, never
# CaughtUp). Run on cardano-box.
set -uo pipefail
TAG="${1:-0.9.0}"
cd /home/nigel/dwarf-v4/antithesis/cardano_node_dwarf
export INTERNAL_NETWORK=false
sed -i "s#dwarf-adversary:0\.[0-9]*\.[0-9]*#dwarf-adversary:${TAG}#" docker-compose.yaml
# FULL reset to genesis: a fresh SHORT chain means relay2 catches up in seconds
# (vs replaying a multi-hour backlog), so it reaches CaughtUp inside the window.
echo "full testnet reset to genesis…"
docker compose down >/dev/null 2>&1
docker volume rm cardano_node_dwarf_relay1-state cardano_node_dwarf_relay2-state >/dev/null 2>&1
docker compose up -d configurator tracer tracer-sidecar p1 p2 p3 relay1 dwarf-adversary relay2 >/dev/null 2>&1
echo "adversary ${TAG} (tx, advancing peer) on a fresh short chain; observing 200s (producers warm -> relay2 CaughtUp -> 60s init delay)…"
sleep 200
echo "=== adversary chain-sync + tx-submission activity (tail) ==="
docker logs dwarf-adversary 2>&1 | grep -iE "inbound connection accepted|chain-sync\(advancing\)|tx-submission:|getBaseTxsFromChain|waiting for producer" | tail -20
REQTX=$(docker logs dwarf-adversary 2>&1 | grep -cE "tx-submission: offering|batch exhausted|no fresh tx; waiting")
ACC=$(docker logs dwarf-adversary 2>&1 | grep -c "inbound connection accepted")
RFW=$(docker logs dwarf-adversary 2>&1 | grep -c "chain-sync(advancing): node sent MsgRequestNext")
RC=$(docker inspect dwarf-adversary --format '{{.RestartCount}}')
echo "connections=$ACC  chain-sync RequestNext=$RFW  RequestTxIds-received=$REQTX  RestartCount=$RC"
if [ "$REQTX" -gt 0 ] && [ "$RC" -eq 0 ]; then
  echo "OK: relay2 reached CaughtUp + STARTED tx-submission (sent RequestTxIds). Foundation validated."
else
  echo "STILL FAILING: relay2 did not request txs (REQTX=$REQTX RC=$RC). Re-investigate before any live run."; exit 1
fi
