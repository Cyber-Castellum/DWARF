# SP4 generative-pool state-machine fuzz — Antithesis live triage evidence

Successor to `antithesis-triage-evidence.md` (which covered the shallow 6-fixed-scenario
ChainSync responder, image `0.21.0`). This covers the **generative concurrent-pool**
redesign, adversary image `ghcr.io/j-gainsec/dwarf-adversary:0.25.0`, bundle
`Cyber-Castellum/DWARF` `antithesis/cardano_node_dwarf` @ commit
`666e8cf356120bcd21b3d1e2737ebd270adac119`. Both runs verified against the run's own
Antithesis triage report (console drill-down on the `amaru-cardano.antithesis.com` tenant).

**What changed vs. the 0.21.0 runs.** The adversary is no longer a single inbound responder
on ChainSync #2. It is an **adversary-initiated concurrent pool**: N workers dial the node
(`relay2.example:3001`), each opens a mini-protocol as **initiator**, walks a legal prefix
from that protocol's M3 state-machine model, then injects one illegal departure (wrong-agency
/ out-of-state / premature-terminal / post-terminal / flood / duplicate), force-closes
(`--sm-conn-ms 750`), and reconnects. Because the adversary is the initiator it opens **every**
protocol itself — so this run exercises the node's **server-side** state machines for
ChainSync #2, **BlockFetch #3**, TxSubmission2 #4, and KeepAlive #8 (the 0.21.0 run only
reached ChainSync). Frames are well-formed; the node's mini-protocol **state machine** (not
its decoder) must reject them.

Command (verbatim, from the run's report compose):

    dwarf-adversary: image ghcr.io/j-gainsec/dwarf-adversary:0.25.0
      --network-magic 42 --state-machine-fuzz --sm-connections 16
      --sm-conn-ms 750 --upstream relay2.example:3001 --seed random

---

## Run A — 1h, clean (`--no-faults`), try 1

- **testRunId:** `5bec1e056c0d9718d5c2f50adc07defdfd06de14a2bd95edcd7b9143754d9a23`
- **on-chain tx:** `306f970b785593020f284d7ff5f0627bd84b4bec08722772cbfbcd876bc33c5f`
- **Status:** Completed, 1h 41m
- **Findings (runs-list):** 0 new · 3 ongoing · 0 resolved · 0 rare
- **Properties:** 110 total — **96 passed, 3 failed**

Node-safety (verbatim, Properties tab):

    Properties → "Never: Cardano Node Errors"     passed
    Properties → "Never: Cardano Node Critical"   passed

Workload engaged — pool injecting across protocols (verbatim, Smoke Test Logs t=568.743,
713,323 captured log items):

    dwarf-adversary: state-machine-init[keepalive]: dialing; injecting departure=WrongAgency legalPrefix=0 frames=1

    antithesis/pods/dwarf-adversary.example/sdk.jsonl  (dwarf-adversary.example)
      Assertion : Reachable     Condition : true
      message   : "dwarf_sm_keepalive_WrongAgency"
      details   : {class:WrongAgency,protocol:keepalive}

    Assertion : Reachable   message: "dwarf_sm_chainsync_PrematureTerminal"  details: {class:PrematureTerminal,protocol:chainsync}
    Assertion : Sometimes   message: "dwarf_sm_served_chainsync"             details: {protocol:chainsync}

The 3 failed = benign Antithesis template/coverage markers, **NOT** node defects (verbatim
leaf names from the Failed tab):
- `The Antithesis Fault Injector was started` — N/A on a `--no-faults` run (group: Setup, 1/6). EXCLUDE.
- `All commands were started at least once` — harness command-coverage (group: Antithesis Test Templates, 2/14). EXCLUDE.
- `All commands were run to completion at least once` — harness command-coverage. EXCLUDE.

**Verdict: 0 rare, 0 new node findings. Node-safe.** The 3 failures are harness meta-checks;
the only one tied to our choice is the fault injector being off because we ran `--no-faults`.

---

## Run B — 3h, fault injection ON, try 2

- **testRunId:** `e075a2acc72f0be1ac05b6c98614af43c42b6a63dfa32cae1057db3326890e36`
- **on-chain tx:** `24f9ef41512e80298aa5a7d1c6104c047ca8fb894787a23cb724431d594d4bc7`
- **Status:** Completed, 3h 53m · `faults_enabled=true`
- **Outcome (on-chain):** `success` (zero failed properties)
- **Findings (runs-list):** 0 new · 0 ongoing · 1 resolved · 0 rare
- **Properties:** 117 total — **104 passed, 0 FAILED**

Node-safety held **under active fault injection** (verbatim, Properties tab):

    Properties → "Never: Cardano Node Errors"     passed
    Properties → "Never: Cardano Node Critical"   passed

Property groups (verbatim, all green): Setup passed 6/6 · (SDK) passed 2 · passed 2 ·
Antithesis Test Templates passed 74 · passed 17. **Failed 0.**

The **1 resolved** finding is `The Antithesis Fault Injector was started` — it (correctly)
failed in the no-faults Run A and is now satisfied because faults are ON. Faults were
actually injected (network partition / delay / kill / pause); only the fuzz/harness
containers carry `com.antithesis.exclude_from_faults`, so relays `p1/p2/p3/relay1/relay2`
were fault-exposed.

**Verdict: 0 rare, 0 new findings, 0 failed properties. The node survived 3h of the fast-pool
illegal-sequence adversary PLUS Antithesis fault injection with zero failures.** This is the
cleaner of the two runs (the no-faults markers from Run A are gone).

---

## Summary

| Run | Dur | Faults | Properties | Findings | Node-safety | Verdict |
|---|---|---|---|---|---|---|
| A (1h, try 1) | 1h41m | off | 96 pass / 3 fail | 0 new · 0 rare | Errors PASS · Critical PASS | node-safe; 3 benign harness markers |
| B (3h, try 2) | 3h53m | **ON** | **104 pass / 0 fail** | 0 new · 1 resolved · 0 rare | Errors PASS · Critical PASS | node-safe under faults; `outcome:success` |

**No real defect; 0 rare across both.** Consistent with the cross-run convention in
`../antithesis-run-evidence/forensic-evidence.md` (runs #1–#29). These two runs continue that
index as #30 (Run A) and #31 (Run B).

> Honest scope note: this generative-pool campaign found **no new node bug**. The SP4 effort's
> one real defect — the `printf` format-string bug in `Ouroboros.Network.Protocol.ChainSync.Codec`
> (see `ouroboros-chainsync-printf-bug-report.md`) — was surfaced by the *predecessor* shallow
> ChainSync responder. The value here is breadth + a node-safety proof at scale: the node's
> server-side mini-protocol state machines (all four live protocols, incl. BlockFetch) safely
> reject the generated illegal-sequence space on a real devnet and under fault injection.
