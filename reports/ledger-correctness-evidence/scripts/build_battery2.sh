#!/usr/bin/env bash
set -uo pipefail
ADDR="addr_test1<REDACTED-TESTNET-ADDR>"
M=42; IMG=$(docker inspect relay1 --format "{{.Image}}"); W=/tmp/battery2; rm -rf $W; mkdir -p $W; chmod 777 $W
cli(){ sudo docker run --rm -v cardano_node_dwarf_relay1-state:/state -v cardano_node_dwarf_utxo-keys:/utxo-keys:ro -v $W:/work -e CARDANO_NODE_SOCKET_PATH=/state/node.socket --network container:relay1 --entrypoint cardano-cli "$IMG" "$@"; }
read TXIN AMT < <(cli conway query utxo --address "$ADDR" --testnet-magic $M --output-json | python3 -c "import json,sys;u=json.load(sys.stdin);k=max(u,key=lambda x:u[x][\"value\"][\"lovelace\"]);print(k,u[k][\"value\"][\"lovelace\"])")
SLOT=$(cli conway query tip --testnet-magic $M --socket-path /state/node.socket | python3 -c "import json,sys;print(json.load(sys.stdin)[\"slot\"])")
FUT=$((SLOT+5000000)); OUT=$((AMT-200000))
echo "input=$TXIN amt=$AMT slot=$SLOT"
sgn(){ cli conway transaction sign --tx-body-file /work/$1.body --signing-key-file /utxo-keys/payment.1.skey --testnet-magic $M --out-file /work/$1.signed; }
# 4) validity-interval lower (invalid-before in the future)
cli conway transaction build-raw --tx-in "$TXIN" --tx-out "$ADDR+$OUT" --fee 200000 --invalid-before "$FUT" --out-file /work/vintlo.body && sgn vintlo
# 5) BadInputs: forged nonexistent input
FORGED="0000000000000000000000000000000000000000000000000000000000000000#0"
cli conway transaction build-raw --tx-in "$FORGED" --tx-out "$ADDR+1000000" --fee 200000 --out-file /work/badin.body && sgn badin
# 6) OutputTooSmall: 1 lovelace output
cli conway transaction build-raw --tx-in "$TXIN" --tx-out "$ADDR+1" --fee 200000 --out-file /work/small.body && sgn small
# 9) zero-value output
cli conway transaction build-raw --tx-in "$TXIN" --tx-out "$ADDR+0" --fee 200000 --out-file /work/zero.body && sgn zero
# 7) MaxTxSize: oversized metadata (~40KB)
python3 -c "import json;print(json.dumps({\"674\":{\"big\":[\"x\"*64]*640}}))" > $W/big.json
cli conway transaction build-raw --tx-in "$TXIN" --tx-out "$ADDR+$OUT" --fee 200000 --metadata-json-file /work/big.json --out-file /work/maxsz.body && sgn maxsz
sudo chmod -R a+r $W; echo "=== built ==="; ls -1 $W/*.signed
