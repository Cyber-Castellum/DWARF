#!/usr/bin/env bash
# Stage-1 behavioral gate: run `scenario verify` over the structured
# cardano-node cbor-fuzz scenarios (the ones that meaningfully exercise
# decode + roundtrip against the built shims). Reports OK/FAIL totals.
#
# Run on cardano-box (where the shim binaries are installed).
# Usage: tools/stage1_verify.sh ['glob']   (default: structured cbor-fuzz)
set -uo pipefail
cd "$(dirname "$0")/.."
glob="${1:-dwarf/scenarios/cardano-node-cbor-*-structured.yaml}"
ok=0; fail=0
for f in $glob; do
  [ -e "$f" ] || continue
  n=$(basename "$f")
  if PYTHONPATH=dwarf python3 dwarf/cardano-profile scenario verify "$f" \
       --runs-dir /tmp/stage1-runs --state-dir /tmp/stage1-state >/tmp/s1v.out 2>&1; then
    ok=$((ok+1)); echo "OK   $n"
  else
    fail=$((fail+1)); echo "FAIL $n"; grep -oE 'FAIL:.*' /tmp/s1v.out | head -1
  fi
done
echo "verify OK=$ok FAIL=$fail"
[ "$fail" -eq 0 ]
