# Cardano–Amaru Relay Bootstrap Control Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build and mechanically prove a new additive DWARF mixed Cardano-node/Amaru control scenario using the current upstream `amaru-relay-bootstrap` image before adding adversarial workloads or requesting an Antithesis run.

**Architecture:** Preserve every existing scenario and create a new package modeled on the Cardano Foundation's proven `cardano_amaru` topology. Each Amaru relay bootstraps independently from its paired live Cardano ChainDB using the upstream long-running relay entrypoint; an isolated Cardano consumer has only the two Amaru relays as upstream peers. Readiness, participation, and progress are separate fail-closed gates, and all local execution goes through DWARF on `cardano-box` without a worktree.

**Tech Stack:** Docker Compose, Cardano node 10.7.1, Amaru, `amaru-bootstrap-producer`, `db-analyser`, DWARF scenario/runtime engine, Python/pytest, shell-based OCI and runtime probes.

---

## Non-negotiable constraints

- [ ] Do not create a Git worktree.
- [ ] Do not modify, rename, or delete an existing Antithesis package or scenario.
- [ ] Create a new package and a new DWARF scenario with unique names and Compose project identity.
- [ ] Pin every image by immutable digest and retain the full source revision in evidence.
- [ ] Record source tag `6f641855baecbe9632eb10c14bd58c351ffc2a2c`, but use Antithesis-compatible digest-only Compose spelling `ghcr.io/lambdasistemi/amaru-bootstrap-producer@sha256:aabaf9e1fc1f58045329e14c1127c5424ba4794855d39bce05e3b426b7025c36` unless a newer artifact is deliberately re-audited first.
- [ ] Do not reuse the known-bad `cf657b91...` image.
- [ ] Do not use the obsolete separate one-shot `bootstrap-producer -> service_completed_successfully -> amaru relay` dependency model.
- [ ] Do not treat container start, `snouty validate`, or a startup marker as proof that Amaru bootstrapped or participated.
- [ ] Do not submit to Moog or Antithesis during this plan.
- [ ] Stop at the first failed gate, retain evidence, diagnose it, and rerun locally.

### Task 1: Freeze and verify the upstream contract

**Files:**
- Create: `antithesis/cardano_amaru_relay_bootstrap_control/UPSTREAM-PROVENANCE.md`
- Reference: `dwarf/docs/antithesis-scenario-preflight-checklist.md`
- Reference: `antithesis/upstream_amaru_control/docker-compose.yaml`

**Steps:**

1. Record the audited source revisions for `pragma-org/amaru`, `lambdasistemi/amaru-bootstrap`, `cardano-foundation/cardano-node-antithesis`, and `cardano-foundation/cardano-ignite`.
2. Record the successful historical mixed Antithesis controls: PR #177 and the 21/0 Amaru-served consumer property reported by PR #197.
3. Record the known rewards panic as Amaru issue #1104 and its fixes in PRs #1101 and #1125.
4. Record that the September 4 bootstrap artifact passed supplier CI and its live Cardano verifier but has not yet been proven in a full mixed fault-enabled Antithesis run.
5. Resolve the image anonymously with OCI index/manifest-compatible headers and verify the digest exactly.
6. Inspect the image and require `/bin/amaru`, `/bin/db-analyser`, `/bin/bootstrap-producer`, and the `amaru-relay-bootstrap` entrypoint contract.
7. Save all commands, revisions, URLs, and results without storing credentials.

**Pass condition:** The exact image is anonymously pullable, has the required binaries/entrypoint, contains an Amaru revision newer than the #1104 fixes, and every provenance statement has an authoritative URL.

### Task 2: Write fail-closed package contract tests

**Files:**
- Create: `tests/test_cardano_amaru_relay_bootstrap_control.py`
- Test: `antithesis/cardano_amaru_relay_bootstrap_control/docker-compose.yaml`

**Steps:**

