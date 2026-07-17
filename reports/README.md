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
- `dwarf-ledger-correctness-fuzzing-campaign-report.html` — **ledger-correctness & high-volume**
  local campaigns (the FU1–FU5 follow-ups): the node **actively rejects** adversarial input at the
  right layer and accepts nothing it shouldn't. Reject-oracle (2,445 decode-rejects, 0/165,560
  blocks adopted); **ledger-layer reach** (3,266 witness-tampered txs over 8h, 0 mempool-accepted);
  **validation-bypass battery** (4 ledger rules each correctly enforced, 0 bypass); high-volume
  in-process decoder fuzzing (15M decodes persisted, ~7.2B over an 8h soak, 0 uncaught
  exceptions). Self-contained with verbatim raw-log snippets; backing logs in
  `ledger-correctness-evidence/`.
- `dwarf-cbor-fuzzing-campaign-client-report.html` — the consolidated CBOR fuzzing client report:
  Antithesis live runs itemized (tx / block-header / block decoders, malformed + structural CBOR,
  1h–3h each, incl. the SP3a eclipse run) with per-run testRunId + assertion tables, plus the 8h
  local soak summary. The `dwarf_served_mutated_{tx,header,block}` assertions passed live; the
  node never crashed. (Scope note: this report covers the CBOR-decoder runs; the run-ledger
  above is the full set of all 29 runs.)
- `dwarf-8h-exhaustive-sancov-campaign.html` — the 9-surface native-SanCov local deep-dive
  (self-contained HTML view of `8h-exhaustive-campaign/`).
- `dwarf-consensus-chain-selection-differential-campaign.html` — the **cross-implementation
  chain-selection differential** on the upstream `cardano_amaru` mesh: does `cardano-node` ever
  disagree with the Rust `Amaru` node on which chain is canonical? A node-agnostic Common-Prefix
  oracle across five regimes (honest reorg, within-k partition recovery, epoch boundary, VRF
  tiebreak, private-fork-under-delay), each looped **4 h**. **466 iterations, 0 genuine
  divergences** (9 stage1 flags are benign 1-slot intra-Amaru propagation jitter). Backing
  logs/scripts in `consensus-differential-evidence/`.
- `dwarf-consensus-long-range-attack-differential.html` — the **long-range deep-rollback
  rejection differential**: each node is eclipsed behind a chain-serving adversary that, once the
  node is caught up, injects an exact 10-block (> k=5) `MsgRollBackward`. Both `cardano-node` and
  `Amaru` **refuse identically** (selected tip never regresses > k). ~3 h soak: 91 injections into
  cardano-node, 92 into Amaru, **0 regressions, 0 divergences**. Backing logs/harness in
  `consensus-differential-evidence/`.

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

## Ledger-correctness evidence (`ledger-correctness-evidence/`)

Persisted raw logs + harness scripts backing the ledger-correctness report:
`logs/witness_soak.log` (8h ledger-layer reach), `logs/decoder_fuzz_results.txt` (15M decoder-fuzz),
`logs/fu1_struct.log` (reject-oracle), `logs/seed_soak_8h.log` + `logs/cert_soak_8h.log`
(certificate/seed-corpus soaks), and `scripts/` (reject_oracle, witness/seed/cert soaks,
validation-bypass battery builders). Also bundled as `fuzzing-correctness-evidence.tar.gz`. No
credentials present.

Raw AFL fuzzer logs: `../raw/logs/`.

## Consensus differential evidence (`consensus-differential-evidence/`)

Summaries + heartbeats + harness backing the two consensus differential campaign reports
(`campaign-reports/dwarf-consensus-chain-selection-differential-campaign.html` and
`…-long-range-attack-differential.html`). `logs/run4h-*.summary.json` + `…heartbeat.jsonl` are
the per-scenario 4-hour soak tallies (chainhold 80/80, epoch-boundary 94/94, rollback 47/47,
stage1 145/154 with 9 benign 1-slot jitter flags, threshold 91/91 = **466 iterations, 0 genuine
divergences**); `logs/longrange-deep-rollback-soak.log` is the long-range deep-rollback soak
(91/92 injections, 0 > k regressions). `scenario-reports/consensus-report-*.md` are the
per-scenario write-ups; `scripts/` holds `consensus_4h_runner.py`, the eclipse harness
(`docker-compose.lr-eclipse.yaml`, `relay-lr-topology.json`), the adversary's
`deepRollbackChainSyncServer`, and the differential oracles. Also bundled as
`consensus-differential-evidence.tar.gz`. No credentials present.
