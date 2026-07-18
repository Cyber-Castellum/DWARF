#!/usr/bin/env bash
# For each mutated shelley-genesis, start cardano-node and classify accept/reject/crash + latency.
# config.node.json = testnet_42 config.json with DijkstraGenesisFile added (node 10.7.1 requires it).
set -eu
W=/tmp/cfgdiff; CN=/home/nigel/.local/bin/cardano-node
for name in $(python3 -c "import json;print(' '.join(json.load(open('$W/muts/_order.json'))))"); do
  cp "$W/muts/$name.json" "$W/configs/shelley-genesis.json"; rm -rf "$W/cndb"; mkdir -p "$W/cndb"
  s=$(date +%s.%N)
  out=$(timeout 15 "$CN" run --topology /tmp/fl2/hrelay-topo.json --config "$W/configs/config.node.json" \
        --database-path "$W/cndb" --socket-path "$W/cn.sock" --port 3055 --host-addr 127.0.0.1 2>&1); rc=$?
  dur=$(python3 -c "import sys;print(f'{$(date +%s.%N)-$s:.2f}s')")
  echo "$name rc=$rc ${dur} $(echo "$out" | grep -oiE 'Value is outside of bounds[^"]*|is negative[^"]*|divide by zero|Ratio has zero denominator|Encountered zero[^"]*|parsing [A-Za-z]+ failed[^"]*|key .* not found' | head -1)"
done
