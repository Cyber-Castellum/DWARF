#!/bin/bash
# GHC -pgmlo wrapper: run GHC legacy-PM opt as requested, then inject
# new-PM ghc-sancov (trace-pc-guard edge coverage) into the same output file.
set -e
out=""; prev=""
for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
opt-15 "$@"
opt-15 -load-pass-plugin=${SANCOV_TOOLCHAIN}/GhcSancov.so -passes=ghc-sancov "$out" -o "$out"
