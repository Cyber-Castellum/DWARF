#!/usr/bin/env bash
# Forge the false-leadership attack chain on the cardano_amaru devnet (testnet_42:
# k=5, f=0.2, epoch 125). Uses the devnet's real pool credentials + genesis, copied
# out of the running p1 producer's config volume.
#
#   config.forge.json = the devnet config.json with DijkstraGenesisFile REMOVED
#     (db-synthesizer 0.25 has no Dijkstra era; cardano-node 10.7.1 requires the key
#      — keep a separate config.json WITH it for the node).
set -eu
export PATH="$HOME/.ghcup/bin:$PATH"
X=/tmp/occ/ouroboros-consensus-cardano-0.25.1.0/dist-newstyle/build/x86_64-linux/ghc-9.6.7/ouroboros-consensus-cardano-0.25.1.0/x
BIN=$X/db-synthesizer/build/db-synthesizer/db-synthesizer
TRUNC=$X/db-truncater/build/db-truncater/db-truncater
W=/tmp/fl2; K=$W/keys
CFG=$W/configs/config.forge.json

run(){ # $1=db  $2=env-prefix  $3=slots-to-forge
  eval "$2 $BIN --config $CFG --db $1 \
    --shelley-operational-certificate $K/opcert.cert \
    --shelley-vrf-key $K/vrf.skey --shelley-kes-key $K/kes.skey \
    -s $3 -f 2>&1" | grep -iE 'forged and adopted|Error'
}

# 0) proof the forgery is real: honest vs patched over the same window
echo '== honest (f=0.2) =='          ; run $W/db_honest ""                        400
echo '== false-leadership (f~0.99) =='; run $W/db_false  "DWARF_FALSE_LEADERSHIP=1" 400

# 1) honest chain to the Amaru bundle chain tip (epoch >= 3 required by bootstrap-producer)
run $W/db_honest2 "" 800     # tip lands at block 50, slot 646 (epoch 5)

# 2) attack chain = honest[0..646] + false-leadership[647..] kept WITHIN epoch 5
#    (truncater only touches immutable, so clear volatile/ledger/gsm afterwards,
#     else the synthesizer reads the stale volatile tip). -s counts slots forged.
rm -rf $W/db_attack3; cp -r $W/db_honest2 $W/db_attack3
$TRUNC --db $W/db_attack3 --truncate-after-slot 646 cardano --config $CFG
rm -rf $W/db_attack3/volatile/* $W/db_attack3/ledger/* $W/db_attack3/gsm/*
DWARF_FALSE_LEADERSHIP=1 $BIN --config $CFG --db $W/db_attack3 \
  --shelley-operational-certificate $K/opcert.cert \
  --shelley-vrf-key $K/vrf.skey --shelley-kes-key $K/kes.skey -s 749 -a
