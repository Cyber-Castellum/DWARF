#!/usr/bin/env bash
# SP3a round-trip: generate the block bundle (new block-fetch path) AND the
# header bundle (SP2 regression), run the Stage-2 gate + docker compose lint on
# each. Confirms the block scenario now generates end-to-end and the header
# path stays green. Run on build-host. INTERNAL_NETWORK is supplied by the
# Antithesis launcher at runtime (set here for the bare `config` lint).
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
for SCEN in dwarf/scenarios/cardano-node-cbor-block-fuzz-structured.yaml \
            dwarf/scenarios/cardano-node-cbor-block-header-fuzz-structured.yaml; do
  OUT=/tmp/sp3a-$(basename "$SCEN" .yaml)
  rm -rf "$OUT"
  if ! PYTHONPATH=dwarf python3 dwarf/cardano-profile scenario run "$SCEN" \
        --backend antithesis --out "$OUT" --registry ghcr.io/j-gainsec --tag 0.2.0 >/tmp/sp3a-gen.out 2>&1; then
    echo "FAIL generate/verify $(basename "$SCEN")"; cat /tmp/sp3a-gen.out; fail=1; continue
  fi
  if INTERNAL_NETWORK=true docker compose -f "$OUT/config/docker-compose.yaml" config >/dev/null 2>&1; then
    echo "OK $(basename "$SCEN")"
  else
    echo "FAIL compose config $(basename "$SCEN")"; fail=1
  fi
done
[ "$fail" -eq 0 ] && echo "sp3a round-trip done" || { echo "sp3a round-trip FAILED"; exit 1; }
