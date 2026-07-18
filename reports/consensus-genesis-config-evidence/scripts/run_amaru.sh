#!/usr/bin/env bash
# Feed each mutated genesis through Amaru's derivation path (bootstrap-producer) and classify.
# NOTE: bootstrap-producer intermittently HANGS on its internal header-extractor regardless of
# input (harness fragility) -> use a hard timeout and treat rc=124 as HANG. Results are not yet
# reliable enough for a confirmed Amaru finding; a deterministic Amaru genesis-parse surface is needed.
set -eu
W=/tmp/cfgdiff
AIMG=ghcr.io/lambdasistemi/amaru-bootstrap-producer:03d2727b71e8d1fe7c793d5036dce3c3ce294f6c
for name in "$@"; do
  cp "$W/muts/$name.json" "$W/configs/shelley-genesis.json"
  rm -rf "$W/state2"; cp -r /tmp/fl2/db_honest2 "$W/state2"; printf 42 > "$W/state2/protocolMagicId"; touch "$W/state2/lock"
  sudo rm -rf "$W/bundle"; mkdir -p "$W/bundle"; docker rm -f cfg-boot >/dev/null 2>&1 || true
  out=$(timeout 180 docker run --name cfg-boot --rm \
    -v "$W/state2":/cardano/state -v "$W/configs":/cardano/config/configs:ro -v "$W/bundle":/srv/amaru \
    --entrypoint /bin/bootstrap-producer "$AIMG" /cardano/state /cardano/config/configs /srv/amaru testnet_42 2>&1); rc=$?
  echo "$name rc=$rc $(echo "$out" | grep -aoiE 'derived AMARU_GLOBAL_.*epoch_length=[0-9-]+|panic|error[^,]{0,40}' | head -1)"
done
