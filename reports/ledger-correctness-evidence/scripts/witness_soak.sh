#!/usr/bin/env bash
set -uo pipefail
DUR=${1:-3600}; END=$(( $(date +%s)+DUR )); LOG=/tmp/witness_soak.log
IMG=$(docker inspect relay1 --format "{{.Image}}")
num(){ docker exec "$1" sh -lc "$2" 2>/dev/null | tail -1 | tr -dc '0-9'; }
echo "witness soak start $(date -u +%FT%TZ) dur=${DUR}s img=0.17.0 shape=witness" > "$LOG"
n=0
while [ "$(date +%s)" -lt "$END" ]; do
  n=$((n+1))
  sudo docker run --rm -v cardano_node_dwarf_relay1-state:/state -v cardano_node_dwarf_utxo-keys:/utxo-keys:ro \
    -v /tmp/cwork:/work -v /tmp/inject_corpus.sh:/inject_corpus.sh:ro \
    --network container:relay1 --entrypoint bash "$IMG" /inject_corpus.sh >/dev/null 2>&1
  b=$(docker exec tracer cat /opt/cardano-tracer/logs/relay2.example_3001/node.json 2>/dev/null)
  rej=$(printf "%s" "$b" | grep -Fc MempoolRejectedTx); add=$(printf "%s" "$b" | grep -Fc MempoolAddedTx)
  wit=$(num dwarf-adversary "grep -c \"wit:\" /tmp/sdk.jsonl"); wit=${wit:-0}
  crit=$(printf "%s" "$b" | grep -Fc "\"sev\":\"Critical\""); rst=$(docker inspect relay2 --format "{{.RestartCount}}")
  printf "%s iter=%s wit=%s mempoolReject=%s mempoolAdd=%s relay2_crit=%s relay2_restart=%s\n" "$(date -u +%H:%M:%S)" "$n" "$wit" "$rej" "$add" "$crit" "$rst" >> "$LOG"
  sleep 45
done
echo "witness soak end $(date -u +%FT%TZ)" >> "$LOG"
