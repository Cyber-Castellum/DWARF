#!/bin/bash
W=/tmp/cfgsweep; CN=/home/dwarf/.local/bin/cardano-node
RES=$W/results/classified.tsv; : > "$RES"
for f in "$W"/muts/*.json; do
  name=$(basename "$f" .json); cp "$f" "$W/configs/shelley-genesis.json"; rm -rf "$W/cndb"; mkdir -p "$W/cndb"
  s=$(date +%s.%N)
  out=$(timeout 3 "$CN" run --topology /tmp/fl2/hrelay-topo.json --config "$W/configs/config.node.json" --database-path "$W/cndb" --socket-path "$W/cn.sock" --port 3055 --host-addr 127.0.0.1 2>&1)
  rc=$?; dur=$(awk "BEGIN{printf \"%.2f\", $(date +%s.%N)-$s}")
  crash=$(echo "$out" | grep -oiE "divide by zero|Ratio has zero denominator|<<loop>>|stack overflow|internal error|Non-exhaustive|arithmetic overflow|arithmetic underflow" | head -1)
  reject=$(echo "$out" | grep -oiE "Value is outside of bounds[^\"]{0,50}|is negative[^\"]{0,40}|parsing [A-Za-z]+ failed[^\"]{0,50}|key .{0,30} not found|Encountered zero[^\"]{0,40}|Value is either floating[^\"]{0,40}|Value is too big[^\"]{0,30}|Error in \$[^\"]{0,55}|expected [A-Za-z]+, but encountered [A-Za-z]+|GenesisDecodeError|InstantiationError" | head -1)
  if [ "$rc" -eq 124 ]; then cls=ACCEPT; msg="not-rejected(3s)"
  elif [ -n "$crash" ]; then cls=CRASH; msg="$crash"
  elif [ -n "$reject" ]; then cls=REJECT; msg="$reject"
  else cls=OTHER; msg="rc=$rc $(echo "$out" | tail -1 | cut -c1-70)"; fi
  printf "%s\t%s\t%s\t%s\t%s\n" "$name" "$cls" "$rc" "$dur" "$msg" >> "$RES"
done
echo "SWEEP_DONE $(grep -c . "$RES")" >> "$RES"
