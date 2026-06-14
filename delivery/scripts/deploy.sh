#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

ensure_package_layout
ensure_runtime_dirs
seed_example_runs
seed_example_bundles
require_docker

docker image inspect "${DWARF_IMAGE}" >/dev/null || {
  echo "image not found: ${DWARF_IMAGE}" >&2
  echo "run delivery/scripts/build-image.sh first" >&2
  exit 1
}

echo "Deploying ${DWARF_CONTAINER_NAME}"
compose up -d --no-build --force-recreate dwarf-fw

echo "Waiting for dashboard readiness"
ready=false
for _ in $(seq 1 30); do
  if docker exec -i "${DWARF_CONTAINER_NAME}" python3 - <<'PY' >/dev/null 2>&1
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8787/api/status", timeout=20) as response:
    raise SystemExit(0 if response.status == 200 else 1)
PY
  then
    ready=true
    break
  fi
  sleep 1
done

if [[ "${ready}" != true ]]; then
  echo "dashboard did not become ready: http://${DWARF_DASHBOARD_BIND}:${DWARF_DASHBOARD_PORT}/api/status" >&2
  docker logs "${DWARF_CONTAINER_NAME}" --tail 80 >&2 || true
  exit 1
fi

optional_moog_bootstrap
compose ps
