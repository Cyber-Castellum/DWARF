# Mixed Cardano/Amaru hot-KES security design

## Decision

Create a new package at `antithesis/cardano_amaru_kes_security/`. Copy the
September 5 `cardano_amaru_relay_bootstrap_control` package byte-for-byte as the
benign substrate, then add dedicated Cardano-node and Amaru victims behind
dedicated live DWARF ChainSync proxies. Do not alter any existing package.

The exact security question is:

> When the same otherwise-valid, live Conway header is changed only within its
> 448-byte hot-KES signature and delivered over real N2N ChainSync, do Cardano
> node and Amaru both refuse to adopt it while the benign mixed network remains
> live under Antithesis process and network faults?

This is a protocol/adversary differential, not another bootstrap experiment.

## Why this is new

The W34 reports cover fee divergence, the old Amaru rewards panic, harness
failures, CPU/output pressure, and generic header-seed failure. They do not
contain a live mixed hot-KES mutation result. Current `pragma-org/amaru` issues,
the Amaru wiki, `IntersectMBO/cardano-node` issues, and
`cardano-foundation/cardano-node-antithesis` issues likewise contain no report
of this exact differential. Amaru source does contain the expected
`InvalidKesSignature` validation branch; exercising a specified validation
branch under mixed runtime and faults is a regression/security property, not a
claim that the mere existence of that branch is novel.

The local DWARF run `20260905T083615Z-26b9ac9c` proved one control and one
signature-tail mutation end to end against dedicated Cardano and Amaru victims.
It did not provide a self-contained Antithesis package, repeated live targets,
test-template scheduling, or recovery/fault exploration.

## Substrate contract

The new package preserves every service and asset from
`cardano_amaru_relay_bootstrap_control`. That control is the current replacement
for the obsolete `upstream_amaru_control` image/bootstrap flow. Its local proof
requires:

- normalized producer genesis clocks;
- three Cardano producers and two Cardano relays;
- two independently bootstrapped Amaru relays using the digest-pinned current
  `amaru-relay-bootstrap` artifact;
- an isolated Cardano consumer whose only upstreams are Amaru relays on port
  3000; and
- separate setup, relay-progress, and consumer-convergence evidence.

The new security delta must not weaken any of those gates.

## Security data path

Two dedicated DWARF proxies continuously mirror `p1.example:3001` into an
in-memory chain and serve that advancing chain to exactly one victim each:

```text
p1 -> kes-cardano-proxy -> kes-cardano-victim
p1 -> kes-amaru-proxy   -> kes-amaru-victim
```

Both proxies use the same explicit seed. For each real header they derive a
sub-seed from the encoded header bytes. The sub-seed decides whether to mutate,
which of the final 448 KES-signature payload bytes to change, and which bit to
flip. The decision is pure and replayable: identical seed plus identical header
bytes yields identical wire bytes. No header-body, VRF, opcert, slot, block
number, parent hash, CBOR length, or framing byte is changed.

Most headers remain honest. A victim can therefore advance and prove the path
is usable before it encounters a mutation. Both proxies additionally suppress
mutation before source slot 1800. Both victims start only after the same
Cardano seed snapshot completes, so their initially different bootstrap
points converge on a common future header stream before mutation is eligible.
Each dedicated victim then remains
at the last valid header while its proxy repeatedly offers the same invalid
successor. This is an intentional safety attack, not a liveness claim. The
explicit shared seed is fixed per reviewed campaign for exact paired replay.

Each proxy writes bounded JSONL evidence to its own shared volume and emits
fallback-SDK reachability events when it starts, accepts its victim, serves an
honest header, and serves a KES mutation. The two proxy instances never share a
writable evidence file.

## Victim initialization and observation

The Cardano victim receives a private copy of `p1` state after the established
Amaru bootstrap sentinel. Its topology contains only its dedicated proxy. Its
RTS capability count is limited to reduce the CPU-pressure class seen in W34.

The Amaru victim uses the same proven `amaru-relay-bootstrap` contract and a
private store, but its runtime `AMARU_PEER` is its dedicated proxy. It shares
the Cardano victim's successful seed-completion dependency so the two streams
overlap before slot 1800. A Bash
process-substitution wrapper preserves `exec` signal behavior while teeing only
this victim's bounded logs into a shared evidence volume. The observer requires
the explicit Amaru invalid-KES classification; it does not infer rejection from
an absent tip alone.

The workload image contains the current Python SDK 0.3.1 and catalogs its
Python sources. Its test template contains:

- `parallel_driver_observe_kes.py`: bounded, fault-active observation of new
  mutation events and both victims;
- `anytime_kes_non_adoption.py`: checks that neither victim reports the mutated
  header as its adopted tip when both endpoints are observable; and
- `eventually_kes_recovery.py`: after faults stop, requires the benign mixed
  control to resume progress while the paired KES encounter remains
  classifiable. It does not claim the malicious-only victims advance.

Transport/unavailability is never scored as semantic rejection. Safety checks
are emitted only for a concrete mutation whose delivery and victim observation
are both evidenced. Missing observations fail the separate `Sometimes`/
reachability gates, preventing vacuous green results.

## Fault model

The proven baseline infrastructure retains its existing fault exclusions.
Victims and proxies are faultable. The workload/oracle is excluded with the
Moog token string `network,kill,pause,stop`, never a Boolean. This focuses the
state space on connection loss, victim restart, proxy restart/seed change, and
replay around the rejection boundary without invalidating the bootstrap oracle.

## Readiness and launch boundary

`setup_complete` remains owned by the proven sidecar path and is not emitted by
the workload. It is only deployment acceptance. Readiness for a paid run also
requires a fresh local DWARF execution proving baseline convergence, both
dedicated proxy/victim paths, honest advancement before mutation, mutation
delivery, non-adoption, explicit Amaru classification, executable test
commands, and a clean bounded recovery command.

Two independent fresh-volume DWARF runs proved this contract on 2026-09-06:
`20260906T054611Z-54d146dd` and `20260906T060356Z-555ea49f`. Both produced a
paired post-slot-1800 mutation, explicit Amaru invalid-KES classification,
non-adoption by both victims, converged control progress, and true semantic
verdicts from all three test commands.

The Antithesis Compose file contains public anonymous digest references for
every custom image. A local tag belongs only in a local override. The first
one-hour run completed on 2026-09-06 as
`8417206dcfc6e6c97dc31e0c11a96bcb-60-7`: the mixed KES path and all non-vacuity
properties ran under faults, and neither implementation adopted the paired
invalid header. No KES differential was found.

Triage found that expected Cardano-victim pauses could raise an uncaught
`subprocess.TimeoutExpired` in the observer. The repaired source returns a
transient unavailable observation and continues its bounded retry. A new
workload image digest is required before an optional repeat run; the original
digest remains the immutable identity of the completed run.
