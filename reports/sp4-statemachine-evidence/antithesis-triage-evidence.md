# SP4 state-machine fuzz — Antithesis live triage evidence

Two live runs on the `amaru-cardano.antithesis.com` tenant, adversary image
`ghcr.io/j-gainsec/dwarf-adversary:0.21.0`, bundle `Cyber-Castellum/DWARF`
`antithesis/cardano_node_dwarf` @ commit `e87edcf990d3712a30050a6725c6e5772f2a4dc1`.
Both verified against the run's own Antithesis triage report (console drill-down).

The adversary serves chain-sync (mini-protocol #2) with a raw scripted responder
(`StateMachine.hs`) that emits **well-formed** messages in **illegal protocol
state / agency** — so the bytes decode cleanly and the node's mini-protocol
**state machine** (not its decoder) must reject them.

---

## Run 1 — 1h, clean (`--no-faults`), try 1

- **testRunId:** `908e48fee8f3573517e57557f72ce6b00428a108bd37febd5f4711cf4d573e6b`
- **on-chain tx:** `5170924467efdea6bbd0c2819e2dc767447aed76eee5b2770b2157a160513b2c`
- **Status:** Completed, 1h 16m
- **Findings:** 0 rare · 3 benign · 1 resolved
- **Properties:** 71 total — **61 passed, 3 failed**

Node-safety (verbatim, Properties tab):

    Properties → "Never: Cardano Node Errors"     passed
    Properties → "Never: Cardano Node Critical"   passed

Workload engaged (verbatim, Smoke Test Logs t=423.659):

    dwarf-adversary: state-machine: node opened chainsync (msg); injecting
                     illegal-sequence scenario = double-awaitreply

    antithesis/pods/dwarf-adversary.example/sdk.jsonl  (dwarf-adversary.example)
      Assertion : Reachable     Condition : true
      message   : "dwarf_statemachine_violation_served"
      details   : {scenario: double-awaitreply}

The 3 failed = benign Antithesis template/coverage markers, NOT node defects:
- `The Antithesis Fault Injector was started` — N/A on a `--no-faults` run (Setup).
- `All commands were started at least once` — harness command-coverage (Test Templates).
- `All commands were run to completion at least once` — harness command-coverage.
Resolved (1): `Sometimes assertions → dwarf_base_header_obtained`.

---

## Run 2 — 3h, fault injection ON, try 2

- **testRunId:** `dd2bde6927aac96d0f7d8f085046b3b0fc4ea850b0775a911e1d1db54228e54a`
- **on-chain tx:** `b5942472d996914f43ec69f9c67d4782d9344c3605d4394727ddfde5d2162abf`
- **Status:** Completed, 3h 28m · `faults_enabled=true`
- **Findings:** 0 rare · 1 benign · 1 resolved
- **Properties:** **68 passed, 1 failed** — cleaner than the clean run

Node-safety held **under active fault injection** (verbatim, Properties tab):

    Properties → "Never: Cardano Node Errors"     passed
    Properties → "Never: Cardano Node Critical"   passed

Faults were actually injected — the report's `fault_injector` events recur
throughout, and the run notes describe clock/CPU faults
("the clock speed of the simulated processor is randomized..."). Relays
`p1/p2/p3/relay1/relay2` were fault-exposed (only fuzz/harness containers excluded).

Workload engaged under chaos (verbatim):

    message   : "dwarf_statemachine_violation_served"
    Condition : true
    details   : {scenario: double-awaitreply}

`background_monitor` mid-run (containers created 02:08, after faults began) shows
the cluster healthy: `name:relay2 ... state:Running`,
`image_name:.../dwarf-adversary:0.21.0 ... state:Running`, all producers + both
decoder-fuzz workloads `Running`.

Property groups: Setup **passed 6** (the fault-injector-started marker now
satisfies, since faults were enabled — the clean run's 1/6 failure resolved),
Antithesis SDK passed 43 (incl. `dwarf_statemachine_violation_served`),
Correctness passed 2, Performance passed. The lone failure: `All commands were
run to completion at least once` (Test Templates 1/13) — a benign command-coverage
marker. Resolved (1): `dwarf_base_header_obtained`.

---

## Net

Across **1h clean + 3h under fault injection**, with a trusted local-root peer
feeding `relay2` illegal chain-sync sequences: `Never: Cardano Node Errors` and
`Never: Cardano Node Critical` **passed in both**, **0 rare findings in both**, and
the `dwarf_statemachine_violation_served` assertion fired in both (including under
chaos). No node defect. The only failures are benign Antithesis template/coverage
markers. Backed locally by the 8h soak (`logs/`): 2,386 injections, node-safe
throughout, the `printf` artifact reproduced 7,158× (always non-fatal).
