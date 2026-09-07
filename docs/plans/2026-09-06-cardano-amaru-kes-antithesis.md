# Mixed Cardano/Amaru hot-KES security implementation plan

**Goal:** Produce and locally prove a new, self-contained Antithesis package
that repeatedly delivers deterministic signature-only hot-KES mutations to
dedicated Cardano-node and Amaru victims without weakening the proven mixed
control.

**Constraints:** No worktree, no edits to existing scenario packages, no
credential material, all live work on `cardano-box`, and no Antithesis/Moog
submission without explicit approval.

## 1. Freeze research and contracts

- Add the package-local Antithesis scratchbook with source revisions, topology,
  existing assertions, properties, relationships, and evaluation.
- Record the September 5 relay-bootstrap control as the only acceptable current
  substrate and the September 5 local KES run as feasibility evidence.
- Record the W34/upstream issue audit and the precise novelty boundary.

## 2. Write failing contract and unit tests

- Assert all original control assets and services remain semantically identical.
- Assert only named security services, volumes, networks, and topology assets are
  added.
- Assert valid Moog fault-label strings, digest-only Antithesis images, no build
  contexts, no secrets/Apple metadata/bytecode, and valid executable test-command
  prefixes.
- Unit-test deterministic selection, 448-byte signature-only bit mutation,
  rate boundaries, and distinct-header variability.
- Unit-test the observer's fail-closed classification and non-vacuity rules.
- Run the focused tests and preserve the expected failures.

## 3. Extend the KES adversary without breaking local mode

- Keep the existing four-argument one-target mode unchanged.
- Add an explicit live-proxy mode using `runChainProducerInto` and
  `advancingChainSyncServer`.
- Add a pure seeded KES codec that mutates only a selected bit in the final
  448-byte signature payload and leaves non-selected headers byte-identical.
- Emit bounded, uniquely identified mutation/honest/connection evidence and
  fallback-SDK reachability events.
- Add a thin Ubuntu runtime Dockerfile/build script for the new binary.

## 4. Assemble the additive package

- Copy the relay-bootstrap control into
  `antithesis/cardano_amaru_kes_security/`.
- Add private seed/state volumes, victim-only topologies, two proxy services,
  two victim services, and a Python workload service.
- Retain the exact proven baseline services and control-path settings.
- Use a local override for the locally built KES/workload images; leave no
  floating image in the Antithesis Compose contract.

## 5. Implement the observer and test template

- Parse only fresh, schema-valid proxy events.
- Query the Cardano victim through its mounted node socket.
- Require the explicit Amaru invalid-KES log marker for semantic
  classification.
- Emit unique Reachable, Sometimes, and Always properties with details that
  include seed, source point, original hash, mutated hash, byte offset, and bit.
- Bound all polling and output; classify unavailable/timeout separately.

## 6. Prove locally through DWARF

- Sync the additive files to `cardano-box` without deleting unrelated state.
- Build the Haskell binary with `-Werror`, run its tests, build local images, and
  render the exact local Compose model.
- Add a uniquely named DWARF scenario/probe. Start a fresh unique Compose
  project, run all readiness and security gates, invoke every test command in
  documented order, and retain bounded logs/results.
- Repeat from fresh volumes once because bootstrap/state timing is material.
- Stop only the new project; retain evidence and do not touch existing stacks.

## 7. Final pre-publication review

- Run focused and applicable regression tests, DWARF semantic validation,
  Compose rendering, image inspection, secret/metadata scans, and the project
  preflight checklist.
- Diff the final baseline portion mechanically against the September 5 control.
- Produce a report that marks each gate pass/fail and names the first blocker.
- Stop before publication, Moog, or Antithesis. Public digest pinning and paid
  launch require a separate explicit approval.

## Local implementation result

Completed on 2026-09-06 with two independent fresh-volume DWARF passes:
`20260906T054611Z-54d146dd` and `20260906T060356Z-555ea49f`. The proven
orchestration requires a 30-minute Compose bootstrap allowance, shell-only
seed cleanup compatible with the pinned Cardano image, overlaying the existing
read-only topology mountpoint, synchronized victim startup, and a shared
minimum mutation slot of 1800.

The first one-hour live run subsequently completed from public commit
`59b2a0ece5f70710b6a15e288a6d7ee2cf53def8` as Antithesis run
`8417206dcfc6e6c97dc31e0c11a96bcb-60-7`. All mixed KES safety, reachability,
classification, and recovery properties passed under active faults; no KES
acceptance divergence was found. The run did find two known inherited failures
(Amaru rewards-summary restart and Cardano fork-depth) plus a distinct Amaru
supervised-listener EADDRINUSE candidate. It also showed that the observer did
not catch `cardano-cli` timeouts during victim pauses. That harness defect now
has failing-then-passing regression coverage and a minimal local repair. The
next publication boundary is a new workload image digest containing that
repair, followed by the unchanged preflight gates before any optional rerun.
