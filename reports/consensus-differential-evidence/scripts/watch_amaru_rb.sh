#!/bin/bash
# Wait for the deep-rollback injection into amaru-lr, capture its reaction.
amaru_h() { ssh cardano-box 'docker logs amaru-lr 2>&1 | grep "adopted tip" | tail -1' 2>/dev/null | grep -oE "tip.block_height=[0-9]+" | grep -oE "[0-9]+" | tail -1; }
for i in $(seq 1 120); do
  INJ=$(ssh cardano-box 'docker logs dwarf-adversary-amaru 2>&1 | grep "INJECTING deep RollBackward" | tail -1' 2>/dev/null)
  H=$(amaru_h); H=${H:-0}
  echo "poll $i: amaru_block_height=$H inj=[$([ -n "$INJ" ] && echo yes || echo no)]"
  if [ -n "$INJ" ]; then
    echo "=== INJECTION: $INJ"
    Hb=$H
    sleep 15
    Ha=$(amaru_h); Ha=${Ha:-0}
    echo "=== amaru block_height: before=$Hb after=$Ha (drop=$((Hb-Ha)))"
    echo "--- amaru-lr reaction (rollback/error/disconnect) ---"
    ssh cardano-box 'docker logs --since 90s amaru-lr 2>&1 | grep -iE "rollback|roll_back|rolledback|invalid|beyond|security|disconnect|error|refuse|switch|adopted tip" | tail -15'
    echo "--- adversary-amaru tail ---"; ssh cardano-box 'docker logs dwarf-adversary-amaru 2>&1 | grep -iE "INJECTING|MsgDone|FindIntersect|exception" | tail -5'
    if [ "$Ha" -ge "$((Hb-2))" ]; then echo "VERDICT amaru REFUSED deep rollback (tip held)"; else echo "VERDICT amaru ROLLED BACK ($((Hb-Ha))) - POSSIBLE VIOLATION / DIVERGENCE"; fi
    exit 0
  fi
  sleep 25
done
echo "TIMEOUT no injection into amaru-lr yet"; exit 2
