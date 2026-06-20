#!/bin/bash
# with-compiler wrapper: -fllvm + GhcSancov plugin + linkwrap (exe rt), NCG
# fallback per package. Build env must set LD_PRELOAD=libsancovrt.so so GHC's
# Template-Haskell dlopen of instrumented dependency .so resolves sancov syms.
REAL=${HOME}/.ghcup/bin/ghc-9.6.7
OPTWRAP=${SANCOV_TOOLCHAIN}/optwrap.sh
LINKWRAP=${SANCOV_TOOLCHAIN}/linkwrap.sh
if "$REAL" -fllvm -pgmlo "$OPTWRAP" -pgmlc llc-15 -pgml "$LINKWRAP" "$@"; then exit 0; fi
exec "$REAL" "$@"
