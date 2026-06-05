#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PACKAGE_ROOT=$(cd "${SCRIPT_DIR}/../.." && pwd)

required_files=(
  "README.md"
  "INSTALL.md"
  "OPERATIONS.md"
  "RELEASE-NOTES.md"
  "TEST-OUTPUTS.md"
  "docs/index.html"
  "delivery/docker-compose.dwarf.yml"
  "delivery/scripts/common.sh"
  "delivery/scripts/install.sh"
  "delivery/scripts/build-image.sh"
  "delivery/scripts/deploy.sh"
  "delivery/scripts/status.sh"
  "delivery/scripts/undeploy.sh"
  "delivery/scripts/uninstall.sh"
  "infrastructure/docker/dwarf-fw.Dockerfile"
  "infrastructure/docker/dwarf-fw-entrypoint.sh"
  "dwarf/cardano-profile"
  "dwarf/runs/20260419T020533Z-aa19a2d4/manifest.json"
  "dwarf/bundles/20260419T020533Z-aa19a2d4.tar.gz"
)

forbidden_paths=(
  "agent"
  "dist"
  "docs/plans"
  "dwarf/fuzz-tests"
  "dwarf/tests"
  "dwarf/docs/fuzz-framework-pattern-mapping.md"
  "dwarf/docs/production-target.md"
  "dwarf/docs/web-ui.md"
  "dwarf/corpora/m3"
  "dwarf/corpora/plutus-phase2"
  "dwarf/corpora/crash-triage-example"
  "dwarf/corpora/differential-rule-harness-example"
  "OPERATE-CHEATSHEET.md"
  "dwarf-client-showcase.html"
  "dwarf-operate-cheatsheet.html"
  "dwarf/scripts/README.md"
  "dwarf/scripts/deploy-dashboard.sh"
  "dwarf-uptodatemay"
  "dwarf-v55"
  "infrastructure/cardano-box"
  "infrastructure/docker/amaru-0.1.2.Dockerfile"
  "infrastructure/docker/cardano-node-10.7.1.Dockerfile"
  "infrastructure/docker/docker-compose.yml"
  "infrastructure/docker/IMAGE-SIGNING.md"
  "infrastructure/docker/REPRODUCIBLE-BUILD.md"
)

required_executables=(
  "delivery/scripts/install.sh"
  "delivery/scripts/build-image.sh"
  "delivery/scripts/deploy.sh"
  "delivery/scripts/status.sh"
  "delivery/scripts/undeploy.sh"
  "delivery/scripts/uninstall.sh"
)

for path in "${required_files[@]}"; do
  test -e "${PACKAGE_ROOT}/${path}" || {
    echo "missing required file: ${path}" >&2
    exit 1
  }
done

for path in "${forbidden_paths[@]}"; do
  test ! -e "${PACKAGE_ROOT}/${path}" || {
    echo "non-delivery path should not be packaged: ${path}" >&2
    exit 1
  }
done

for path in "${required_executables[@]}"; do
  test -x "${PACKAGE_ROOT}/${path}" || {
    echo "not executable: ${path}" >&2
    exit 1
  }
done

for script in "${required_executables[@]}" "delivery/scripts/common.sh"; do
  bash -n "${PACKAGE_ROOT}/${script}"
done

grep -q "dwarf/framework:june-20260604-m2" "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q "dwarf-fw-june-m2" "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml"
grep -q 'seed_example_runs' "${PACKAGE_ROOT}/delivery/scripts/deploy.sh"
grep -q 'seed_example_runs' "${PACKAGE_ROOT}/delivery/scripts/install.sh"
grep -q 'seed_example_bundles' "${PACKAGE_ROOT}/delivery/scripts/deploy.sh"
grep -q 'seed_example_bundles' "${PACKAGE_ROOT}/delivery/scripts/install.sh"
grep -q -- '--force-recreate' "${PACKAGE_ROOT}/delivery/scripts/deploy.sh"
grep -q '/api/status' "${PACKAGE_ROOT}/delivery/scripts/deploy.sh"
grep -q 'docker exec -i' "${PACKAGE_ROOT}/delivery/scripts/deploy.sh"
grep -q 'Acquire::Check-Valid-Until "false";' "${PACKAGE_ROOT}/infrastructure/docker/dwarf-fw.Dockerfile"
grep -q 'Acquire::Check-Date "false";' "${PACKAGE_ROOT}/infrastructure/docker/dwarf-fw.Dockerfile"
if grep -q '\\\"false\\\"' "${PACKAGE_ROOT}/infrastructure/docker/dwarf-fw.Dockerfile"; then
  echo "Dockerfile apt config contains escaped quotes that apt will not parse as intended" >&2
  exit 1
fi
if grep -q 'infrastructure/cardano-box' "${PACKAGE_ROOT}/infrastructure/docker/dwarf-fw.Dockerfile"; then
  echo "Dockerfile depends on cardano-box helper files that are not part of the delivery package" >&2
  exit 1
fi
if grep -Eq 'dwarf-v55|dwarf-uptodatemay|Selected audit/research evidence|May continuation notes|Source-of-truth and requirements docs|dist/' "${PACKAGE_ROOT}/README.md"; then
  echo "README still describes non-delivery carryover directories" >&2
  exit 1
fi

manifest_count=$(
  find "${PACKAGE_ROOT}/dwarf/targets/manifests" -type f -name '*.yaml' | wc -l | tr -d '[:space:]'
)
if [[ "${manifest_count}" != "29" ]]; then
  echo "expected 29 M2 target manifests, found ${manifest_count}" >&2
  exit 1
fi

if find "${PACKAGE_ROOT}/dwarf/targets/manifests" -type f -name '*.yaml' \
  ! \( -name '*-cbor-decode-*.yaml' -o -name '*-mini-protocol-decode-*.yaml' \) \
  | grep -q .; then
  echo "target manifest catalog contains non-M2 manifests" >&2
  exit 1
fi

if find "${PACKAGE_ROOT}/dwarf/targets" -mindepth 1 -maxdepth 1 -type d \
  ! \( -name 'amaru' -o -name 'cardano-node' -o -name 'manifests' \) \
  | grep -q .; then
  echo "target source tree contains non-M2 top-level harness directories" >&2
  exit 1
fi

if find "${PACKAGE_ROOT}/dwarf/corpora" -mindepth 1 -maxdepth 1 -type d \
  \( -name 'amaru-cardano-differential-cargo-fuzz-*' -o -name 'amaru-cargo-fuzz-ledger-*' \) \
  | grep -q .; then
  echo "corpora contains non-M2 differential or ledger campaign seeds" >&2
  exit 1
fi

if find "${PACKAGE_ROOT}/dwarf/grammars" -mindepth 1 -maxdepth 1 -type d \
  \( -name 'amaru-cardano-differential-cargo-fuzz-*' -o -name 'amaru-cargo-fuzz-ledger-*' \) \
  | grep -q .; then
  echo "grammars contains non-M2 differential or ledger campaign dictionaries" >&2
  exit 1
fi

if grep -R -E '/Users/nigel|/home/nigel|cardano-box|192\.168\.30\.16' "${PACKAGE_ROOT}/dwarf/targets/manifests" "${PACKAGE_ROOT}/dwarf/targets/README.md" "${PACKAGE_ROOT}/dwarf/grammars/README.md"; then
  echo "public target or grammar catalog contains local host paths" >&2
  exit 1
fi

if command -v docker >/dev/null 2>&1; then
  docker compose -f "${PACKAGE_ROOT}/delivery/docker-compose.dwarf.yml" config >/dev/null
fi

echo "delivery contract ok"
