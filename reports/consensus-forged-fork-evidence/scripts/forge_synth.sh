#!/bin/bash
ssh cardano-box '
T=/tmp/occ/ouroboros-consensus-cardano-0.25.1.0/dist-newstyle/build/x86_64-linux/ghc-9.6.7/ouroboros-consensus-cardano-0.25.1.0/x
K=/tmp/planB/keys
$T/db-synthesizer/build/db-synthesizer/db-synthesizer \
  --config /tmp/planB/configs/config.json \
  --db /tmp/planB/forkdb \
  --shelley-operational-certificate $K/opcert.cert \
  --shelley-vrf-key $K/vrf.skey \
  --shelley-kes-key $K/kes.skey \
  -s 600 -a 2>&1 | tail -30
echo "SYNTH_EXIT=${PIPESTATUS[0]}"'
