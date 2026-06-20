#!/usr/bin/env bash
# Generate a single-node devnet env and bake it into the cardano-node-devnet image.
# Usage: build.sh [registry] [tag]   (registry empty => local image only)
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REGISTRY="${1:-}"
TAG="${2:-latest}"
MAGIC="${DWARF_DEVNET_MAGIC:-42}"
BASE_IMAGE="${DWARF_NODE_BASE_IMAGE:-dwarf/cardano-node:10.7.1}"
BIN_DIR="${DWARF_CARDANO_BIN_DIR:-${HOME}/.local/bin}"
IMAGE_LOCAL="cardano-node-devnet:${TAG}"

# cardano-testnet finds cardano-cli/cardano-node via these env vars (NOT PATH).
export CARDANO_CLI="${CARDANO_CLI:-${BIN_DIR}/cardano-cli}"
export CARDANO_NODE="${CARDANO_NODE:-${BIN_DIR}/cardano-node}"
export PATH="${BIN_DIR}:${PATH}"
CARDANO_TESTNET="${CARDANO_TESTNET_BIN:-${BIN_DIR}/cardano-testnet}"

stage="${SCRIPT_DIR}/env"
rm -rf "${stage}"
echo "Generating devnet env (magic ${MAGIC}) via ${CARDANO_TESTNET}"
"${CARDANO_TESTNET}" create-env \
  --output "${stage}" \
  --num-pool-nodes 1 \
  --testnet-magic "${MAGIC}" \
  --node-logging-format json

echo "Building ${IMAGE_LOCAL} (base ${BASE_IMAGE})"
docker build --platform linux/amd64 --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
  -t "${IMAGE_LOCAL}" "${SCRIPT_DIR}"

if [[ -n "${REGISTRY}" ]]; then
  ref="${REGISTRY}/cardano-node-devnet:${TAG}"
  docker tag "${IMAGE_LOCAL}" "${ref}"
  echo "Tagged ${ref} (push separately, or via image_push_commands)"
fi
echo "Built ${IMAGE_LOCAL}"
