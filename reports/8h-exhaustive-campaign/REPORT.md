# DWARF native-SanCov exhaustive fuzz campaign — live report

Coverage-guided AFL++ over the cardano-node (Haskell) decode + ledger surfaces (GHC SanitizerCoverage). Oracle: clean DeserialiseFailure/validation reject -> exit 0; any uncaught exception / RTS abort -> SIGABRT (crash); hang -> timeout.

| surface | entrypoint | run_s | execs | edges | corpus | cycles | stability | crashes | hangs |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| tx | wire GenTx -> Conway tx decode | 28800 | 2336495 | 12425 | 3823 | 1 | 99.58% | 0 | 5 |
| block | full block decode (widest) | 28800 | 2337037 | 7918 | 3099 | 13 | 99.23% | 0 | 2 |
| header | Praos header decode | 28800 | 2351019 | 4394 | 1097 | 10 | 99.20% | 0 | 0 |
| ledger | Conway TxBody + getMinFeeTxUtxo | 28800 | 2350172 | 6475 | 1504 | 10 | 99.94% | 0 | 0 |
| applytx | applyTx (mempool LEDGER STS) | 28800 | 2267940 | 13892 | 3897 | 2 | 99.64% | 0 | 1 |
| applyblock | applyBlock (BBODY->LEDGERS->per-tx rules) over genesis NewEpochState | 28800 | 1850770 | 28058 | 4108 | 2 | 88.52% | 0 | 4 |
| handshake | N2N handshake codec decode | 28800 | 2305194 | 4599 | 4225 | 2 | 98.87% | 0 | 0 |
| txsub | tx-submission2 codec decode | 28800 | 2356429 | 2432 | 481 | 149 | 99.63% | 0 | 0 |
| keepalive | keep-alive codec decode | 28800 | 2356455 | 1091 | 173 | 264 | 99.54% | 0 | 1 |

**Totals:** crashes=0, hangs=13 across 9 surfaces.

SARIF: `/tmp/cov8h/dwarf-exhaustive-fuzz.sarif` (9 runs).
