# Conway governance ledger coverage — Antithesis live triage evidence

Two live runs on the `amaru-cardano.antithesis.com` tenant, adversary image
`ghcr.io/j-gainsec/dwarf-adversary:0.26.0`, bundle `Cyber-Castellum/DWARF`
`antithesis/cardano_node_dwarf` @ commit `b3d73cd1105f622e09c596a31d64accb5149cc8f`.
Both verified against the run's own Antithesis triage report (console drill-down).

**Workload.** A new `decoder-fuzz-governance` service runs `dwarf-decoder-fuzz --target tx
--shape governance --corpus /corpus-gov` — high-volume in-process fuzzing that mutates the
governance sub-field (proposal_procedures, tx_body key 20) of real Conway gov wire GenTxs and
decodes via the real `decTx`, so the mutated bytes land in the Conway **governance decoder**.
SDK oracle: `dwarf_decoder_no_uncaught_exception` / `no_timeout` (Always), plus reachability
signals. The `decoder-fuzz-governance` container is registered in every fault-exclusion row of
both reports (it ran).

---

## Run A — 1h, clean (`--no-faults`), try 1

- **testRunId:** `b3f37310aedc93d35764bdd7a3a0305e98ce330395dde908c4761ab8dd942dd7`
- **on-chain tx:** `d26f45d8c9cc34e55cf3364611ef48eed9ecb4863ef135993a8903418224c2a1`
- **Status:** Completed, 1h 20m
- **Findings (list):** 4 new · 0 ongoing · 0 resolved · 0 rare
- **Properties:** 72 total — **68 passed, 4 failed**

Node-safety (verbatim, Properties tab):

    Properties → "Never: Cardano Node Errors"     passed
    Properties → "Never: Cardano Node Critical"   passed

The 4 failed = benign harness/coverage markers, **NOT** node or gov-workload defects:
- `The Antithesis Fault Injector was started` — N/A on a `--no-faults` run (Setup 1/6). EXCLUDE.
- `dwarf_base_header_obtained` — a *header* `Sometimes` assertion, N/A for a tx/gov **decoder**
  workload (no header obtained) (SDK 1/45). EXCLUDE.
- `All commands were started at least once` / `…run to completion at least once` — CF template
  command-coverage (Test Templates 2/14). EXCLUDE.

SDK group passed **44/45** — the governance decoder-fuzz assertions among them.
**Verdict: gov workload fuzzed; node-safe; 0 real findings.**

---

## Run B — 3h, fault injection ON, try 2

- **testRunId:** `bbc19a28eae5b09f2e7b74cf45af2f53c872ff0a4e4de93dd8cf90c2dbdea409`
- **on-chain tx:** `a23d1a8b2f1ea4ff1db55ee8108515ee555f81a9827d426e3b88c76dfd3f4b90`
- **Status:** Completed, 3h 18m · `faults_enabled=true`
- **Outcome (on-chain):** `success`
- **Findings (list):** 2 new · 0 ongoing · 0 resolved · 0 rare
- **Properties:** 82 total — **80 passed, 2 failed**

Node-safety held **under active fault injection** (verbatim, Properties tab):

    Properties → "Never: Cardano Node Errors"     passed
    Properties → "Never: Cardano Node Critical"   passed

`fault_injector` events recur throughout the report (faults genuinely applied —
partition/delay/kill/pause); only fuzz/harness containers carry `exclude_from_faults`, so relays
were fault-exposed. Cleaner than Run A: **Setup passed 6/6** (fault-injector now satisfied) and
**Test Templates passed 17/17** (command coverage over 3h). The only 2 failures are SDK 2/52 —
benign N/A `Sometimes` assertions (`dwarf_base_header_obtained` + a base-tx/header assertion),
same EXCLUDE category as every prior run.

**Verdict: gov workload fuzzed under chaos; node survived 3h of adversary + fault injection with
0 real findings; `outcome:success`.**

---

## Summary

| Run | Dur | Faults | Properties | Node-safety | Verdict |
|---|---|---|---|---|---|
| A (1h, try1) | 1h20m | off | 68 pass / 4 fail | Errors PASS · Critical PASS | node-safe; 4 benign markers |
| B (3h, try2) | 3h18m | **ON** | **80 pass / 2 fail** | Errors PASS · Critical PASS | node-safe under faults; `outcome:success` |

**No real defect; 0 rare across both.** Continues the cross-run convention in
`../antithesis-run-evidence/forensic-evidence.md` as #32 (Run A) and #33 (Run B). The local side
independently proves the same binary fuzzes the gov decoder: L2 battery rejects 3/3 rules at
their exact `ConwayGovPredFailure` (0 bypass), and the 8h in-process campaign did 1.32B gov
decodes (255M full) with 0 exceptions / 0 timeouts.
