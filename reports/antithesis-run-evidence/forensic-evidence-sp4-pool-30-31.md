# Antithesis Run Forensic Evidence — SP4 generative-pool addendum (#30–#31)

Append to `forensic-evidence.md`. Convention unchanged: "report" = run's own Antithesis triage
report (authoritative); "list" = dashboard runs-list column. Verdict per finding: INCLUDE (real
defect) / EXCLUDE (benign artifact). **Still 0 rare; no real defect.**

## #30 — 5bec1e05… · 666e8cf try1 · SP4 generative-pool state-machine, no-faults 1h (img 0.25.0) — VERIFIED vs source report
Completed 1h41m. Properties 110: **96 passed / 3 failed**. list: 0 new / 3 ongoing / 0 resolved / 0 rare. Findings (verbatim, Failed tab): "The Antithesis Fault Injector was started" (EXCLUDE, `--no-faults`; Setup 1/6), "All commands were started at least once" (EXCLUDE, Test Templates 2/14), "All commands were run to completion at least once" (EXCLUDE, Test Templates). Never: Cardano Node Errors PASS, Never: Cardano Node Critical PASS. Pool adversary (initiator, dials relay2) engaged across protocols — sdk.jsonl: dwarf_sm_keepalive_WrongAgency Reachable·true {class:WrongAgency,protocol:keepalive}; dwarf_sm_chainsync_PrematureTerminal Reachable; dwarf_sm_served_chainsync Sometimes. 713,323 captured log items incl. `state-machine-init[keepalive]: dialing; injecting departure=WrongAgency`. testRunId 5bec1e05…; tx 306f970b…. **No real defect.**

## #31 — e075a2ac… · 666e8cf try2 · SP4 generative-pool state-machine, faults-ON 3h (img 0.25.0) — VERIFIED vs source report
Completed 3h53m, `faults_enabled=true`, on-chain `outcome:success`. Properties 117: **104 passed / 0 FAILED**. list: 0 new / 0 ongoing / 1 resolved / 0 rare. The 1 resolved = "The Antithesis Fault Injector was started" (now satisfied, faults ON). Groups all green: Setup 6, Test Templates 74, +17, +2+2. Never: Cardano Node Errors PASS, Never: Cardano Node Critical PASS — relays p1/p2/p3/relay1/relay2 fault-EXPOSED (only fuzz/harness containers carry exclude_from_faults). Node survived the fast-pool illegal-sequence adversary + fault injection (partition/delay/kill/pause) with zero failed properties. testRunId e075a2ac…; tx 24f9ef41…. **No real defect — cleanest run in the index.**

---

Cross-run line update: across runs #1–#31, **0 rare; no real defect**. The SP4 effort's one
real defect (ChainSync.Codec `printf` format-string) was found by the predecessor shallow
responder, not these runs — these establish node-safety of the server-side mini-protocol
state machines at scale (12.4M local injections; 1h+3h on Antithesis incl. faults).
