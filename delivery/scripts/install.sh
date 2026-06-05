#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

ensure_package_layout
ensure_runtime_dirs
seed_example_runs
seed_example_bundles

echo "Dwarf V3 June M2 delivery package installed/prepared."
print_delivery_config
