#!/usr/bin/env bash
# SP2 round-trip: generate the header-path native bundle, run the Stage-2 gate,
# lint with docker compose, and confirm moog accepts the bundle as a test asset.
# Run on cardano-box (Docker + moog present).
set -uo pipefail
cd "$(dirname "$0")/.."
SCEN=dwarf/scenarios/cardano-node-cbor-block-header-fuzz-structured.yaml
OUT=/tmp/sp2-bundle
REG=reg.example/x

rm -rf "$OUT"
PYTHONPATH=dwarf python3 dwarf/cardano-profile scenario run "$SCEN" \
  --backend antithesis --out "$OUT" --registry "$REG" || { echo "FAIL generate/verify"; exit 1; }

# docker compose static lint (no pull, no run). INTERNAL_NETWORK is supplied by
# the Antithesis launcher at runtime (CF testnet convention); set it here so the
# bare `config` lint can interpolate networks.default.internal.
INTERNAL_NETWORK=true docker compose -f "$OUT/config/docker-compose.yaml" config >/dev/null \
  && echo "OK docker compose config" || { echo "FAIL docker compose config"; exit 1; }

# moog accepts the bundle dir as a test asset (read-only validation; no submission)
PYTHONPATH=dwarf python3 dwarf/cardano-profile moog asset validate --asset-dir "$OUT" --json \
  && echo "OK moog asset validate" || echo "WARN moog asset validate (check layout)"
echo "sp2 round-trip done"
