#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PACKAGE_ROOT=$(cd "${SCRIPT_DIR}/../.." && pwd)
COMPOSE_FILE="${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"

if [[ -f "${PACKAGE_ROOT}/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${PACKAGE_ROOT}/.env"
  set +a
fi

DWARF_IMAGE=${DWARF_IMAGE:-dwarf/framework:june-20260604-m2}
DWARF_CONTAINER_NAME=${DWARF_CONTAINER_NAME:-dwarf-fw-june-m2}
DWARF_DASHBOARD_BIND=${DWARF_DASHBOARD_BIND:-0.0.0.0}
DWARF_DASHBOARD_PORT=${DWARF_DASHBOARD_PORT:-8787}
DWARF_RUNTIME_ROOT=${DWARF_RUNTIME_ROOT:-${PACKAGE_ROOT}/var}
ADA2_DWARF_TOKEN=${ADA2_DWARF_TOKEN:-dwarf}

export DWARF_IMAGE
export DWARF_CONTAINER_NAME
export DWARF_DASHBOARD_BIND
export DWARF_DASHBOARD_PORT
export DWARF_RUNTIME_ROOT
export ADA2_DWARF_TOKEN

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

require_docker() {
  require_cmd docker
  docker version >/dev/null
  docker compose version >/dev/null
}

ensure_package_layout() {
  local required=(
    "${PACKAGE_ROOT}/dwarf/cardano-profile"
    "${PACKAGE_ROOT}/dwarf/profile_manager"
    "${PACKAGE_ROOT}/infrastructure/docker/dwarf-fw.Dockerfile"
    "${PACKAGE_ROOT}/infrastructure/docker/dwarf-fw-entrypoint.sh"
    "${PACKAGE_ROOT}/infrastructure/docker/requirements-framework.txt"
    "${COMPOSE_FILE}"
  )

  for path in "${required[@]}"; do
    test -e "$path" || {
      echo "missing package component: $path" >&2
      exit 1
    }
  done
}

ensure_runtime_dirs() {
  mkdir -p "${DWARF_RUNTIME_ROOT}/runs" "${DWARF_RUNTIME_ROOT}/state" "${DWARF_RUNTIME_ROOT}/bundles"
}

seed_example_runs() {
  local source_runs="${PACKAGE_ROOT}/dwarf/runs"
  local target_runs="${DWARF_RUNTIME_ROOT}/runs"

  [[ -d "${source_runs}" ]] || return 0
  mkdir -p "${target_runs}"

  local run_dir run_id
  for run_dir in "${source_runs}"/*; do
    [[ -d "${run_dir}" ]] || continue
    run_id=$(basename "${run_dir}")
    if [[ ! -e "${target_runs}/${run_id}" ]]; then
      cp -R "${run_dir}" "${target_runs}/${run_id}"
    fi
  done
}

seed_example_bundles() {
  local source_bundles="${PACKAGE_ROOT}/dwarf/bundles"
  local target_bundles="${DWARF_RUNTIME_ROOT}/bundles"

  [[ -d "${source_bundles}" ]] || return 0
  mkdir -p "${target_bundles}"

  local bundle_path bundle_name
  for bundle_path in "${source_bundles}"/*.tar.gz; do
    [[ -f "${bundle_path}" ]] || continue
    bundle_name=$(basename "${bundle_path}")
    if [[ ! -e "${target_bundles}/${bundle_name}" ]]; then
      cp "${bundle_path}" "${target_bundles}/${bundle_name}"
    fi
  done
}

compose() {
  docker compose -f "${COMPOSE_FILE}" "$@"
}

container_running() {
  docker inspect -f '{{.State.Running}}' "${DWARF_CONTAINER_NAME}" 2>/dev/null | grep -qx true
}

print_delivery_config() {
  cat <<EOF
Package root: ${PACKAGE_ROOT}
Compose file: ${COMPOSE_FILE}
Image: ${DWARF_IMAGE}
Container: ${DWARF_CONTAINER_NAME}
Dashboard: ${DWARF_DASHBOARD_BIND}:${DWARF_DASHBOARD_PORT}
Runtime root: ${DWARF_RUNTIME_ROOT}
EOF
}
