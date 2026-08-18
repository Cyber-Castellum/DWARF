#!/usr/bin/env bash
#
# install.sh — one-command DWARF bring-up.
#
# By default this does the FULL local bring-up so the web dashboard can drive
# everything from that point on:
#   1. prepare package layout + seed the writable catalog (scenarios,
#      manifests, profiles, example runs/bundles)
#   2. build the dashboard container image
#   3. start the dashboard container and wait for readiness
#
# Two host-side capabilities are OFF by default because they touch the host
# beyond the container sandbox — enable them with flags:
#   --control-channel   provision the restricted forced-command SSH shim so the
#                       dashboard can deploy/teardown the substrate and run
#                       host-side coverage. (Without it the dashboard runs fine
#                       but the deploy/teardown/coverage buttons have no host to
#                       talk to.)
#   --afl               build the AFL coverage harness (HEAVY: needs GHC 9.6.7 +
#                       cabal; builds a ~268 MB instrumented binary).
#   --all               = --control-channel --afl
#
# Other flags:
#   --no-build          reuse the existing image (skip the docker build)
#   --no-up             prepare + build but don't start the container
#   --prepare-only      only seed the catalog (the legacy install behavior);
#                       implies --no-build --no-up
#   -h | --help         show this help
#
# Env passthrough for --control-channel (see provision-control-channel.sh):
#   DEPLOY_USER, DEPLOY_HOST, REMOTE_BASE_PATH, INSTALL_DIR ...
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

CONTROL_PLANE_DIR="${PACKAGE_ROOT}/delivery/control-plane"

DO_CONTROL_CHANNEL=0
DO_AFL=0
DO_BUILD=1
DO_UP=1
PREPARE_ONLY=0

usage() { sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --control-channel|-c) DO_CONTROL_CHANNEL=1 ;;
    --afl|--coverage)     DO_AFL=1 ;;
    --all)                DO_CONTROL_CHANNEL=1; DO_AFL=1 ;;
    --no-build)           DO_BUILD=0 ;;
    --no-up)              DO_UP=0 ;;
    --prepare-only)       PREPARE_ONLY=1; DO_BUILD=0; DO_UP=0 ;;
    -h|--help)            usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; echo "run '$0 --help' for usage" >&2; exit 2 ;;
  esac
  shift
done

# Track outcomes so the closing summary is honest about what actually happened.
CONTROL_CHANNEL_STATUS="skipped"
AFL_STATUS="skipped"

echo "==> [1/5] preparing package layout + seeding catalog"
ensure_package_layout
ensure_runtime_dirs
seed_example_runs
seed_example_bundles
seed_scenarios
seed_manifests
seed_profiles
echo "    catalog seeded under ${DWARF_RUNTIME_ROOT}/state"

if [[ "${PREPARE_ONLY}" == 1 ]]; then
  echo
  echo "Prepare-only: catalog is ready; image not built and container not started."
  print_delivery_config
  exit 0
fi

if [[ "${DO_BUILD}" == 1 ]]; then
  echo "==> [2/5] building dashboard image"
  require_docker
  bash "${SCRIPT_DIR}/build-image.sh"
else
  echo "==> [2/5] skipping image build (--no-build)"
fi

# --- optional host-side capability: control channel -------------------------
if [[ "${DO_CONTROL_CHANNEL}" == 1 ]]; then
  echo "==> [3/5] provisioning web-driven substrate control channel"
  # Pin the provision script's staging dir to the SAME runtime root the
  # container mounts (DWARF_RUNTIME_ROOT, which .env can redirect away from
  # REPO_ROOT/var). Otherwise the key lands where compose never reads it and
  # the container comes up without a working channel.
  if STATE_DIR="${DWARF_RUNTIME_ROOT}/state" sh "${CONTROL_PLANE_DIR}/provision-control-channel.sh"; then
    # The shim env must be set BEFORE 'compose up' so the container is created
    # with it. deploy.sh's 'compose up' inherits this exported value.
    export ADA2_DWARF_CONTROL_SHIM=1
    CONTROL_CHANNEL_STATUS="provisioned"
    echo "    control channel provisioned; container will run in shim mode"
  else
    CONTROL_CHANNEL_STATUS="FAILED"
    echo "!!  control-channel provisioning failed — bringing up the dashboard" >&2
    echo "!!  WITHOUT host fan-out. Re-run provision-control-channel.sh to fix." >&2
  fi
else
  echo "==> [3/5] control channel not requested (pass --control-channel to enable)"
fi

# --- optional host-side capability: AFL coverage harness --------------------
if [[ "${DO_AFL}" == 1 ]]; then
  echo "==> [4/5] building AFL coverage harness (heavy — GHC + cardano-node deps)"
  if sh "${SCRIPT_DIR}/build-afl-harness.sh"; then
    AFL_STATUS="built"
  else
    AFL_STATUS="FAILED"
    echo "!!  AFL harness build failed — aflpp coverage scenarios will SKIP" >&2
    echo "!!  cleanly until build-afl-harness.sh succeeds (needs GHC 9.6.7 + cabal)." >&2
  fi
else
  echo "==> [4/5] AFL harness not requested (pass --afl to build it)"
fi

# Preserve shim mode if the control channel was provisioned on a PRIOR run
# (a real, non-empty staged key exists) even when --control-channel wasn't
# passed this time — so re-running install.sh never silently disables an
# existing channel. ensure_runtime_dirs seeds only an EMPTY placeholder key,
# so `-s` is false on a fresh install.
if [[ "${ADA2_DWARF_CONTROL_SHIM:-}" != 1 && -s "${DWARF_SSH_KEY_PATH}" ]]; then
  export ADA2_DWARF_CONTROL_SHIM=1
  [[ "${CONTROL_CHANNEL_STATUS}" == "skipped" ]] && CONTROL_CHANNEL_STATUS="pre-existing (preserved)"
  echo "    detected previously-provisioned control channel; preserving shim mode"
fi

# --- start the dashboard ----------------------------------------------------
if [[ "${DO_UP}" == 1 ]]; then
  echo "==> [5/5] starting dashboard container"
  bash "${SCRIPT_DIR}/deploy.sh"
else
  echo "==> [5/5] not starting container (--no-up)"
fi

echo
echo "================ DWARF install summary ================"
print_delivery_config
cat <<EOF
Control channel : ${CONTROL_CHANNEL_STATUS}
AFL harness     : ${AFL_STATUS}
EOF
if [[ "${DO_UP}" == 1 ]]; then
  echo "Dashboard       : http://${DWARF_DASHBOARD_BIND}:${DWARF_DASHBOARD_PORT}/operate?token=${ADA2_DWARF_TOKEN}"
fi
cat <<'EOF'
-------------------------------------------------------
From here everything is driven from the web dashboard:
profiles, targets, primitives, scenario runs, fuzzing,
backups, scheduling, settings — and, when the control
channel is provisioned, substrate deploy/teardown and
host-side coverage runs. Two jobs stay on the host by
design (the hardened container never gets a host shell):
the forced-command shim and the AFL forkserver.
=======================================================
EOF
