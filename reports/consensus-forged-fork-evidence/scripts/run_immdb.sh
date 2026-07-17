#!/bin/bash
ssh cardano-box '
T=/tmp/occ/ouroboros-consensus-cardano-0.25.1.0/dist-newstyle/build/x86_64-linux/ghc-9.6.7/ouroboros-consensus-cardano-0.25.1.0/x
nohup $T/immdb-server/build/immdb-server/immdb-server --db /tmp/planB/forkdb --port 3010 --config /tmp/planB/configs/config.json > /tmp/planB/immdb.log 2>&1 &
echo "immdb-server pid $!"; sleep 8
echo "=== immdb log ==="; tail -15 /tmp/planB/immdb.log
echo "=== listening on 3010? ==="; ss -ltn 2>/dev/null | grep 3010 || echo "not listening"'
