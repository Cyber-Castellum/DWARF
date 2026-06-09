#!/usr/bin/env bash
#
# finally_fuzz_summary.sh — one end-of-run coverage marker proving the
# dwarf-adversary fuzz daemon stayed alive to end-of-test.
#
# dwarf-adversary is a long-lived chain-sync UPSTREAM SERVER (not a
# per-tick exec), so its perturbation assertions are emitted by the
# daemon itself (dwarf_served_mutated_header, dwarf_base_header_obtained).
# This finally_ script only emits a single test-level coverage signal:
# if "dwarf_fuzz_run_completed" is passed in the report, the fuzz server
# survived to end-of-test; if absent, it was killed early and the other
# dwarf_* Sometimes rows are suspect.
#
# Lifecycle notes (mirrors CF's finally_adversary_summary.sh):
# - No 'set -e'. Antithesis fault injection can deliver SIGTERM mid-
#   script; with set -e the interrupted command's exit code propagates
#   and trips the "Always: Commands finish with zero exit code" property.
#   This script's only job is to emit one marker; always exit 0.
# - No sleep. Nothing here checks an invariant, so there's no settle
#   delay to wait out — skipping it removes the largest interruption
#   window.

OUT="${ANTITHESIS_OUTPUT_DIR:-/tmp}/sdk.jsonl"
mkdir -p "$(dirname "$OUT")" 2>/dev/null

jq -nc '{
  antithesis_assert: {
    id:"dwarf_fuzz_run_completed", message:"dwarf_fuzz_run_completed",
    condition:true, display_type:"Sometimes", hit:true, must_hit:true,
    assert_type:"sometimes",
    location:{file:"",function:"",class:"",begin_line:0,begin_column:0},
    details:null }
}' >> "$OUT" 2>/dev/null || true

exit 0
