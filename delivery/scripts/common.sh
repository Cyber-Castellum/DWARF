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

DWARF_IMAGE=${DWARF_IMAGE:-dwarf/framework:current}
DWARF_CONTAINER_NAME=${DWARF_CONTAINER_NAME:-dwarf-fw}
DWARF_DASHBOARD_BIND=${DWARF_DASHBOARD_BIND:-0.0.0.0}
DWARF_DASHBOARD_PORT=${DWARF_DASHBOARD_PORT:-8787}
DWARF_RUNTIME_ROOT=${DWARF_RUNTIME_ROOT:-${PACKAGE_ROOT}/var}
ADA2_DWARF_TOKEN=${ADA2_DWARF_TOKEN:-dwarf}
DWARF_MOOG_BOOTSTRAP=${DWARF_MOOG_BOOTSTRAP:-off}
DWARF_MOOG_BOOTSTRAP_APPROVE=${DWARF_MOOG_BOOTSTRAP_APPROVE:-0}
# SSH key / known_hosts mounted read-only into the container for optional
# substrate fan-out. Default to placeholder files under the runtime root
# (created by ensure_runtime_dirs) so a fresh install never bind-mounts a
# missing host path. Override to real files only when SSH fan-out is needed.
DWARF_SSH_KEY_PATH=${DWARF_SSH_KEY_PATH:-${DWARF_RUNTIME_ROOT}/state/ssh_deploy_key}
DWARF_SSH_KNOWN_HOSTS=${DWARF_SSH_KNOWN_HOSTS:-${DWARF_RUNTIME_ROOT}/state/ssh_known_hosts}

export DWARF_IMAGE
export DWARF_CONTAINER_NAME
export DWARF_DASHBOARD_BIND
export DWARF_DASHBOARD_PORT
export DWARF_RUNTIME_ROOT
export ADA2_DWARF_TOKEN
export DWARF_MOOG_BOOTSTRAP
export DWARF_MOOG_BOOTSTRAP_APPROVE
export DWARF_SSH_KEY_PATH
export DWARF_SSH_KNOWN_HOSTS

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
  # The framework container runs as uid 1000 and writes run bundles + state into
  # these bind-mounted dirs. Make them writable regardless of which host uid owns
  # them (a fresh clone's dirs, or dirs Docker auto-created as root). Best-effort:
  # ignore if the current user may not chmod pre-existing dirs.
  chmod 0777 "${DWARF_RUNTIME_ROOT}/runs" "${DWARF_RUNTIME_ROOT}/state" "${DWARF_RUNTIME_ROOT}/bundles" 2>/dev/null || true
  # Placeholder SSH files so the read-only key/known_hosts mounts always have an
  # existing source on a fresh install (empty = no fan-out configured, harmless).
  [[ -e "${DWARF_SSH_KNOWN_HOSTS}" ]] || : > "${DWARF_SSH_KNOWN_HOSTS}"
  if [[ ! -e "${DWARF_SSH_KEY_PATH}" ]]; then
    : > "${DWARF_SSH_KEY_PATH}"
    chmod 600 "${DWARF_SSH_KEY_PATH}"
  fi
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

