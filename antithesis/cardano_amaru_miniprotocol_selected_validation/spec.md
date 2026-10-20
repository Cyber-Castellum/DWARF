# Selected-version mixed mini-protocol validation

Contract: `antithesis/spec.md` v1.2 at
`a8b21de65d82f97563c8665d81a5230af5993894`.
Reviewed: 2026-10-20. Author: Codex; independent review: binding_review.

This directory is the public delivery of the **wizard-generated overlay**.
The adjacent Compose is the runnable configuration. The original retained DWARF
recipe remains unchanged. Detailed evidence is maintained in the DWARF workbench:
https://bench.gainpalfam.com/wb/dwarf-runpage/o/obj_8ec6579a3008413f8c8f3540

Status: **G2 locally exercised**. Public delivery, deployed MOOG verification
and remote execution remain pending.

## Experiment and selected targets

Test whether Cardano 10.6.2 and Amaru 10.11.20260918 remain observable and contain
the paired mini-protocol adversary workload, and recover/progress under the
recorded Antithesis fault profile. The dedicated victims are `sm-cardano-victim`
and `sm-amaru-victim`. The supporting control mesh and observers have exclusions
for network, kill, pause and stop; those labels do not exclude every possible
platform fault.

- Harness: existing upstream Amaru control substrate plus retained paired
  mini-protocol workload, through the dedicated Antithesis wizard adapters.
- Scenario: `cardano-amaru-miniprotocol-security-local`; scenario-default topology.
- Cardano selected image:
  `ghcr.io/intersectmbo/cardano-node@sha256:c3cbc5aa0b758ba211567791539a67001e661fbe7a09275e37449e8b1252c4b2`.
  Applied to relay1, relay2 and the Cardano victim.
- Amaru selected image:
  `ghcr.io/pragma-org/amaru@sha256:2176f12085219a4052be17dca89742954897eb9a5321554c799d0458913c9c60`.
  The existing extraction helper supplies `/target/amaru` to both Amaru control
  relays and the Amaru victim. Bootstrap tooling remains a separate pinned image.
- Requested measurements: none; stock releases. SDK assertions do not prove
  node coverage instrumentation or measurement patches.
- Seed: `0x20260907`. Four protocols × six departure classes form the 24-cell
  logged-description comparison. Equal logged prefixes do not prove identical
  delivered wire bytes.
- Historical precedents and failures: canonical spec section 8 and reconciliation
  section 20. They do not qualify this selected-version overlay.
- Authorization: user instructed continuation of validation followed by one
  one-hour test after validation, on 2026-10-20. Intended remote faults: enabled.

## Candidate transformations

The generated package now emits `internal: false` networks, and the Antithesis
adapter adds `container_name`, `hostname` and `init: true` to the extracted-binary
helper after checking the existing shared transformation. Local runtime code is
unchanged. All 21 services have matching service/container names and init enabled.

Materialization replaces the workload build context with the already-built
workload image, whose source bytes match this regenerated package. References
containing both tag and digest are reduced to `repository@digest`; the selected
digest is unchanged. The final workload reference is
`ghcr.io/j-gainsec/dwarf-sm@sha256:d55a630bcf029558e6146df5800ddd4176b8b17e4df4e59341c070c149b7ec69`.
Its manifest bytes were verified anonymously after publication. Non-runtime
preview provenance is omitted from this delivery; runtime assets are unchanged
except for the local-to-public workload image reference.

Source ZIP SHA-256:
`4d73b6a06982a7233d4258bdad91dd84a5de3aec1d5c5bf5bbac23a250201043`.
Candidate Compose SHA-256 before workload publication:
`2ea7a35c4cb88e837d998e44b79d895598910239fa41d3db080d297034faf006`.
The local candidate configuration image's Compose readback matches these bytes.

The local smoke uses the candidate Compose with
`COMPOSE_PROJECT_NAME=dwarf-remote-validation-20261020`; it does not rename the
explicit service containers or change network internal flags. Collision and
empty-state checks precede startup. This desktop environment is not the offline
Antithesis simulator.

## Roles, startup and observation wiring

| Services | Role and readiness | Fault policy |
|---|---|---|
| configurator | Generates shared genesis/configuration; completes before producers | Four classes excluded |
| p1, p2, p3 | Honest Cardano producer/control mesh; actual tips must agree | Four classes excluded |
| tracer, tracer-sidecar, log-tailer | Shared tracer socket/log volume and observation output | Four classes excluded |
| relay1, relay2 | Selected Cardano control relays | Four classes excluded |
| amaru-target-extract | One-shot selected executable extraction; completion gates consumers | Four classes excluded |
| amaru-relay-1, amaru-relay-2 | Bootstrap from producer history, private stores, selected executable | Four classes excluded |
| amaru-consumer-seed, amaru-consumer | Matching state seed and consumer fed through Amaru | Four classes excluded |
| sm-cardano-seed | One-shot victim state preparation | No exclusion label; record any actual fault |
| sm-cardano-victim, sm-amaru-victim | Selected binaries, dedicated state/logs; startup marker/socket and TCP readiness gate fuzzers | Fault eligible |
| sm-cardano-fuzzer, sm-amaru-fuzzer | Persistent SP4 services with identical seed; write paired logs | Four classes excluded |
| sm-workload | Reads fuzzer/runtime logs, sockets and status; produces pre-fault ready marker | Four classes excluded |
| sidecar | Waits for relay markers, victim readiness and pre-fault marker, then emits setup-complete | Four classes excluded |

