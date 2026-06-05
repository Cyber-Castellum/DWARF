#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

purge=false
remove_image=false

for arg in "$@"; do
  case "$arg" in
    --purge)
      purge=true
      ;;
    --remove-image)
      remove_image=true
      ;;
    --help|-h)
      cat <<EOF
Usage: delivery/scripts/uninstall.sh [--purge] [--remove-image]

Stops the Dwarf delivery stack. By default, runtime data and Docker images are preserved.

Options:
  --purge         Remove package-local runtime data under var/
  --remove-image  Remove ${DWARF_IMAGE}
EOF
      exit 0
      ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

ensure_package_layout
require_docker

compose down --remove-orphans

if [[ "$purge" == true ]]; then
  echo "Removing runtime data: ${DWARF_RUNTIME_ROOT}"
  rm -rf "${DWARF_RUNTIME_ROOT}"
else
  echo "Preserving runtime data: ${DWARF_RUNTIME_ROOT}"
fi

if [[ "$remove_image" == true ]]; then
  echo "Removing image: ${DWARF_IMAGE}"
  docker image rm "${DWARF_IMAGE}" || true
else
  echo "Preserving image: ${DWARF_IMAGE}"
fi