1. Write a failing test requiring the new package directory and Compose file.
2. Require two distinct long-running Amaru relay services using `entrypoint: amaru-relay-bootstrap`.
3. Require each relay to mount a different paired Cardano state volume at `/live:ro`, its Cardano configuration, runtime JSON, startup volume, and private Amaru state.
4. Require unique `RELAY_NAME` and correct paired `AMARU_PEER` values.
5. Reject any Amaru dependency on a separate bootstrap service with `service_completed_successfully`.
6. Require an isolated Cardano consumer network whose topology lists only the two Amaru relays.
7. Require valid MOOG fault-exclusion token strings; reject boolean `exclude_from_faults` values.
8. Require explicit container names, hostnames, restart policy, and setup/readiness plumbing.
9. Require immutable image digests and reject `latest`, mutable-only tags, the old `cf657b91...` image, and embedded sensitive values.
10. Run the focused test and retain the expected initial failure.

**Pass condition:** The test fails because the new package has not yet been implemented, not because the test itself is malformed.

### Task 3: Implement the new additive mixed control package

**Files:**
- Create: `antithesis/cardano_amaru_relay_bootstrap_control/docker-compose.yaml`
- Create: `antithesis/cardano_amaru_relay_bootstrap_control/testnet.yaml`
- Create: `antithesis/cardano_amaru_relay_bootstrap_control/relay-topology.json`
- Create: `antithesis/cardano_amaru_relay_bootstrap_control/amaru-consumer-topology.json`
- Create: `antithesis/cardano_amaru_relay_bootstrap_control/tracer-config.yaml`
- Create: `antithesis/cardano_amaru_relay_bootstrap_control/amaru-runtime/era-history.json`
- Create: `antithesis/cardano_amaru_relay_bootstrap_control/amaru-runtime/global-parameters.json`
- Create: `antithesis/cardano_amaru_relay_bootstrap_control/README.md`

**Steps:**

1. Copy only the proven Cardano topology/configuration shape needed by the new package; do not edit the source package.
2. Add two self-bootstrapping Amaru relays following the current upstream `docs/antithesis.md` contract.
3. Pair relay 1 with `p1` and relay 2 with `p2`; keep their writable state independent.
4. Align genesis, system start, epoch length, era history, network magic, and global consensus parameters.
5. Add the isolated Cardano consumer and ensure DNS/network reachability exists only to the two Amaru relays.
6. Emit setup-complete only after the deployment contract is accepted, while keeping actual bootstrap/sync proof in later runtime assertions.
7. Apply MOOG fault exclusions only to infrastructure/control services using the documented comma-separated string.
8. Render Compose with `docker compose config --quiet` and inspect the complete normalized model.
9. Run the focused contract tests until they pass.

**Pass condition:** Static tests and normalized Compose prove the intended topology, immutable artifacts, valid MOOG labels, and absence of the obsolete bootstrap model.

### Task 4: Add a new DWARF scenario and mechanical runtime probe

**Files:**
- Create: `dwarf/scenarios/cardano-amaru-relay-bootstrap-control-local.yaml`
- Create: `tools/cardano_amaru_relay_bootstrap_probe.py`
- Modify only if required to register the additive scenario: the narrow DWARF scenario registry/data file identified during implementation

**Steps:**

1. Write a scenario test that fails until DWARF discovers the new scenario by its unique identifier.
2. Configure the scenario to invoke the new package through the normal DWARF runtime path on `cardano-box`.
3. Make the probe query real Cardano tips with `cardano-cli`; never infer chain progress from node stdout configuration dumps.
4. Require nonempty `db-analyser` target rows for three consecutive completed epochs.
5. Require a bootstrap-complete sentinel or equivalent upstream completion record for both relays.
6. Require both Amaru processes to remain alive and advance beyond their bootstrap anchors.
7. Require both Amaru relays to cross at least one epoch boundary without the #1104 panic or another fatal consensus error.
8. Require the isolated consumer to advance beyond its seed and reach the producer tip within an explicit tolerance.
9. Record timestamps, slots, blocks, bootstrap points, image IDs/digests, exit states, and relevant bounded logs.
10. Run focused DWARF/unit tests until they pass.

**Pass condition:** The new scenario is discoverable and the probe fails closed for missing targets, incomplete bootstrap, dead/stalled relays, incorrect consumer topology, or lack of Amaru-served progress.

### Task 5: Run a fresh end-to-end control through DWARF on `cardano-box`

**Files:**
- Create after execution: `reports/cardano-amaru-relay-bootstrap-control-<run-id>/README.md`
- Create after execution: `reports/cardano-amaru-relay-bootstrap-control-<run-id>/manifest.json`
- Create after execution: bounded logs and probe output under the same evidence directory

