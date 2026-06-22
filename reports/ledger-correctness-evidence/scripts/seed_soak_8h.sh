#!/usr/bin/env bash
set -uo pipefail
DUR=${1:-28800}
END=$(( $(date +%s) + DUR ))
LOG=/tmp/seed_soak_8h.log
num() { docker exec "$1" sh -lc "$2" 2>/dev/null | tail -1 | tr -dc '0-9'; }
echo "seed-corpus soak start $(date -u +%FT%TZ) dur=${DUR}s img=0.16.0 shape=certificate seeds=2 injector=none" > "$LOG"
n=0
while [ "$(date +%s)" -lt "$END" ]; do
  n=$((n+1))
  chain=$(docker logs dwarf-adversary 2>&1 | grep -oE "getBaseTxsFromChain: [0-9]+ txs" | tail -1 | grep -oE "[0-9]+"); chain=${chain:-NA}
  serv=$(num dwarf-adversary 'grep -c dwarf_served_mutated_tx /tmp/sdk.jsonl'); serv=${serv:-0}
  cert=$(num dwarf-adversary 'grep -c "cert:" /tmp/sdk.jsonl'); cert=${cert:-0}
  certreal=$(num dwarf-adversary 'grep "cert:" /tmp/sdk.jsonl | grep -vc "cert:none"'); certreal=${certreal:-0}
  crit=$(num tracer 'grep -Fc "\"sev\":\"Critical\"" /opt/cardano-tracer/logs/relay2.example_3001/node.json'); crit=${crit:-0}
  rst=$(docker inspect relay2 --format '{{.RestartCount}}' 2>/dev/null || echo NA)
  arst=$(docker inspect dwarf-adversary --format '{{.RestartCount}}' 2>/dev/null || echo NA)
  printf '%s iter=%s chain_txs=%s served=%s cert_tagged=%s cert_real=%s relay2_crit=%s relay2_restart=%s adv_restart=%s\n' \
    "$(date -u +%H:%M:%S)" "$n" "$chain" "$serv" "$cert" "$certreal" "$crit" "$rst" "$arst" >> "$LOG"
  sleep 60
done
echo "seed-corpus soak end $(date -u +%FT%TZ)" >> "$LOG"
