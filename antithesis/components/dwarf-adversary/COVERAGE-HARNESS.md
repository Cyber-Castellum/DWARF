# dwarf-decode-any — coverage-guided cardano-node (Haskell) fuzz harness

The cardano-node (Haskell) counterpart of the `amaru-cargo-fuzz-*` (Rust/libFuzzer)
targets. `amaru` coverage measures amaru's Rust code; this measures **cardano-node's
own Haskell code paths** (cborg + cardano-ledger + ouroboros), which amaru cannot.

Single binary, surface selected by the `DWARF_DECODER` env var, one input file
per exec (AFL's `@@`). Catalog manifests: `dwarf/targets/manifests/cardano-node-cov-*.yaml`.

| `DWARF_DECODER` | entrypoint | corpus | measured edges (60s) |
|---|---|---|---|
| `tx` | `decTx` — wire GenTx → Conway tx | `corpora/tx` | 4,232 |
| `block` | `decBlock` — full block (widest decode surface) | `corpora/block` | 3,023 |
| `header` | `decHeader` — Praos header | `corpora/block` | 511 |
| `ledger` | Conway `TxBody` decode + `getMinFeeTxUtxo` (ledger min-fee/UTxO) | `corpora/txbody` | 3,114 |
| `applytx` | decode full Conway `Tx` + `applyTx` (full STS rule pipeline: UTXOW→UTXO→…) | `corpora/conwaytx` | 8,795 |
| `applyblock` | decode Conway `Tx` → `Block BHeaderView` → `applyBlockEither` (BBODY→LEDGERS→per-tx LEDGER) over a genesis-initialised Conway `NewEpochState` | `corpora/conwaytx` | 21,236 |

`applytx` runs the real mempool `applyTx` (ValidateAll) against a default `LedgerState`
(empty UTxO) + fixed Conway `Globals`: witness/value/fee/script checks all execute.
`applyblock` is the deepest ledger surface (~2× applytx): it builds an initial Conway
`NewEpochState` from genesis (`DWARF_GENESIS_DIR`, default `ledger-genesis/`; Shelley
staking emptied; Conway-hard-fork-at-0) once per process, wraps the decoded tx in a
single-tx block whose body hash/size match so the BBODY structural checks pass, and runs
the full block-application STS. Proven to reach `ConwayUtxow/Utxo/Certs` failures.
Both the genesis bootstrap and the surface live in the shared library module
`DwarfAdversary.ApplyBlock`, so the Antithesis SDK harness reuses them
(`dwarf-decoder-fuzz --target applyblock`).

NOTE: the AFL `afl-fuzz` binary must match the runtime linked into the instrumented
target (afl.rs **4.40c**); a mismatched system `afl-fuzz` (e.g. 4.09c) fails the
forkserver handshake. The `applyblock` framework scenario sets `DWARF_AFL_FUZZ` to the
vendored 4.40c, and `aflpp_campaign.py` passes `-m none` (the genesis build exceeds
AFL's default memory cap).

Real consensus header validation (Praos/VRF/KES/operational-cert crypto) is NOT in
`applyblock` — it uses a `BHeaderView`, so only body + ledger rules run. The
consensus-level `tickThenApply` path is blocked by the snapshot version skew
(Peras/1.0.x vs the adversary's 0.25.x) and is a follow-on.

### Mini-protocol message codecs (typed-protocols `decode` in a representative state)
These exercise the N2N mini-protocol *message grammar* (envelopes, request/reply
structure) that the payload decoders don't. Plumbing lives in
`DwarfAdversary.MiniProtocolDecode`.

| `DWARF_DECODER` | codec / state | corpus | edges (45s) |
|---|---|---|---|
| `handshake` | `nodeToNodeHandshakeCodec` @`StConfirm` (version negotiation) | `corpora/handshake` | 959 |
| `txsub` | `codecTxSubmission2` @`StIdle` (request grammar, real GenTx codecs) | `corpora/txsub` | 707 |
| `keepalive` | `codecKeepAlive_v2` @`StServer` (cookie) | `corpora/keepalive` | 570 |

All 100% stability, 0 crashes. Remaining mini-protocols (peer-sharing, and chain-sync /
block-fetch message envelopes — payloads already covered by `header`/`block`) are the
next additions.

Oracle: clean reject (`DeserialiseFailure` / `Left` / trailing bytes) → exit 0;
any other uncaught exception / RTS abort → `SIGABRT` (AFL crash); hang → AFL timeout.

## Throughput (and why persistent mode doesn't help here)

Fork-per-exec runs at **~82 execs/s** (applyblock ~66/s) — ~12-13 ms/exec. The
decode itself is microseconds; the cost is **fork + GHC RTS startup + classifying
the ~2.07M-entry whole-tree SanCov bitmap every exec**. AFL persistent mode
(`__AFL_LOOP`, fuzz-persist/) was prototyped to remove the fork+RTS cost: AFL
engages persistent + shared-memory mode, but the deferred fork (after the GHC RTS
is up) **hangs the persistent child** — the known managed-runtime persistent-fork
incompatibility (`-V0 -I0` did not help). Fork-per-exec works only because
afl-compiler-rt forks at the C constructor, before the RTS.

The realistic speedup is **shrinking the coverage map** — instrument only the
ledger/consensus/codec packages instead of the whole 275-package tree — which
cuts the per-exec bitmap-classification cost (and focuses coverage). For raw
generational throughput, the in-process `dwarf-decoder-fuzz` already does ~73k/s
(no coverage steering).

## Coverage mechanism (native, not QEMU)
GHC emits no SanitizerCoverage natively. The whole dep tree is compiled with
`-fllvm` + a new-PM LLVM pass plugin that injects `trace-pc-guard` edge coverage,
linked against AFL's `afl-compiler-rt.o`. Recipe + fixes:
`docs/superpowers/specs/2026-06-17-coverage-guided-haskell-decoder-fuzzing.md`
and memory `ghc-sancov-coverage-recipe`. Toolchain lives on build-host at
`~/dwarf-sancov/` (plugin, opt/link wrappers, `with-compiler` ghcw.sh, libsancovrt).

## Build (build-host, GHC 9.6.7 + LLVM-15)
```bash
cd antithesis/components/dwarf-adversary
export PATH=$HOME/.ghcup/bin:$PATH LD_PRELOAD=${SANCOV_TOOLCHAIN}/libsancovrt.so
cabal build dwarf-decode-any        # cabal.project.local pins with-compiler=ghcw.sh
```

## Run
```bash
python3 tools/gen-cov-corpora.py    # (re)generate corpora/{tx,block,txbody}
BIN=$(cabal list-bin dwarf-decode-any)
DWARF_DECODER=ledger afl-fuzz -i corpora/txbody -o out -- "$BIN" @@
```

Native ~80–100 execs/s, 100% stability (RTS stable under AFL fork server). These
are short calibration runs; long campaigns + more ledger rules (consumed/produced
value, scripts-needed, full `applyTx` with a constructed `LedgerState`) are the
follow-on.
