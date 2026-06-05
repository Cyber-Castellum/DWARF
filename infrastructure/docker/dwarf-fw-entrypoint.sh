#!/usr/bin/env bash
set -euo pipefail

cd /home/dwarf/dwarf-fw

if [[ $# -eq 0 ]]; then
  exec python3 dwarf/cardano-profile dashboard serve --bind 0.0.0.0 --port 8787 --token "${ADA2_DWARF_TOKEN:-dwarf}"
fi

exec python3 dwarf/cardano-profile "$@"
