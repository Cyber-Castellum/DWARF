# Antithesis Run Forensic Evidence — verified against source report snapshots

Convention: "report" = the run's own Antithesis triage report (authoritative); "list" = dashboard runs-list summary column (can differ — findings lifecycle). Verdict per finding: INCLUDE (real defect) / EXCLUDE (benign artifact). Across all runs: **0 rare; no real defect.**

## #1 — 859ad183… · 68c97a7b try2 · applyBlock faults-ON 3h (img 0.19/0.18) — VERIFIED (earlier deep pull)
Completed 3h19m. Properties 70: 68 passed / 2 failed. 2 new / 0 rare. Findings: dwarf_base_header_obtained (EXCLUDE, header N/A in tx mode); "All commands run to completion" (EXCLUDE, test-template). Never: Cardano Node Errors PASS, Critical PASS — relays p1/p2/p3/relay1/relay2 fault-EXPOSED. dwarf_base_tx_obtained fired.

## #2 — 0341f850… · 68c97a7b try1 · applyBlock no-faults 1h — VERIFIED (earlier deep pull)
Completed 1h17m. Properties 66: 62 passed / 4 failed. 4 new / 0 rare. Findings: Fault-Injector-started (EXCLUDE, --no-faults); dwarf_base_header_obtained (EXCLUDE); All-commands-started + All-commands-run-to-completion (EXCLUDE, templates). Never: Errors/Critical PASS. Consensus held (5 nodes adopt tip 32e68751…@449).

## #3 — ce0552a7… · 99404740 try2 · witness/ledger + in-process decoder-fuzz 3h (img 0.18) — VERIFIED vs source
report: Test hours 6d 7h 5m / wall 3h 8m. Passed 78 / Failed 4. 4 new / 0 rare. Findings (verbatim): "The Antithesis Fault Injector was started" (EXCLUDE), "Sometimes assertions → dwarf_base_header_obtained" (EXCLUDE), "All commands were started at least once" (EXCLUDE), "All commands were run to completion at least once" (EXCLUDE). Categories: Setup failed 1/6, SDK failed 1/53, Test Templates failed 2/16, Correctness passed 2, Never: Cardano Node Errors PASS, Never: Cardano Node Critical PASS. Assertions (verbatim, sdk.jsonl): dwarf_served_mutated_tx {depth:0,kind:wit:sigflip,seed:16122733323927362000,shape:witness} (served, ×2); dwarf_base_tx_obtained {count:2,seeds:2}. decoder-fuzz container ran, clean child exits (status 0).

## #4 — 03616e63… · 99404740 try1 · same bundle 1h — VERIFIED vs source
report: Test hours 2d 7h 30m / wall 1h 9m. Passed 69 / Failed 4. 4 new / 0 rare. Same 4 benign findings. Categories: Setup 1/6, SDK 1/46, Templates 2/14, Never: Errors PASS, Critical PASS. dwarf_base_tx_obtained {count:2,seeds:2}; in-process decoder-fuzz input stream active (campaign pc-K6A5…).

## #5 — 01797fe9… · 730146 try2 · INCOMPLETE — BUILD FAILURE — VERIFIED vs source
NOT a runtime/webhook death. Antithesis could not pull a bundle image. Verbatim: "Pulling image ghcr.io/j-gainsec/dwarf-decoder-fuzz:0.1.0…" → "Error: initializing source docker://ghcr.io/j-gainsec/dwarf-decoder-fuzz:0.1.0: unable to retrieve auth token: invalid username/password: unauthorized" (×6 retries: 2/4/8/16/32s) → "Failed command after 6 attempts" → antithesis_error code 4001 "could not resolve to a reachable image: 'dwarf-decoder-fuzz' … tag 0.1.0" → "Command customer,environment.build.from_notebook failed with code: 125" → "Status is: failed". (cardano-node/tracer + dwarf-adversary pulled OK; only the separate dwarf-decoder-fuzz:0.1.0 image was unpublished/unauthorized.) Node never evaluated. ~2026-06-17 04:05 UTC.

## #6 — 163bff56… · 730146 try1 · INCOMPLETE — BUILD FAILURE — VERIFIED vs source
Verbatim: "Trying to pull ghcr.io/j-gainsec/dwarf-adversary:0.17.0…" → "Error: initializing source docker://ghcr.io/j-gainsec/dwarf-adversary:0.17.0: reading manifest 0.17.0 in ghcr.io/j-gainsec/dwarf-adversary: manifest unknown" (×6 retries) → "Failed command after 6 attempts" → antithesis_error 4001 "dwarf-adversary … tag 0.17.0" → "failed with code: 125" → "Status is: failed". Failure differs from #5: here the adversary tag 0.17.0 itself was not pushed (manifest unknown). Node never evaluated. ~2026-06-17 03:53 UTC.

## #7 — d656ffd6… · 42028dc try2 · cert seed-corpus 3h (img 0.16) — VERIFIED vs source
report: Test hours 6d 5h 56m / wall 3h 7m. Passed 77 / Failed 8. report Findings = 9 new / 0 rare (LIST showed 6 new — discrepancy noted). Findings incl. dwarf_base_header_obtained (EXCLUDE) + Fault-Injector-started (EXCLUDE). Never: Errors PASS, Critical PASS. dwarf_base_tx_obtained present.

## #8 — 1251f60f… · 42028dc try1 · cert seed-corpus 1h — VERIFIED vs source
report: Test hours 2d 7h 5m / wall 1h 8m. Passed 84 / Failed 2. report Findings = 6 new / 0 rare (LIST showed 1 new — discrepancy). Findings: dwarf_base_header_obtained (EXCLUDE) + Fault-Injector-started (EXCLUDE); one RESOLVED finding (dwarf_base_tx_obtained Sometimes). Never: Errors PASS, Critical PASS.

## #9–#29 — capture in progress (subagent → run09..run29 .md), then parse + manual verify.
