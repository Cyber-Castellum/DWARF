# DWARF — 8-hour exhaustive native-SanCov fuzz campaign (cardano-node Haskell)

**Date:** 2026-06-20 · **Host:** build-host · **Image:** `dwarf-haskell-cov:0.2`
**Duration:** 28,800 s (8 h) per surface, 9 surfaces in parallel · **Task:** #118 (#5 depth)

## Method
Coverage-guided AFL++ (afl.rs 4.40c) over a native GHC **SanitizerCoverage**-instrumented
cardano-node dependency tree (`-fllvm` + LLVM SanCov pass, ~2.07M-entry edge map). One
binary, surface selected by `DWARF_DECODER`; fork-per-exec, file-arg harness. Oracle:
clean `DeserialiseFailure` / validation reject → exit 0; any other uncaught exception or
RTS abort → `SIGABRT` (AFL crash); non-termination → AFL timeout (hang). Per-surface AFL
output + a 10-min SARIF/`REPORT.md` harvest; `plot_data`, `docker logs`, and crash/hang
dirs persisted.

## Result: 0 crashes over ~20.5M executions

| surface | entrypoint | execs | edges (8h) | edges (1h) | Δ | corpus | cycles | stability | crashes | hangs |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| applyblock | BBODY→LEDGERS→per-tx LEDGER over genesis NewEpochState | 1,850,770 | **28,058** | 24,842 | +13% | 4,108 | 2 | 88.5% | 0 | 4* |
| applytx | applyTx (mempool LEDGER STS) | 2,267,940 | 13,892 | 11,908 | +17% | 3,897 | 2 | 99.6% | 0 | 1* |
| tx | wire GenTx → Conway tx decode | 2,336,495 | 12,425 | 7,570 | +64% | 3,823 | 1 | 99.6% | 0 | 5* |
| block | full block decode (widest) | 2,337,037 | 7,918 | 4,056 | +95% | 3,099 | 13 | 99.2% | 0 | 2* |
| ledger | Conway TxBody + getMinFeeTxUtxo | 2,350,172 | 6,475 | 5,402 | +20% | 1,504 | 10 | 99.9% | 0 | 0 |
| handshake | N2N handshake codec decode | 2,305,194 | 4,599 | 4,362 | +5% | 4,225 | 2 | 98.9% | 0 | 0 |
| header | Praos header decode | 2,351,019 | 4,394 | 4,312 | +2% | 1,097 | 10 | 99.2% | 0 | 0 |
| txsub | tx-submission2 codec decode | 2,356,429 | 2,432 | 1,140 | +113% | 481 | 149 | 99.6% | 0 | 0 |
| keepalive | keep-alive codec decode | 2,356,455 | 1,091 | 1,088 | +0% | 173 | 264 | 99.5% | 0 | 1* |

**Totals: ~20.5M execs · 0 crashes · 13 hangs (all spurious, see below).**
(*1h figures from the live hourly checks; the run *started* near the 60-s numbers.)

## Why 8 hours (not a soak)
This was coverage-guided, not a timed soak. The long window paid off where the surface
was wide: **block +95%, txsub +113% (a late jump at ~h7 after 147 dry cycles), tx +64%,
ledger +20%, applytx +17%, applyblock +13%**. Surfaces with small grammars plateaued
early and just cycled (keepalive +0% over 264 cycles, header +2%, handshake +5%) — their
value here is the 0-crash soak, not new edges. applyblock led absolute coverage (28k
edges) as the deepest surface (block-body + the full per-tx Conway ledger rules).

## The 13 hangs are false positives, not findings
AFL saved 13 inputs as "hangs" (tx 5, applyblock 4, block 2, applytx 1, keepalive 1),
clustered in two CPU-load spikes (9 parallel campaigns + the substrate stack; the second
batch coincided with an unrelated persistent-mode build/test). **Replayed standalone they
run in ~20–97 ms with rc=0** (decode surfaces ~20 ms; applyblock ~80 ms for the full
BBODY→LEDGERS work) — far under any timeout. They are AFL's contention-relative
exec-timeout tripping on fast inputs, not non-termination/DoS. The SARIF lists them as
`cov-hang` results for completeness; this note is the adjudication.

## Throughput note
Fork-per-exec ran at ~82 execs/s (applyblock ~66/s) — ~12–13 ms/exec, dominated by
fork + GHC RTS startup + classifying the 2.07M-entry whole-tree bitmap (the decode itself
is microseconds). AFL **persistent mode** was prototyped to remove the fork+RTS cost but
**hangs on the GHC RTS deferred fork** (managed-runtime incompatibility; `-V0 -I0` no
help) — documented, left unbuilt. The realistic speedup is shrinking the SanCov map
(instrument only ledger/consensus/codec packages); for raw generational throughput the
in-process `dwarf-decoder-fuzz` already does ~73k/s.

## Artifacts
- `dwarf-exhaustive-fuzz.sarif` — SARIF 2.1.0 (schema-valid), 9 runs, 13 `cov-hang` results, 0 `cov-crash`.
- `plot/*.plot_data` — per-surface AFL coverage-over-time.
- `REPORT.md` — the live harvester's final snapshot.
- (box) `~/cov8h-results/{logs,crashes-*}` — docker logs + the (empty) crash dirs.
