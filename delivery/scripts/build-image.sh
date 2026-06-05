#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

ensure_package_layout
require_docker

echo "Building ${DWARF_IMAGE} from ${PACKAGE_ROOT}"
docker build \
  -f "${PACKAGE_ROOT}/infrastructure/docker/dwarf-fw.Dockerfile" \
  -t "${DWARF_IMAGE}" \
  "${PACKAGE_ROOT}"

docker image inspect "${DWARF_IMAGE}" >/dev/null
echo "Built image: ${DWARF_IMAGE}"