**Steps:**

1. Confirm SSH access, disk space, Docker health, and the active DWARF deployment without changing unrelated containers.
2. Sync only the new package, scenario, tests, and probe to the established `cardano-box` DWARF tree.
3. Pull and inspect every exact image before runtime.
4. Start the scenario through DWARF with a unique Compose project name and fresh dedicated volumes.
5. Observe the ChainDB becoming sufficiently mature and both relays independently completing bootstrap.
6. Continue through at least one post-bootstrap epoch transition; extend the local duration if the configured timing cannot satisfy this proof.
7. Prove the isolated consumer advances through Amaru and reaches the producer tip within tolerance.
8. Search bounded logs for known bootstrap, nonce, VRF, rewards, rollback, consensus-death, and container-exit signatures.
9. Stop only this run's containers and retain its volumes/evidence until review completes.
10. Copy the complete nonsensitive evidence set back into the local report directory.

**Pass condition:** One fresh DWARF-created run proves full bootstrap, two live/advancing Amaru relays, a successful epoch transition, and consumer-through-Amaru convergence. Any missing proof is a failed control, even if containers remain running.

### Task 6: Independent preflight review

**Files:**
- Create: `antithesis/cardano_amaru_relay_bootstrap_control/reports/preflight-proof-<date>.md`
- Update: `dwarf/docs/antithesis-scenario-preflight-checklist.md` only if the execution uncovers a genuinely new reusable pitfall

**Steps:**

1. Review every mandatory gate in `AGENTS.md` and the expanded preflight checklist against retained evidence.
2. Diff the normalized new Compose model against the known-good historical Cardano Foundation mixed model and the current upstream relay-bootstrap contract.
3. Enumerate every intentional difference and explain why it is required.
4. Re-run all focused tests, existing relevant scenario tests, Compose validation, secret scanning, and archive hygiene checks.
5. Verify existing scenarios remain byte-for-byte untouched except for any explicitly reviewed additive registry entry.
6. Mark the baseline `READY FOR SECURITY DELTA` only when every gate has direct evidence.

**Pass condition:** The review has no unexplained topology, bootstrap, image, assertion, or fault-model difference and confirms no existing scenario was broken.

### Task 7: Design and add the security delta only after baseline success

**Files:**
- Create later: a separate new security package derived from the proven control
- Create later: separate workload/oracle tests and a run design

**Steps:**

1. Re-audit current Amaru issues, wiki, Cardano issues, DWARF reports, and W34/earlier reports immediately before selecting the property.
2. Exclude already-saturated consensus fork/rollback coverage and documented known failures unless the scenario tests a materially new mechanism.
3. Select one high-value differential/adversarial property that requires both implementations and is expressible under Antithesis faults.
4. Add only the workload/oracle/fault-model delta to a copy of the locally proven control package.
5. Add Brown-M&M reachability assertions proving the security driver reached both implementations and exercised the intended mutation/fault path.
6. Repeat the full local DWARF control and preflight gates with the security workload enabled.
7. Prepare an Antithesis launch recommendation, but do not submit without explicit user approval.

**Pass condition:** The security package retains all mixed-baseline proofs and adds a uniquely exercised, non-vacuous security property with local evidence.

## Completion definition

This goal is complete only when Tasks 1–6 pass and the new baseline is mechanically proven through DWARF on `cardano-box`. Task 7 is the next gated phase toward the broader security-testing objective. A Compose parse, image pull, container start, setup marker, or `snouty validate` result alone cannot complete the goal.

## Implementation status — 2026-09-05

- [x] Task 1 — upstream contract and exact image provenance frozen.
- [x] Task 2 — fail-closed package tests written with observed RED failures.
- [x] Task 3 — new additive control package implemented; existing scenarios were not edited.
- [x] Task 4 — new DWARF scenario and runtime probe implemented.
- [x] Task 5 — fresh end-to-end run `20260905T052416Z-2a5a18da` passed both the inner runtime gates and outer DWARF assertion.
- [x] Task 6 — independent preflight and evidence review completed; decision is `READY FOR SECURITY DELTA`.
- [ ] Task 7 — deliberately not started; it requires a separate new security package and a fresh issue/report audit.
