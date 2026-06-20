# Reports & evidence

Results from the successful DWARF fuzzing campaigns (local + Antithesis).

- `8h-exhaustive-campaign/` — native-SanCov coverage-guided AFL++ over 9 cardano-node
  decode + ledger surfaces, 8 h each: **~20.5M executions, 0 crashes** (WRITEUP.md,
  dwarf-exhaustive-fuzz.sarif, REPORT.md, fuzzer_stats/, plot/, cleaned-logs/).
- `campaign-reports/` — the two campaign-results reports:
  - `dwarf-cbor-fuzzing-campaign-client-report.html` — the consolidated CBOR fuzzing
    client report: **10 Antithesis live runs** itemized (tx / block-header / block
    decoders, malformed + structural CBOR, 1h–3h each, incl. the SP3a eclipse run) with
    per-run testRunId + assertion tables, plus the 8 h local soak summary. All three
    `dwarf_served_mutated_{tx,header,block}` assertions passed live; the node never crashed.
  - `dwarf-8h-exhaustive-sancov-campaign.html` — the 9-surface native-SanCov local
    deep-dive (self-contained HTML view of `8h-exhaustive-campaign/`).

Raw AFL fuzzer logs: `../raw/logs/`.
