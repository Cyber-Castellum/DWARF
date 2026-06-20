#!/usr/bin/env bash
# Build a DIVERSE decoder corpus (#3): txs exercising distinct Conway decoder
# branches (mint/multiasset, aux-data, certs, multi-in/out, edge values). Only
# needs to be DECODABLE (structure), not ledger-valid — it's decoder fuzz seed.
set -uo pipefail
ADDR="addr_test1qz75g6s5f0t5he4qh9x8p9xxhjkyxm7ajkp67klqgdq8dedsxus3vre9cqa243j8v69wurxjncl55dts6g4tjzxwm7ds2ja0n3"
M=42; IMG=$(docker inspect relay1 --format "{{.Image}}")
W=/tmp/corpus-build; rm -rf $W; mkdir -p $W; chmod 777 $W
cli(){ sudo docker run --rm -v cardano_node_dwarf_relay1-state:/state -v cardano_node_dwarf_utxo-keys:/utxo-keys:ro -v $W:/work \
  -e CARDANO_NODE_SOCKET_PATH=/state/node.socket --network container:relay1 --entrypoint cardano-cli "$IMG" "$@"; }
TXIN=$(cli conway query utxo --address "$ADDR" --testnet-magic $M --output-json | python3 -c "import json,sys;print(next(iter(json.load(sys.stdin))))")
echo "input=$TXIN"
# policy for multi-asset mint
VKH=$(cli address key-hash --payment-verification-key-file /utxo-keys/payment.1.vkey)
printf "{\"type\":\"sig\",\"keyHash\":\"%s\"}" "$VKH" > $W/policy.json
POLID=$(cli transaction policyid --script-file /work/policy.json)
echo "policyid=$POLID"
sign(){ cli conway transaction sign --tx-body-file /work/$1.body --signing-key-file /utxo-keys/payment.1.skey --testnet-magic $M --out-file /work/$1.signed; }
# 1) multi-asset mint
cli conway transaction build-raw --tx-in "$TXIN" --tx-out "$ADDR+5000000+5 $POLID.4459" --mint "5 $POLID.4459" --mint-script-file /work/policy.json --fee 200000 --out-file /work/d_mint.body && sign d_mint
# 2) rich nested metadata (aux data)
printf "{\"674\":{\"a\":[1,2,3,{\"b\":\"%0.s x\" }],\"deep\":{\"x\":{\"y\":{\"z\":[true,false,null,-9,18446744073709551615]}}},\"msg\":[\"diverse decoder corpus seed\"]}}" > $W/meta.json
cli conway transaction build-raw --tx-in "$TXIN" --tx-out "$ADDR+3000000" --metadata-json-file /work/meta.json --fee 200000 --out-file /work/d_meta.body && sign d_meta
# 3) multi-output (5 outputs)
cli conway transaction build-raw --tx-in "$TXIN" --tx-out "$ADDR+1000000" --tx-out "$ADDR+2000000" --tx-out "$ADDR+3000000" --tx-out "$ADDR+4000000" --tx-out "$ADDR+5000000" --fee 200000 --out-file /work/d_multiout.body && sign d_multiout
# 4) stake-registration cert
cli conway stake-address registration-certificate --stake-verification-key-file /utxo-keys/stake.1.vkey --key-reg-deposit-amt 2000000 --out-file /work/stake.cert
cli conway transaction build-raw --tx-in "$TXIN" --tx-out "$ADDR+1000000" --certificate-file /work/stake.cert --fee 200000 --out-file /work/d_stakereg.body && cli conway transaction sign --tx-body-file /work/d_stakereg.body --signing-key-file /utxo-keys/payment.1.skey --signing-key-file /utxo-keys/stake.1.skey --testnet-magic $M --out-file /work/d_stakereg.signed
# 5) vote-delegation cert (always-abstain)
cli conway stake-address vote-delegation-certificate --stake-verification-key-file /utxo-keys/stake.1.vkey --always-abstain --out-file /work/vote.cert
cli conway transaction build-raw --tx-in "$TXIN" --tx-out "$ADDR+1000000" --certificate-file /work/vote.cert --fee 200000 --out-file /work/d_votedeleg.body && cli conway transaction sign --tx-body-file /work/d_votedeleg.body --signing-key-file /utxo-keys/payment.1.skey --signing-key-file /utxo-keys/stake.1.skey --testnet-magic $M --out-file /work/d_votedeleg.signed
# 6) edge: many tiny outputs (min-utxo-ish) + big int
cli conway transaction build-raw --tx-in "$TXIN" --tx-out "$ADDR+1000000" --tx-out "$ADDR+1000001" --tx-out "$ADDR+999999999999" --fee 9223372036854775807 --out-file /work/d_edge.body && sign d_edge
echo "=== built ==="; ls -1 $W/*.signed 2>/dev/null
