# SanCov toolchain (coverage-docker build context)

Source files (committed): `GhcSancov.cpp` (LLVM new-PM SanitizerCoverage plugin),
`libsancovrt.c` (inert build-time rt for TH dlopen), `optwrap.sh`/`linkwrap.sh`/`ghcw.sh`
(GHC `-pgmlo`/`-pgml`/`with-compiler` wrappers).

Vendored AFL++ 4.40c binaries (gitignored, fetch before `docker build`):
`afl-fuzz`, `afl-showmap`, `afl-compiler-rt.o` — copy from a host with `cargo install cargo-afl`
(e.g. `~/.local/share/afl.rs/AFLplusplus/`). A fresh `git clone` of AFL main + a manual
`afl-compiler-rt.o` compile fails the forkserver handshake against the whole-tree map; the
proven 4.40c build is required.
