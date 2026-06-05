#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

ensure_package_layout
require_docker

echo "Stopping/removing ${DWARF_CONTAINER_NAME}; preserving ${DWARF_RUNTIME_ROOT}"
compose down --remove-orphans

