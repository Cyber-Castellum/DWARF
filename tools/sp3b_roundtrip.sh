#!/usr/bin/env bash
# SP3b round-trip: generate the 3 tx bundles (tx-body / certificate /
# auxiliary-data, new tx-submission path) plus the block + header bundles
# (regression), running the Stage-2 gate + docker compose lint on each.
# Confirms the tx scenarios now generate end-to-end and the block/header paths
# stay green. Run on cardano-box. INTERNAL_NETWORK is supplied by the Antithesis
# launcher at runtime (set here for the bare `config` lint).
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
for SCEN in dwarf/scenarios/cardano-node-cbor-tx-body-fuzz-structured.yaml \
            dwarf/scenarios/cardano-node-cbor-certificate-fuzz-structured.yaml \
            dwarf/scenarios/cardano-node-cbor-auxiliary-data-fuzz-structured.yaml \
            dwarf/scenarios/cardano-node-cbor-block-fuzz-structured.yaml \
            dwarf/scenarios/cardano-node-cbor-block-header-fuzz-structured.yaml; do
  OUT=/tmp/sp3b-$(basename "$SCEN" .yaml)
  rm -rf "$OUT"
  if ! PYTHONPATH=dwarf python3 dwarf/cardano-profile scenario run "$SCEN" \
        --backend antithesis --out "$OUT" --registry ghcr.io/j-gainsec --tag 0.5.1 >/tmp/sp3b-gen.out 2>&1; then
    echo "FAIL generate/verify $(basename "$SCEN")"; cat /tmp/sp3b-gen.out; fail=1; continue
  fi
  if INTERNAL_NETWORK=true docker compose -f "$OUT/config/docker-compose.yaml" config >/dev/null 2>&1; then
    echo "OK $(basename "$SCEN")"
  else
    echo "FAIL compose config $(basename "$SCEN")"; fail=1
  fi
done
[ "$fail" -eq 0 ] && echo "sp3b round-trip done" || { echo "sp3b round-trip FAILED"; exit 1; }
