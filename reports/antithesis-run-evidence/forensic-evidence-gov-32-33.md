# Antithesis Run Forensic Evidence — governance addendum (#32–#33)

Append to `forensic-evidence.md`. Convention unchanged. **Still 0 rare; no real defect.**

## #32 — b3f37310… · b3d73cd try1 · Conway governance in-process decoder-fuzz, no-faults 1h (img 0.26.0) — VERIFIED vs source report
Completed 1h20m. Properties 72: **68 passed / 4 failed**. list: 4 new / 0 ongoing / 0 rare. Findings (verbatim): "The Antithesis Fault Injector was started" (EXCLUDE, `--no-faults`; Setup 1/6), `dwarf_base_header_obtained` (EXCLUDE, header N/A in a tx/gov decoder workload; SDK 1/45), "All commands were started at least once" + "…run to completion at least once" (EXCLUDE, Test Templates 2/14). Never: Cardano Node Errors PASS, Never: Cardano Node Critical PASS. The `decoder-fuzz-governance` container ran (registered in fault-exclusion rows); SDK passed 44/45 (gov decode assertions among them). testRunId b3f37310…; tx d26f45d8…. **No real defect.**

## #33 — bbc19a28… · b3d73cd try2 · Conway governance in-process decoder-fuzz, faults-ON 3h (img 0.26.0) — VERIFIED vs source report
Completed 3h18m, `faults_enabled=true`, on-chain `outcome:success`. Properties 82: **80 passed / 2 FAILED**. list: 2 new / 0 ongoing / 0 rare. Cleaner than the 1h: Setup passed 6/6 (fault-injector satisfied), Test Templates passed 17/17. The 2 fails = SDK 2/52, benign N/A Sometimes (dwarf_base_header_obtained + a base-tx/header assertion — EXCLUDE). Never: Cardano Node Errors PASS, Never: Cardano Node Critical PASS — relays fault-EXPOSED; `fault_injector` events throughout (partition/delay/kill/pause). Node survived the gov decoder-fuzz workload + fault injection with zero real defects. testRunId bbc19a28…; tx a23d1a8b…. **No real defect — cleanest gov run.**

---

Cross-run: across #1–#33, **0 rare; no real defect**. The governance cycle establishes correct
per-rule rejection (local L2: 3/3 `ConwayGovPredFailure`, 0 bypass) and node-safety of the gov
decoder at scale (1.32B in-process gov decodes; 1h + 3h Antithesis incl. faults).
