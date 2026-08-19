#!/usr/bin/env bash
# DWARF devnet-smoke CI runner (self-hosted runner only).
#
# Runs a curated subset of self-provisioning cardano-node devnet scenarios through
# the composer, then GUARANTEES teardown of anything THIS run created — without ever
# touching containers it did not start (e.g. long-running client devnets on the host).
#
# Isolation model:
#   - Each `scenario run` composes a project named dwarf-substrate-<run-id>.
#   - Before the run we snapshot existing dwarf-substrate-* projects; after (always)
#     we tear down only the projects that appeared during this run.
#
# Exit non-zero if any scenario fails its assertions.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

RUNS_DIR="${DWARF_CI_RUNS_DIR:-$REPO_ROOT/ci-devnet-runs}"
mkdir -p "$RUNS_DIR"

# Curated smoke subset: 2-node cardano-node, testnet_42 (fast fake epochs),
# all-cardano assertions (no amaru serve-only observation gaps). Keep it short.
SCENARIOS=(
  "dwarf/scenarios/runtime-substrate-honest-baseline-docker-mode-example-smoke.yaml"
  "dwarf/scenarios/runtime-substrate-cardano-node-mixed-minor-phase1-smoke.yaml"
)
[ "$#" -gt 0 ] && SCENARIOS=("$@")

projects_now() { docker ps -a --format '{{.Names}}' 2>/dev/null \
  | grep -oE 'dwarf-substrate-[a-z0-9]+' | sort -u; }

BEFORE="$(projects_now)"

cleanup() {
  echo "== cleanup: tearing down only this run's substrate projects =="
  local after new proj
  after="$(projects_now)"
  new="$(comm -13 <(echo "$BEFORE") <(echo "$after"))"
  for proj in $new; do
    echo "  removing $proj"
    docker ps -a --format '{{.Names}}' | grep -E "^${proj}(-|$)" \
      | xargs -r docker rm -f >/dev/null 2>&1
    docker network ls --format '{{.Name}}' | grep -E "^${proj}-net$" \
      | xargs -r docker network rm >/dev/null 2>&1
  done
  # never touch anything not in $new (client devnets stay up)
}
trap cleanup EXIT

fail=0
for scn in "${SCENARIOS[@]}"; do
  echo "==================================================================="
  echo "== scenario: $scn"
  echo "==================================================================="
  if timeout 900 python3 dwarf/cardano-profile scenario run "$scn" \
        --backend local-devnet --runs-dir "$RUNS_DIR"; then
    echo "== PASS: $scn"
  else
    echo "== FAIL: $scn (exit $?)"
    fail=1
  fi
done

echo "==================================================================="
[ "$fail" -eq 0 ] && echo "devnet-smoke: all scenarios passed" \
                  || echo "devnet-smoke: at least one scenario FAILED"
exit "$fail"