Preserve `.example` DNS names and the recipe's peer configurations. Cardano
listeners use 3001; downstream Amaru listeners use 3000. Inspect actual listener
logs before interpreting reachability. Producers use the configurator-generated
peer map; retain their generated genesis clocks and topology as runtime evidence.

Each Amaru node has private writable storage. The observer mounts both fuzzer
logs, both victim runtime logs, producer/victim/consumer sockets and shared status.
The `tracer` and `tracer-sidecar` share the tracer log volume; `sidecar` is a
different service. Preserve raw volume logs before cleanup, not only Docker logs.
The candidate Compose is the complete mount/peer/dependency inventory.

## Commands and semantics

Stopped-image inspection finds exactly these three executable files under
`/opt/antithesis/test/v1/mixed-miniprotocol-security/`, matching generated bytes.
No other service image has a Composer command catalog.

| Command | Behavior and evidence |
|---|---|
| parallel_driver_fuzz_observe.py | Samples the persistent fuzzer activity and target/control observations; emits reachability, coverage and containment assertions |
| anytime_containment.py | Checks current containment and fatal-signal observations; generated guards require available runtime logs |
| eventually_recovery.py | Samples until recovery/convergence or 150-second deadline; generated guard also requires classifiable evidence |

Antithesis stops infrastructure fault injection and other Composer commands for
`eventually_`; the persistent fuzzer services continue. Recovery here therefore
means progress/convergence with that protocol stimulus still present. No custom
stop-faults hook is required. The recovery assertion uses `sometimes`, so its
passing report status does not mean every invocation recovered.

Missing runtime logs make the generated observation unclassifiable. Review
assertion encounters and false samples separately from command return codes.
A zero exit code can accompany false assertions. A failed containment assertion
requires classification against actual infrastructure faults, observer health
and target evidence before assigning a node defect.

## Requirement matrix and gates

| Contract | Current evidence / remaining requirement |
|---|---|
| C01 | Root Compose and packaged relative assets; candidate config-image readback matches. Public Git and actual MOOG image pending |
| C02 | Eleven distinct Linux/amd64 image variants inspected; selected binary versions and target processes captured in the fresh run |
| C03 | Candidate has images only; all eleven image references verified anonymously, including the published workload |
| C04 | No pull policy/custom logging. Remote output efficiency remains to be measured |
| C05 | Both candidate networks explicitly false, including config-image copy |
| C06 | All 21 service/container names match; `.example` peer names retained |
| C07 | All 21 services init-enabled; helper completed successfully in fresh smoke |
| C08 | Dependency and readiness paths retained; fresh full setup passed after bootstrap |
| C09 | Mounts retained; fresh private stores used; raw victim/fuzzer volume logs retained |
| C10 | Existing label validator passes; actual launcher profile and remote events pending |
| C11 | Single existing sidecar setup path; fresh setup-complete detected by Snouty |
| C12 | Stopped-image inventory, executable modes and source hashes verified |
| C13 | Exact three-command catalog; three local commands passed; remote encounters pending |
| C14 | Existing Dwarf asset scan passes; final published tree still requires review |
| C15 | Seed, source package, image variants and candidate Compose recorded; fresh runtime observations retained |

Fresh local verification used Snouty 0.6.1 (`3c7b57c`), with a 1,200-second
setup budget and `--keep-running`. It detected setup-complete and the expected
one driver, one anytime and one eventually command. All three were then invoked
explicitly and exited zero. Their observations were classifiable/safe; the final
recovery predicate was true. All 62 captured SDK assertion events (60 unique IDs)
had condition true. This was a faults-off local run on AMD64 emulation, not a
remote fault/recovery result. The eventual predicate succeeded in an already
progressing system.

The pre-fault common prefix covered all 24 cells over 101 descriptions; complete
stream lengths were 331 Cardano and 101 Amaru. The final command observed all
three producers, both victims and the consumer at block 285 / slot 1424 with the
same hash. Actual victim version probes returned the selected versions. Raw
victim/fuzzer volume logs, process lists, setup logs and SDK events were retained.
Scoped cleanup removed all test containers, volumes and networks; unrelated
`arctic-base` remained running.

G3 awaits immutable public Git input verification and final submission checks.
The workload is public. The existing requester on cardano-box uses release
MOOG 0.5.1.3; wallet identity, repository whitelist and requester registration
were checked. CLI help defines duration in hours; the intended argument is 1.
Use the existing Dwarf request builder and MOOG release binary; verify that its
duration argument means one hour. Do not use `snouty launch`. Check requester
registration and the `amaru-cardano` tenant without POSTing a diagnostic launch.
Record the MOOG ID before retrying any submission to avoid duplicate tests.

## Remote results

Not submitted. No MOOG ID or Antithesis ID exists for this candidate.
After launch, retain actual config-image identity, setup, exact catalog,
invocations, selected node versions, actual fault exclusions/events and terminal
properties. Do not substitute the earlier local smoke or historical reports.
