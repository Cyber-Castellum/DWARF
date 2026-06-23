# SP4 mini-protocol state-machine fuzz — evidence

Backs `../campaign-reports/dwarf-antithesis-sp4-statemachine-live.html`.

A new, non-decoder cardano-node fuzzing surface: the DWARF adversary
(`--state-machine-fuzz`, image `0.21.0`) drives chain-sync (mini-protocol #2)
with a raw mux-channel responder that sends **well-formed** messages in
**illegal protocol state / agency** (six scenarios: wrong-agency-requestnext,
wrong-agency-done, double-awaitreply, awaitreply-storm, requestnext-flood,
done-then-more). The bytes decode cleanly; what must reject them is the node's
mini-protocol **state machine** + multiplexer — a surface no decoder/CBOR
fuzzing reaches.

## Contents

- `StateMachine.hs` — the adversary responder source (the scripted illegal
  sequences; exact ChainSync wire frames documented inline).
- `antithesis-triage-evidence.md` — verbatim triage for **both** live Antithesis
  runs: 1h clean (testRunId `908e48fe…`) and 3h with fault injection
  (testRunId `dd2bde69…`). Node-safety (`Never: Cardano Node Errors`/`Critical`)
  passed in both; 0 rare in both; `dwarf_statemachine_violation_served` fired in
  both (incl. under faults).
- `logs/statemachine-soak-summary.txt` — the 8h local soak sample table +
  scenario breakdown + final state.
- `logs/statemachine-soak-adversary.log` — full 8h adversary stdout (per-injection).
- `logs/statemachine-soak-node-tracer.json.gz` — full 8h `relay2` node tracer
  (`node.json`): **0** Error/Critical lines, **7,158** `printf: bad formatting
  char 'd'` occurrences.

## Result

Node-safe under chain-sync sequencing attack, **with and without fault
injection** (Antithesis), and across an 8h local soak (2,386 injections, node
`running`/RestartCount 0 throughout, 0 fatal sev).

## The finding (root cause traced)

Rejecting an illegal chain-sync sequence drives the node's unexpected-message
**error-formatting** path, which throws `printf: bad formatting char 'd'`
(`Net.Mux.Remote.ExceptionExit`, mini-protocol #2, `sev:Notice`) instead of a
clean error — whereas byte-corrupted (grammar) frames are rejected with a
well-formed `DecoderFailure`.

Traced to `Ouroboros.Network.Protocol.ChainSync.Codec` (upstream
`ouroboros-network-protocols`): the `decode` fallthrough for `StNext`/`StIntersect`
calls `printf "codecChainSync (%s) unexpected key (%d, %d)"` (one `%s`) but passes
**four** args `(show agency) (show stok) key len` — the String `show stok` is
consumed by `%d` → `errorBadFormat 'd'`. The `SingIdle` branch correctly uses
`(%s, %s)`. Present on current upstream `main` (see
`upstream-main-ChainSync-Codec.hs`, lines 188/191/194). Fix: add the missing `%s`.
Non-fatal (peer still rejected; node survives; reproduced 7,158× with 0
Error/Critical), but it destroys the diagnostic for a misbehaving/malicious peer's
protocol violation.

- `ouroboros-chainsync-printf-bug-report.md` — full upstream bug report (printf
  arity table, reproduction, severity, one-line diff).
- `upstream-main-ChainSync-Codec.hs` — the current upstream `main` source,
  showing the defect is unfixed.

No credentials present.
