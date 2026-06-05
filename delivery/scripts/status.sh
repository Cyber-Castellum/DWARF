#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

ensure_package_layout
require_docker

print_delivery_config

echo
echo "Docker image:"
if docker image inspect "${DWARF_IMAGE}" >/dev/null 2>&1; then
  docker image inspect "${DWARF_IMAGE}" --format '  id={{.Id}} size={{.Size}}'
else
  echo "  missing"
fi

echo
echo "Compose status:"
compose ps

echo
echo "Container status:"
if docker inspect "${DWARF_CONTAINER_NAME}" >/dev/null 2>&1; then
  docker inspect "${DWARF_CONTAINER_NAME}" --format '  running={{.State.Running}} status={{.State.Status}} started={{.State.StartedAt}}'
  docker port "${DWARF_CONTAINER_NAME}" 8787/tcp 2>/dev/null | sed 's/^/  port /' || true
else
  echo "  missing"
fi

echo
echo "In-container CLI smoke:"
if container_running; then
  docker exec "${DWARF_CONTAINER_NAME}" python3 dwarf/cardano-profile dashboard status
else
  echo "  skipped: container is not running"
fi

