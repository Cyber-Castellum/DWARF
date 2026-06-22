# Reports & evidence

Results from the DWARF fuzzing campaigns against `cardano-node` (local + Antithesis).

## Campaign reports (`campaign-reports/`)

- `dwarf-antithesis-run-ledger-forensic.html` — the **complete forensic ledger of all 29
  Antithesis runs** for `Cyber-Castellum/DWARF` (requester `j-gainsec`), 2026-06-08 → 06-22.
  Every run is listed with its `testRunId`, commit·try, purpose, status, Passed/Failed counts,
  finding counts, and node-safety — each **verified against the run's own Antithesis triage
  report**. Result: **25 Completed, 4 Incomplete; 0 rare findings anywhere; `Never: Cardano Node
  Errors`/`Critical` passed on every Completed run; no real node/decoder defect.** The 4
  Incomplete runs never produced a result and split into 2 image-build failures (#5/#6) and 2
  setup-deaths on stripped eclipse/baked bundles (#19/#20) — neither a code defect. Includes the
  honest evidence limits (serve/decoder assertions are SDK-indexed events, not rendered as named
  rows in the report HTML) and the corrections this audit produced.
- `dwarf-applyblock-ledger-apply-campaign-report.html` — client report for the **applyBlock
  ledger-apply** surface (adversary image 0.19.0): in-process `decode → BHeaderView → BBODY →
  LEDGERS → per-tx ledger rules` over a baked Conway genesis. Proven live on Antithesis both
  clean (1h, run `0341f850…`) and **under fault injection** (3h, run `859ad183…`, relays
  fault-exposed) — both Completed, node error/critical-free, 0 rare — backed by the 8h local
  SanCov applyblock soak (1.85M execs, 28k edges, 0 crashes).
- `dwarf-cbor-fuzzing-campaign-client-report.html` — the consolidated CBOR fuzzing client report:
  Antithesis live runs itemized (tx / block-header / block decoders, malformed + structural CBOR,
  1h–3h each, incl. the SP3a eclipse run) with per-run testRunId + assertion tables, plus the 8h
  local soak summary. The `dwarf_served_mutated_{tx,header,block}` assertions passed live; the
  node never crashed. (Scope note: this report covers the CBOR-decoder runs; the run-ledger
  above is the full set of all 29 runs.)
- `dwarf-8h-exhaustive-sancov-campaign.html` — the 9-surface native-SanCov local deep-dive
  (self-contained HTML view of `8h-exhaustive-campaign/`).

## Local coverage-guided soak (`8h-exhaustive-campaign/`)

Native-SanCov coverage-guided AFL++ over 9 cardano-node decode + ledger surfaces, 8 h each:
**~20.5M executions, 0 crashes** (13 hangs, all adjudicated false positives — see `WRITEUP.md`).
Contents: `REPORT.md`, `WRITEUP.md`, `dwarf-exhaustive-fuzz.sarif`, `fuzzer_stats/`, `plot/`,
`cleaned-logs/`.

## Audit trail (`antithesis-run-evidence/`)

Raw per-run Antithesis triage-report snapshots (`run03-*.md` … `run29-*.md`) plus
`forensic-evidence.md`, backing the run ledger above — one file per run, so any claim in the
ledger ties back to its source report. **Report access tokens have been scrubbed**
(`auth=<REDACTED>`); these files contain report content + run IDs only, no credentials.

Raw AFL fuzzer logs: `../raw/logs/`.
