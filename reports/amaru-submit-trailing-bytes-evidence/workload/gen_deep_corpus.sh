#!/bin/bash
# Generate a deep Conway tx corpus (certs / governance / witness / datum) via build-raw.
# No funds needed — these only need to SERIALIZE + DECODE on both nodes (then fail validation).
set -u
M=42
W=/tmp/deepcorpus
rm -rf $W; mkdir -p $W/out; cd $W
CLI="cardano-cli conway"
DUMMYIN="0000000000000000000000000000000000000000000000000000000000000000#0"
HASH32=$(printf '0%.0s' {1..64})   # 64 hex zeros = 32-byte hash

ok=0; fail=0
emit(){ # $1 label ; expects tx.signed present
  if [ -f tx.signed ]; then
    cp tx.signed out/$1.signed && { echo "OK   $1"; ok=$((ok+1)); } || { echo "FAIL $1 (copy)"; fail=$((fail+1)); }
    rm -f tx.signed tx.raw
  else
    echo "FAIL $1 (no tx.signed)"; fail=$((fail+1)); rm -f tx.raw
  fi
}

# --- keys ---
cardano-cli address key-gen --verification-key-file pay.vkey --signing-key-file pay.skey 2>/dev/null
$CLI stake-address key-gen --verification-key-file stake.vkey --signing-key-file stake.skey 2>/dev/null
$CLI governance drep key-gen --verification-key-file drep.vkey --signing-key-file drep.skey 2>/dev/null
ADDR=$(cardano-cli address build --payment-verification-key-file pay.vkey --testnet-magic $M 2>/dev/null)
echo "addr=$ADDR"

# 1) plain payment (baseline, matches the #1-style seed)
$CLI transaction build-raw --tx-in $DUMMYIN --tx-out "$ADDR+1000000" --fee 200000 --out-file tx.raw 2>/dev/null
$CLI transaction sign --tx-body-file tx.raw --signing-key-file pay.skey --testnet-magic $M --out-file tx.signed 2>/dev/null
emit c01-plain

# 2) stake registration cert
$CLI stake-address registration-certificate --stake-verification-key-file stake.vkey --key-reg-deposit-amt 2000000 --out-file stake-reg.cert 2>/dev/null
$CLI transaction build-raw --tx-in $DUMMYIN --tx-out "$ADDR+1000000" --fee 200000 --certificate-file stake-reg.cert --out-file tx.raw 2>/dev/null
$CLI transaction sign --tx-body-file tx.raw --signing-key-file pay.skey --signing-key-file stake.skey --testnet-magic $M --out-file tx.signed 2>/dev/null
emit c02-stake-reg

# 3) stake vote-delegation to a DRep (abstain)
$CLI stake-address vote-delegation-certificate --stake-verification-key-file stake.vkey --always-abstain --out-file votedeleg.cert 2>/dev/null
$CLI transaction build-raw --tx-in $DUMMYIN --tx-out "$ADDR+1000000" --fee 200000 --certificate-file votedeleg.cert --out-file tx.raw 2>/dev/null
$CLI transaction sign --tx-body-file tx.raw --signing-key-file pay.skey --signing-key-file stake.skey --testnet-magic $M --out-file tx.signed 2>/dev/null
emit c03-vote-deleg-abstain

# 4) DRep registration cert
$CLI governance drep registration-certificate --drep-verification-key-file drep.vkey --key-reg-deposit-amt 500000000 --out-file drep-reg.cert 2>/dev/null
$CLI transaction build-raw --tx-in $DUMMYIN --tx-out "$ADDR+1000000" --fee 200000 --certificate-file drep-reg.cert --out-file tx.raw 2>/dev/null
$CLI transaction sign --tx-body-file tx.raw --signing-key-file pay.skey --signing-key-file drep.skey --testnet-magic $M --out-file tx.signed 2>/dev/null
emit c04-drep-reg

# 5) governance INFO action
$CLI governance action create-info --testnet --governance-action-deposit 100000000000 \
  --deposit-return-stake-verification-key-file stake.vkey \
  --anchor-url "https://x" --anchor-data-hash $HASH32 --out-file info.action 2>/dev/null
$CLI transaction build-raw --tx-in $DUMMYIN --tx-out "$ADDR+1000000" --fee 200000 --proposal-file info.action --out-file tx.raw 2>/dev/null
$CLI transaction sign --tx-body-file tx.raw --signing-key-file pay.skey --testnet-magic $M --out-file tx.signed 2>/dev/null
emit c05-gov-info-action

# 6) DRep vote on a (dummy) action
$CLI governance vote create --yes --governance-action-tx-id $HASH32 --governance-action-index 0 \
  --drep-verification-key-file drep.vkey --out-file drep.vote 2>/dev/null
$CLI transaction build-raw --tx-in $DUMMYIN --tx-out "$ADDR+1000000" --fee 200000 --vote-file drep.vote --out-file tx.raw 2>/dev/null
$CLI transaction sign --tx-body-file tx.raw --signing-key-file pay.skey --signing-key-file drep.skey --testnet-magic $M --out-file tx.signed 2>/dev/null
emit c06-drep-vote

# 7) tx-out with datum hash
$CLI transaction build-raw --tx-in $DUMMYIN --tx-out "$ADDR+1000000" --tx-out-datum-hash $HASH32 --fee 200000 --out-file tx.raw 2>/dev/null
$CLI transaction sign --tx-body-file tx.raw --signing-key-file pay.skey --testnet-magic $M --out-file tx.signed 2>/dev/null
emit c07-datum-hash

# 8) multiple certs (stake reg + vote deleg) in one tx
$CLI transaction build-raw --tx-in $DUMMYIN --tx-out "$ADDR+1000000" --fee 200000 \
  --certificate-file stake-reg.cert --certificate-file votedeleg.cert --out-file tx.raw 2>/dev/null
$CLI transaction sign --tx-body-file tx.raw --signing-key-file pay.skey --signing-key-file stake.skey --testnet-magic $M --out-file tx.signed 2>/dev/null
emit c08-multi-cert

# 9) tx with metadata (auxiliary data present -> array(4) with non-null aux)
echo '{"0":{"string":"dwarf"}}' > meta.json
$CLI transaction build-raw --tx-in $DUMMYIN --tx-out "$ADDR+1000000" --fee 200000 --metadata-json-file meta.json --out-file tx.raw 2>/dev/null
$CLI transaction sign --tx-body-file tx.raw --signing-key-file pay.skey --testnet-magic $M --out-file tx.signed 2>/dev/null
emit c09-with-metadata

# 10) tx with TTL + validity interval
$CLI transaction build-raw --tx-in $DUMMYIN --tx-out "$ADDR+1000000" --fee 200000 --invalid-hereafter 999999 --invalid-before 1 --out-file tx.raw 2>/dev/null
$CLI transaction sign --tx-body-file tx.raw --signing-key-file pay.skey --testnet-magic $M --out-file tx.signed 2>/dev/null
emit c10-validity-interval

echo "=== SUMMARY ok=$ok fail=$fail ==="
ls -la out/
