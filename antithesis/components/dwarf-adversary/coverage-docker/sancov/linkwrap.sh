#!/bin/bash
# GHC -pgml link wrapper. For shared-library links (-shared): pass through
# untouched (undefined sancov symbols in a .so are resolved later; -no-pie must
# not be combined with -shared). For executable links: force -no-pie (SanCov
# emits absolute relocations a PIE link rejects) and append AFL's runtime
# (inert outside afl-fuzz).
for a in "$@"; do
  if [ "$a" = "-shared" ]; then exec gcc "$@"; fi
done
exec gcc -no-pie "$@" ${HOME}/.local/share/afl.rs/AFLplusplus/afl-compiler-rt.o -ldl -lpthread
