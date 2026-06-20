#!/usr/bin/env bash
# Build a battery of ledger-INVALID Conway txs (valid unspent inputs, one rule
# violation each) for the #2 validation-bypass probe. Host-orchestrated:
# cardano-cli runs in the cardano-node container; parsing on the host (python3).
set -uo pipefail
ADDR="addr_test1qz75g6s5f0t5he4qh9x8p9xxhjkyxm7ajkp67klqgdq8dedsxus3vre9cqa243j8v69wurxjncl55dts6g4tjzxwm7ds2ja0n3"
M=42
IMG=$(docker inspect relay1 --format '{{.Image}}')
WORK=/tmp/battery; rm -rf $WORK; mkdir -p $WORK; chmod 777 $WORK
cli() { sudo docker run --rm -v cardano_node_dwarf_relay1-state:/state -v cardano_node_dwarf_utxo-keys:/utxo-keys:ro -v $WORK:/work \
  -e CARDANO_NODE_SOCKET_PATH=/state/node.socket --network container:relay1 --entrypoint cardano-cli "$IMG" "$@"; }
# --- pick an unspent input + its lovelace ---
cli conway query utxo --address "$ADDR" --testnet-magic $M --output-json > $WORK/utxo.json 2>$WORK/err
read TXIN AMT < <(python3 -c "
import json
u=json.load(open('$WORK/utxo.json'))
# pick the largest-lovelace utxo
best=max(u.items(), key=lambda kv: kv[1]['value']['lovelace'])
print(best[0], best[1]['value']['lovelace'])")
echo "input=$TXIN lovelace=$AMT"
SLOT=$(cli conway query tip --testnet-magic $M | python3 -c "import json,sys;print(json.load(sys.stdin)['slot'])")
PAST=$((SLOT-1000)); OUT=$((AMT-200000))
echo "slot=$SLOT past=$PAST out=$OUT"
mk() { cli conway transaction build-raw "$@"; }   # args after include --out-file /work/...
# 1) value-not-conserved: output == full input, plus a 200k fee => out+fee > in
mk --tx-in "$TXIN" --tx-out "$ADDR+$AMT" --fee 200000 --out-file /work/vnc.body
cli conway transaction sign --tx-body-file /work/vnc.body --signing-key-file /utxo-keys/payment.1.skey --testnet-magic $M --out-file /work/vnc.signed
# 2) fee-too-low: fee=0
mk --tx-in "$TXIN" --tx-out "$ADDR+$OUT" --fee 0 --out-file /work/fee0.body
cli conway transaction sign --tx-body-file /work/fee0.body --signing-key-file /utxo-keys/payment.1.skey --testnet-magic $M --out-file /work/fee0.signed
# 3) ttl-expired: invalid-hereafter in the past, otherwise valid + signed
mk --tx-in "$TXIN" --tx-out "$ADDR+$OUT" --fee 200000 --invalid-hereafter "$PAST" --out-file /work/ttl.body
cli conway transaction sign --tx-body-file /work/ttl.body --signing-key-file /utxo-keys/payment.1.skey --testnet-magic $M --out-file /work/ttl.signed
# 4) wrong-key witness: balanced, signed with payment.2 (not the input owner)
mk --tx-in "$TXIN" --tx-out "$ADDR+$OUT" --fee 200000 --out-file /work/wk.body
cli conway transaction sign --tx-body-file /work/wk.body --signing-key-file /utxo-keys/payment.2.skey --testnet-magic $M --out-file /work/wk.signed
# 5) missing-witness: assemble with NO witnesses
mk --tx-in "$TXIN" --tx-out "$ADDR+$OUT" --fee 200000 --out-file /work/miss.body
cli conway transaction assemble --tx-body-file /work/miss.body --out-file /work/miss.signed 2>$WORK/miss.err || echo "assemble(miss) note: $(cat $WORK/miss.err)"
echo "=== built ==="; ls -1 $WORK/*.signed 2>/dev/null
