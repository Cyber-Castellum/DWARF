# Mixed mini-protocol containment and recovery

## Relevant code paths

- `DwarfAdversary.Sequencing.Generate.generateIllegalSequenceRole`
- `DwarfAdversary.StateMachine.scriptedSequenceInitiator`
- `DwarfAdversary.ChainSync.Connection.runSMInitiatorOnce`
- Amaru responder modules for ChainSync, BlockFetch, TxSubmission2, KeepAlive
- Amaru inbound protocol manager/handler lifecycle
- Cardano-node Ouroboros mux and typed-protocol server paths

## Failure modes

- A well-formed but illegal message escapes connection-local containment.
- A violation terminates or wedges the listener, manager, or unrelated sessions.
- One implementation loses honest chain liveness while the other contains the case.
- Restart/recovery leaves a target or the Amaru-fed consumer unable to converge.
- Harness asymmetry sends different cases while claiming a differential.

## Instrumentation

The unchanged SP4 engine logs one descriptor per successful injection. The
prefault observer compares ordered descriptors and requires all 24 cells before
setup. Runtime observation combines continuing transcript counts, TCP probes,
Cardano socket tips, Amaru `tip.adopt` records, fatal classification, Docker
state in local proof, and control/consumer convergence.

## Open questions

None affecting assertion logic. `dwarf_sm_illegal_accepted_*` remains an
exploration heuristic and is explicitly excluded from pass/fail classification.

