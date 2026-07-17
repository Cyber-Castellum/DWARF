#!/bin/bash
for i in $(seq 1 90); do
  INJ=$(ssh cardano-box 'docker logs dwarf-adversary-lr 2>&1 | grep "INJECTING deep RollBackward" | tail -1' 2>/dev/null)
  R=$(ssh cardano-box 'docker exec relay-lr cardano-cli query tip --testnet-magic 42 --socket-path /state/node.socket 2>/dev/null' 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('block',0))" 2>/dev/null || echo 0)
  echo "poll $i: relay-lr_tip=$R inj=[$INJ]"
  if [ -n "$INJ" ]; then
    echo "=== INJECTION: $INJ"
    Rb=$R
    sleep 12
    Ra=$(ssh cardano-box 'docker exec relay-lr cardano-cli query tip --testnet-magic 42 --socket-path /state/node.socket 2>/dev/null' 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('block',0))" 2>/dev/null || echo 0)
    echo "=== relay-lr tip: before=$Rb after=$Ra (drop=$((Rb-Ra)))"
    echo "--- adversary tail ---"; ssh cardano-box 'docker logs dwarf-adversary-lr 2>&1 | grep -iE "INJECTING|node sent MsgDone|node sent MsgFindIntersect|exception" | tail -6'
    echo "--- relay-lr chainsync err/reaction ---"; ssh cardano-box 'docker logs --since 60s relay-lr 2>&1 | grep -iE "rollback|rolledback|invalid|exceed|candidate|intersect|disconnect|MuxError|ExceededRollback|ChainSyncClient" | tail -12'
    if [ "$Ra" -ge "$((Rb-2))" ]; then echo "VERDICT cardano-node REFUSED deep rollback (tip held)"; else echo "VERDICT cardano-node ROLLED BACK ($((Rb-Ra)) blocks) - POSSIBLE VIOLATION"; fi
    exit 0
  fi
  sleep 20
done
echo "TIMEOUT no injection (chainVar may not have reached min-tip)"; exit 2
