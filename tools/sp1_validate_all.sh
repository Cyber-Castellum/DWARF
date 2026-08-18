#!/usr/bin/env bash
# Validate every restored SP1 scenario semantically; exit non-zero if any fail.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0; ok=0
while read -r name; do
  [ -z "$name" ] && continue
  if PYTHONPATH=dwarf python3 dwarf/cardano-profile scenario validate --semantic \
       "dwarf/scenarios/$name" >/tmp/sp1val.out 2>&1; then
    ok=$((ok+1))
  else
    fail=$((fail+1)); echo "FAIL: $name"; grep -E '^FAIL:' /tmp/sp1val.out | head -3
  fi
done < sp1-closure/scenarios.txt
echo "validated OK=$ok FAIL=$fail"
[ "$fail" -eq 0 ]
