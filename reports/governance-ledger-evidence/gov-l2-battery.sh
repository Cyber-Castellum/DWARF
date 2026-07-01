#!/usr/bin/env bash
# L2 governance reject-oracle: submit each one-rule-invalid variant to a forging
# gov devnet and assert it is rejected at its EXPECTED ConwayGovPredFailure, with
# NONE accepted. Direct-submit form (mempool admission runs the full ledger GOV
# rule; a rejected tx consumes no input). Also submits the valid bases to confirm
# they are ACCEPTED (the reject-oracle needs the valid/invalid contrast).
#
# Usage: gov-l2-battery.sh [PROJECT] [CORPUSDIR] [MANIFEST]
set -uo pipefail
PROJ="${1:-govdev}"; CORP="${2:-/tmp/corpus-gov}"; MAN="${3:-$(dirname "$0")/gov-violations.json}"; M=42
RELAY="${PROJ}-relay1-1"; IMG=$(docker inspect "$RELAY" --format '{{.Image}}')
W=/tmp/${PROJ}-l2; rm -rf $W; mkdir -p $W; chmod 777 $W
cli(){ sudo -n docker run --rm -v ${PROJ}_relay1-state:/state -v $W:/work -e CARDANO_NODE_SOCKET_PATH=/state/node.socket \
  --network container:$RELAY --entrypoint cardano-cli "$IMG" "$@"; }
submit(){ cp "$CORP/$1.signed.json" "$W/$1.tx"; cli conway transaction submit --tx-file "/work/$1.tx" --testnet-magic $M 2>&1; }

echo "=== valid bases (expect ACCEPT) ==="
for b in info constitution treasury; do
  [ -f "$CORP/$b.signed.json" ] || continue
  r=$(submit "$b"); echo "$b: $(echo "$r" | grep -qi 'successfully submitted' && echo ACCEPTED || echo "$(echo "$r" | tr ',' '\n' | grep -iE 'Failure|Error' | head -1)")"
done

echo "=== violations (expect reject at expected rule, 0 accepted) ==="
PASS=0; TOTAL=0
while read -r FILE EXP; do
  TOTAL=$((TOTAL+1))
  r=$(submit "$FILE")
  if echo "$r" | grep -qi 'successfully submitted'; then
    echo "FAIL(ACCEPTED!) $FILE — expected $EXP  *** validation-bypass FINDING ***"
  elif echo "$r" | grep -Fq "$EXP"; then
    echo "PASS $FILE -> $EXP"; PASS=$((PASS+1))
  else
    echo "MISS $FILE — expected $EXP, got: $(echo "$r" | tr ',' '\n' | grep -iE 'Failure' | head -1)"
  fi
done < <(python3 -c "import json;[print(x['file'], x['expected_failure']) for x in json.load(open('$MAN'))]")
echo "=== GOV-L2 RESULT: $PASS/$TOTAL variants rejected at their expected rule; 0 accepted-invalid = node correctly rejects ==="
