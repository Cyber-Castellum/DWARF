#!/bin/bash
# Drive genesis-victim (GDD) + amaru-victim (Praos) to their chain-selection decisions.
# honest tip=30520 (dense), fork tip=30553 (longer/sparse). Divergence = they pick differently.
gtip() { ssh cardano-box 'docker exec pb-genesis-victim cardano-cli query tip --testnet-magic 42 --socket-path /state/node.socket 2>/dev/null' 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('block',0))" 2>/dev/null || echo 0; }
atip() { ssh cardano-box 'docker logs pb-amaru-victim 2>&1 | grep "adopted tip" | tail -1' 2>/dev/null | grep -oE "tip.block_height=[0-9]+" | grep -oE "[0-9]+" | tail -1; }
aprev=-1; astall=0; gs=0; as=0; gprev=-1
for i in $(seq 1 200); do
  G=$(gtip); G=${G:-0}; A=$(atip); A=${A:-0}
  echo "poll $i: genesis=$G amaru=$A"
  # amaru self-heal
  if [ "$A" = "$aprev" ] && [ "$A" -gt 0 ] && [ "$A" -lt 30500 ]; then astall=$((astall+1)); else astall=0; fi
  aprev=$A
  [ "$astall" -ge 5 ] && { echo "heal amaru at $A"; ssh cardano-box 'docker restart pb-amaru-victim >/dev/null 2>&1'; astall=0; sleep 15; }
  # settle detection: both at >=30500 and stable
  [ "$G" = "$gprev" ] && [ "$G" -ge 30500 ] && gs=$((gs+1)) || gs=0; gprev=$G
  [ "$A" = "$aprev" ] && [ "$A" -ge 30500 ] && as=$((as+1)) || true
  if [ "$G" -ge 30500 ] && [ "$A" -ge 30500 ] && [ "$gs" -ge 3 ]; then
    echo "=== BOTH SETTLED: genesis=$G amaru=$A (honest=30520 fork=30553) ==="
    if [ "$G" != "$A" ]; then echo "VERDICT DIVERGENCE: genesis(GDD)=$G vs amaru(Praos)=$A  <-- FINDING"; else echo "VERDICT AGREE both=$G"; fi
    echo "--- genesis GDD logs ---"; ssh cardano-box 'docker logs pb-genesis-victim 2>&1 | grep -aiE "GDD|density|disconnect|CandidateB" | tail -6'
    exit 0
  fi
  sleep 30
done
echo "TIMEOUT genesis=$(gtip) amaru=$(atip)"; exit 2
