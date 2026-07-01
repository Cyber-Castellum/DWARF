#!/usr/bin/env bash
# Build Conway governance wire GenTxs against a FORGING gov devnet (default govdev).
# Steps: fan-out the funded utxo into N inputs, build valid actions + one-rule-invalid
# variants, wrap each to the node-to-node wire GenTx [6, tag24(tx)]. Host-orchestrated:
# cardano-cli runs (as root) in the relay container; CBOR wrapping on the host.
#
# Usage: gov-corpus-build.sh [PROJECT] [OUTDIR]   (PROJECT=govdev, OUTDIR=/tmp/corpus-gov)
set -uo pipefail
PROJ="${1:-govdev}"; OUT="${2:-/tmp/corpus-gov}"; M=42
RELAY="${PROJ}-relay1-1"
IMG=$(docker inspect "$RELAY" --format '{{.Image}}')
W=/tmp/${PROJ}-govbuild; rm -rf "$W"; mkdir -p "$W"; chmod 777 "$W"
rm -rf "$OUT"; mkdir -p "$OUT"
HERE=$(cd "$(dirname "$0")" && pwd)
cli(){ sudo -n docker run --rm -v ${PROJ}_relay1-state:/state -v ${PROJ}_utxo-keys:/utxo-keys:ro -v $W:/work \
  -e CARDANO_NODE_SOCKET_PATH=/state/node.socket --network container:$RELAY --entrypoint cardano-cli "$IMG" "$@"; }
catf(){ sudo -n docker run --rm -v $W:/work --entrypoint cat "$IMG" "/work/$1"; }   # root file -> host stdout
ZH="$(python3 -c 'print("0"*64)')"

DEP=$(cli conway query gov-state --testnet-magic $M | python3 -c "import json,sys;print(json.load(sys.stdin)['currentPParams']['govActionDeposit'])")
ADDR=$(cli address build --payment-verification-key-file /utxo-keys/payment.1.vkey --stake-verification-key-file /utxo-keys/stake.1.vkey --testnet-magic $M)
echo "devnet=$PROJ deposit=$DEP addr=$ADDR"

# ---- fan-out: split the single funded utxo into N independent outputs ----
N=10; PER=$((DEP+2000000))   # each output covers one deposit + fee headroom
cli conway query utxo --address "$ADDR" --testnet-magic $M --output-json > $W/u0.json 2>/dev/null
read TXIN AMT < <(python3 -c "import json;u=json.load(open('$W/u0.json'));b=max(u.items(),key=lambda kv:kv[1]['value']['lovelace']);print(b[0],b[1]['value']['lovelace'])")
OUTS=""; for i in $(seq 1 $N); do OUTS="$OUTS --tx-out $ADDR+$PER"; done
CH=$((AMT - N*PER - 1000000))
cli conway transaction build-raw --tx-in "$TXIN" $OUTS --tx-out "$ADDR+$CH" --fee 1000000 --out-file /work/fan.body >/dev/null
cli conway transaction sign --tx-body-file /work/fan.body --signing-key-file /utxo-keys/payment.1.skey --testnet-magic $M --out-file /work/fan.signed >/dev/null
FANTX=$(cli conway transaction txid --tx-file /work/fan.signed | python3 -c "import json,sys;print(json.load(sys.stdin)['txhash'])")
echo "fan-out txid=$FANTX; submitting…"; cli conway transaction submit --tx-file /work/fan.signed --testnet-magic $M 2>&1 | tail -1
echo "waiting for fan-out to confirm (forging devnet)…"
for i in $(seq 1 30); do
  cli conway query utxo --address "$ADDR" --testnet-magic $M --output-json > $W/uf.json 2>/dev/null
  FRESH=$(python3 -c "import json;u=json.load(open('$W/uf.json'));print(' '.join(k for k in u if k.startswith('$FANTX') and u[k]['value']['lovelace']==$PER))")
  [ "$(echo $FRESH | wc -w)" -ge $N ] && break; sleep 6
done
read -a INPUTS <<< "$FRESH"; echo "fresh inputs: ${#INPUTS[@]}"
IDX=0

# ---- build one gov action tx (valid or invalid), wrap to wire GenTx ----
# args: name  deposit  <create-* args...>
build(){
  local name=$1 dep=$2; shift 2
  local IN=${INPUTS[$IDX]}; IDX=$((IDX+1)); local CHG=$((PER - dep - 300000))
  cli conway governance action "$@" --governance-action-deposit $dep \
    --deposit-return-stake-verification-key-file /utxo-keys/stake.1.vkey \
    --anchor-url "https://example.com/$name.json" --anchor-data-hash "$ZH" \
    --testnet --out-file /work/$name.action >/dev/null 2>&1 || { echo "  [$name] action build FAILED"; return 1; }
  cli conway transaction build-raw --tx-in "$IN" --tx-out "$ADDR+$CHG" --fee 300000 \
    --proposal-file /work/$name.action --out-file /work/$name.body >/dev/null 2>&1 || { echo "  [$name] body FAILED"; return 1; }
  cli conway transaction sign --tx-body-file /work/$name.body --signing-key-file /utxo-keys/payment.1.skey \
    --testnet-magic $M --out-file /work/$name.signed >/dev/null 2>&1
  catf "$name.signed" > "$OUT/$name.signed.json"
  python3 "$HERE/wrap_gentx.py" "$OUT/$name.signed.json" "$OUT/$name.cbor" >/dev/null
  echo "  [$name] built + wrapped ($(wc -c <"$OUT/$name.cbor") bytes)  input=$IN"
}

echo "=== valid bases ==="
build info        $DEP create-info
build constitution $DEP create-constitution --constitution-url "https://example.com/c.json" --constitution-hash "$ZH"
build treasury    $DEP create-treasury-withdrawal --funds-receiving-stake-verification-key-file /utxo-keys/stake.1.vkey --transfer 1000000
echo "=== violations (one rule each) ==="
build v-deposit   $((DEP-1)) create-info                                                                  # -> ProposalDepositIncorrect
build v-zerowdl   $DEP       create-treasury-withdrawal --funds-receiving-stake-verification-key-file /utxo-keys/stake.1.vkey --transfer 0   # -> ZeroTreasuryWithdrawals
build v-previd    $DEP       create-constitution --constitution-url "https://example.com/c.json" --constitution-hash "$ZH" \
                             --prev-governance-action-tx-id 0000000000000000000000000000000000000000000000000000000000000000 --prev-governance-action-index 0  # -> InvalidPrevGovActionId

echo "=== corpus-gov ==="; ls -1 "$OUT"/*.cbor 2>/dev/null
