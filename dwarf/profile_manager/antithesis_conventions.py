"""Antithesis bundle conventions, pinned from the installed antithesis-* skills.

Source of truth (read-only):
  ~/.codex/skills/antithesis-setup/references/docker-compose.md
  ~/.codex/skills/antithesis-setup/assets/antithesis/setup-complete.sh
  ~/.codex/skills/antithesis-workload/references/assertions.md
  ~/.codex/skills/antithesis-workload/references/test-commands.md

Python SDK (antithesis PyPI package):
  from antithesis.assertions import always, sometimes, reachable, unreachable
  always(condition: bool, message: str, details: dict) -> None
"""

# Path of the compose file inside a bundle directory.
COMPOSE_RELPATH = "config/docker-compose.yaml"

# Path of the setup-complete entrypoint inside a bundle directory.
SETUP_COMPLETE_RELPATH = "setup-complete.sh"

# Directory holding Antithesis test commands inside a bundle directory.
TEST_DIR = "test"

# Every service must declare this platform (Antithesis runs on x86-64).
PLATFORM = "linux/amd64"

# Verbatim copy of the skill's setup-complete.sh asset.
SETUP_COMPLETE_SH = """#!/usr/bin/env bash
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
"""
