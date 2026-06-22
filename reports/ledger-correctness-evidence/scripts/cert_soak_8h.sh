#!/usr/bin/env bash
set -uo pipefail
DUR=${1:-28800}   # seconds (default 8h)
END=$(( $(date +%s) + DUR ))
LOG=/tmp/cert_soak_8h.log
IMG=$(docker inspect relay1 --format '{{.Image}}')
echo "cert/aux soak start $(date -u +%FT%TZ) dur=${DUR}s img=0.15.0 shape=certificate" > "$LOG"
n=0
while [ "$(date +%s)" -lt "$END" ]; do
  n=$((n+1))
  sudo docker run --rm -v cardano_node_dwarf_relay1-state:/state -v cardano_node_dwarf_utxo-keys:/utxo-keys:ro \
    -v /tmp/cwork:/work -v /tmp/inject_corpus.sh:/inject_corpus.sh:ro \
    --network container:relay1 --entrypoint bash "$IMG" /inject_corpus.sh >/dev/null 2>&1
  cert=$(docker exec dwarf-adversary sh -lc 'grep -c "cert:" /tmp/sdk.jsonl 2>/dev/null' 2>/dev/null || echo 0)
  certreal=$(docker exec dwarf-adversary sh -lc 'grep "cert:" /tmp/sdk.jsonl 2>/dev/null | grep -vc "cert:none"' 2>/dev/null || echo 0)
  serv=$(docker exec dwarf-adversary sh -lc 'grep -c dwarf_served_mutated_tx /tmp/sdk.jsonl 2>/dev/null' 2>/dev/null || echo 0)
  crit=$(docker exec tracer sh -lc 'grep -Fc "\"sev\":\"Critical\"" /opt/cardano-tracer/logs/relay2.example_3001/node.json' 2>/dev/null || echo 0)
  rst=$(docker inspect relay2 --format '{{.RestartCount}}' 2>/dev/null || echo NA)
  arst=$(docker inspect dwarf-adversary --format '{{.RestartCount}}' 2>/dev/null || echo NA)
  echo "$(date -u +%H:%M:%S) iter=$n served=$serv cert_tagged=$cert cert_real=$certreal relay2_crit=$crit relay2_restart=$rst adv_restart=$arst" >> "$LOG"
  sleep 45
done
echo "cert/aux soak end $(date -u +%FT%TZ)" >> "$LOG"
