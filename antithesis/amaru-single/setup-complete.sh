#!/usr/bin/env bash
set -euo pipefail

# Inform Antithesis it can start running test commands.
OUTPUT_PATH="/tmp/antithesis_sdk.jsonl"
if [[ -n "${ANTITHESIS_OUTPUT_DIR:-}" ]]; then
  OUTPUT_PATH="${ANTITHESIS_OUTPUT_DIR}/sdk.jsonl"
elif [[ -n "${ANTITHESIS_SDK_LOCAL_OUTPUT:-}" ]]; then
  OUTPUT_PATH="${ANTITHESIS_SDK_LOCAL_OUTPUT}"
fi
mkdir -p "$(dirname "$OUTPUT_PATH")"
echo '{"antithesis_setup":{"status":"complete","details":{"message":"ready to go"}}}' >> "${OUTPUT_PATH}"
