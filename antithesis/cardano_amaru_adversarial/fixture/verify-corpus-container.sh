#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CARDANO_CONFIGURATOR_IMAGE='ghcr.io/cardano-foundation/cardano-node-antithesis/configurator@sha256:599a73a401e42b0922751458e92eb5ae432b5e3712153f8843def8ee15d79e4f'

exec docker run --rm \
  --entrypoint python3 \
  --volume "$SCRIPT_DIR:/fixture:ro" \
  "$CARDANO_CONFIGURATOR_IMAGE" \
  /fixture/verify_corpus.py /fixture/static