seed_scenarios() {
  # Seed the writable scenario catalog (runtime state) from the baked read-only
  # source. The container mounts this dir at ADA2_DWARF_SCENARIOS_DIR so the
  # dashboard can create/edit scenarios (the image tree is read-only). Only
  # copies files that don't already exist, so user edits are preserved.
  local source_scenarios="${PACKAGE_ROOT}/dwarf/scenarios"
  local target_scenarios="${DWARF_RUNTIME_ROOT}/state/scenarios"

  [[ -d "${source_scenarios}" ]] || return 0
  mkdir -p "${target_scenarios}"

  local scn_path scn_name
  for scn_path in "${source_scenarios}"/*.yaml; do
    [[ -f "${scn_path}" ]] || continue
    scn_name=$(basename "${scn_path}")
    if [[ ! -e "${target_scenarios}/${scn_name}" ]]; then
      cp "${scn_path}" "${target_scenarios}/${scn_name}"
    fi
  done
}

seed_manifests() {
  # Seed the writable target-manifest catalog from the baked source so targets
  # can be registered from the dashboard (ADA2_DWARF_MANIFESTS_DIR).
  local source_manifests="${PACKAGE_ROOT}/dwarf/targets/manifests"
  local target_manifests="${DWARF_RUNTIME_ROOT}/state/targets/manifests"

  [[ -d "${source_manifests}" ]] || return 0
  mkdir -p "${target_manifests}"

  local m_path m_name
  for m_path in "${source_manifests}"/*.yaml; do
    [[ -f "${m_path}" ]] || continue
    m_name=$(basename "${m_path}")
    if [[ ! -e "${target_manifests}/${m_name}" ]]; then
      cp "${m_path}" "${target_manifests}/${m_name}"
    fi
  done
}

seed_profiles() {
  # Seed the writable profile catalog (<id>/profile.yaml dirs) from the baked
  # source so profiles can be created/deployed from the dashboard
  # (ADA2_DWARF_PROFILES_DIR).
  local source_profiles="${PACKAGE_ROOT}/dwarf/profiles"
  local target_profiles="${DWARF_RUNTIME_ROOT}/state/profiles"

  [[ -d "${source_profiles}" ]] || return 0
  mkdir -p "${target_profiles}"

  local p_dir p_id
  for p_dir in "${source_profiles}"/*/; do
    [[ -f "${p_dir}profile.yaml" ]] || continue
    p_id=$(basename "${p_dir}")
    if [[ ! -e "${target_profiles}/${p_id}/profile.yaml" ]]; then
      mkdir -p "${target_profiles}/${p_id}"
      cp "${p_dir}profile.yaml" "${target_profiles}/${p_id}/profile.yaml"
    fi
  done
}

compose() {
  docker compose -f "${COMPOSE_FILE}" "$@"
}

container_running() {
  docker inspect -f '{{.State.Running}}' "${DWARF_CONTAINER_NAME}" 2>/dev/null | grep -qx true
}

optional_moog_bootstrap() {
  case "${DWARF_MOOG_BOOTSTRAP}" in
    off|0|false|no|"")
      return 0
      ;;
    plan)
      echo "Moog bootstrap plan requested (no remote state change)"
      docker exec -i "${DWARF_CONTAINER_NAME}" /home/dwarf/dwarf-fw/dwarf/cardano-profile moog bootstrap --json
      ;;
    approve)
      if [[ "${DWARF_MOOG_BOOTSTRAP_APPROVE}" != "1" ]]; then
        echo "DWARF_MOOG_BOOTSTRAP=approve requires DWARF_MOOG_BOOTSTRAP_APPROVE=1" >&2
        exit 1
      fi
      echo "Moog bootstrap approve requested"
      docker exec -i "${DWARF_CONTAINER_NAME}" /home/dwarf/dwarf-fw/dwarf/cardano-profile moog bootstrap --approve --json
      docker exec -i "${DWARF_CONTAINER_NAME}" /home/dwarf/dwarf-fw/dwarf/cardano-profile moog healthcheck --json
      ;;
    *)
      echo "invalid DWARF_MOOG_BOOTSTRAP value: ${DWARF_MOOG_BOOTSTRAP} (use off, plan, or approve)" >&2
      exit 1
      ;;
  esac
}

print_delivery_config() {
  cat <<EOF
Package root: ${PACKAGE_ROOT}
Compose file: ${COMPOSE_FILE}
Image: ${DWARF_IMAGE}
Container: ${DWARF_CONTAINER_NAME}
Dashboard: ${DWARF_DASHBOARD_BIND}:${DWARF_DASHBOARD_PORT}
Runtime root: ${DWARF_RUNTIME_ROOT}
Moog bootstrap: ${DWARF_MOOG_BOOTSTRAP}
EOF
}
