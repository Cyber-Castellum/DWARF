# Mixed Cardano/Amaru Harness Full Repair Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the mixed Cardano/Amaru Antithesis harness non-vacuously deliver mutated DWARF blocks to Amaru and report coverage, safety, command, and infrastructure failures correctly.

**Architecture:** Use the existing baked Cardano reference node as the canonical lineage for DWARF and both Amaru relays, while leaving the independently generated Cardano cluster isolated for its existing properties. Harden the oracle catalog and assertion semantics, then require local reachability and restart evidence plus verified MOOG fault scope before any paid run.

**Tech Stack:** Docker Compose, Python 3.11, Antithesis Python SDK 0.2.0, Haskell DWARF adversary, Amaru, cardano-node 10.7.1, MOOG 0.5.1.3, Python `unittest`, shell-based container smoke tests.

---

All work is performed in `/home/nigel/dwarf-fresh` on `cardano-box`. Preserve the untracked mode-0600 `.env`; never add or print it. Do not use `snouty`. Do not submit a paid run as part of this plan.

### Task 1: Preserve the failed-run diagnosis as a regression contract

**Files:**
- Create: `reports/amaru-mixed-harness-vacuity-2026-08-23/README.md`
- Modify: `antithesis/cardano_amaru_adversarial/RUN-DESIGN.md`
- Modify: `antithesis/cardano_amaru_adversarial/SUBMIT-RUNBOOK.md`

**Step 1: Write the regression record**

Record run `bb2ba66e1d863efbad2e0666e27d9276-59-13`, MOOG test-run `d259913bb4cd23eca820d4fd9fa9f000ce05fe8b2897e10dc53cbfb3729a5984`, the slot-1189 hash, repeated `intersect not found`, absence of `dwarf_served_mutated_block`, the oracle catalog warning, the 0/0 command property, and the tracer SDK lock. Do not include credentials or downloaded raw logs containing environment values.

**Step 2: Add the explicit validity rule**

Add this rule to both run documents:

```text
A mixed run is invalid unless both Amaru relays advance beyond the baked tip,
DWARF serves a mutated block, and the target records a decoder outcome.
```

**Step 3: Verify identifiers and safety**

Run:

```bash
grep -RIn 'bb2ba66e\|d259913b\|slot 1189\|dwarf_served_mutated_block' \
  reports/amaru-mixed-harness-vacuity-2026-08-23 \
  antithesis/cardano_amaru_adversarial/{RUN-DESIGN.md,SUBMIT-RUNBOOK.md}
find reports/amaru-mixed-harness-vacuity-2026-08-23 -name '.env' -o -name '*.skey' -o -name '._*'
```

Expected: the first command finds all regression markers; the second prints nothing.

**Step 4: Commit**

```bash
git add reports/amaru-mixed-harness-vacuity-2026-08-23/README.md \
  antithesis/cardano_amaru_adversarial/RUN-DESIGN.md \
  antithesis/cardano_amaru_adversarial/SUBMIT-RUNBOOK.md
git commit -m "docs: record mixed Amaru harness vacuity"
```

### Task 2: Prove the baked reference and Amaru stores share usable lineage

**Files:**
- Create: `antithesis/cardano_amaru_adversarial/tools/preflight-mixed-lineage.sh`
- Modify: `antithesis/cardano_amaru_adversarial/workload/tests/test_bundle_contract.py`

**Step 1: Write a failing contract test**

Add a test requiring `tools/preflight-mixed-lineage.sh` to contain the canonical endpoint `cardano-phase1-reference.example:3901`, the baked baseline `1189`, and checks for `intersect not found`, `tip.adopt`, `dwarf_served_mutated_block`, and a decoder-error marker.

**Step 2: Run the test and confirm failure**

```bash
python3 -m unittest antithesis.cardano_amaru_adversarial.workload.tests.test_bundle_contract.BundleContractTests.test_mixed_lineage_preflight_is_present -v
```

Expected: FAIL because the script does not exist.

**Step 3: Implement the isolated preflight**

The script must:

- create a unique Compose project name;
- start the reference node, DWARF, and both Amaru relays using fresh named volumes;
- override peer endpoints to the reference node without modifying persistent host state;
- wait with a bounded timeout;
- capture `docker compose logs` into a temporary directory created with `mktemp -d`;
- fail if either relay logs `intersect not found` for the reference peer;
- require a `tip.adopt` slot greater than 1189 on the control;
- require `dwarf_served_mutated_block` from DWARF;
- require a target decoder rejection/outcome marker;
- always run `docker compose down -v` through a trap.

Use `grep`, `awk`, and Compose only; do not read `.env` into output. Support `PREFLIGHT_TIMEOUT_SECS`, defaulting to 180.

**Step 4: Run the contract test**

Run the command from Step 2.

Expected: PASS.

**Step 5: Execute the lineage preflight**

```bash
cd antithesis/cardano_amaru_adversarial
INTERNAL_NETWORK=false PREFLIGHT_TIMEOUT_SECS=180 ./tools/preflight-mixed-lineage.sh
```

Expected output includes all of:

```text
PASS reference intersection
PASS control Amaru advanced beyond slot 1189
PASS DWARF served a mutated block
PASS target Amaru recorded decoder outcome
```

If the reference intersection or later-block condition fails, stop this plan. Document the evidence and create a replacement design for a shared baked producer/reference chain. Do not continue to Compose rewiring.

**Step 6: Commit**

```bash
git add antithesis/cardano_amaru_adversarial/tools/preflight-mixed-lineage.sh \
  antithesis/cardano_amaru_adversarial/workload/tests/test_bundle_contract.py
git commit -m "test: gate mixed Amaru chain lineage"
```

### Task 3: Rewire the mixed block path to the canonical reference chain

**Files:**
- Modify: `antithesis/cardano_amaru_adversarial/docker-compose.yaml:110-225`
- Modify: `antithesis/cardano_amaru_adversarial/workload/tests/test_bundle_contract.py`

**Step 1: Write failing topology tests**

Add tests that parse the rendered Compose YAML as text and require:

```text
dwarf-adversary --upstream cardano-phase1-reference.example:3901
amaru-relay-1 AMARU_PEER_ADDRESS cardano-phase1-reference.example:3901,dwarf-adversary.example:3001
amaru-relay-2 AMARU_PEER_ADDRESS cardano-phase1-reference.example:3901
```

Also assert that neither Amaru peer variable nor the DWARF upstream contains `relay1.example` or `relay2.example`.

**Step 2: Run tests and confirm failure**

```bash
python3 -m unittest antithesis.cardano_amaru_adversarial.workload.tests.test_bundle_contract -v
```

Expected: the new canonical-lineage tests FAIL.

**Step 3: Apply the minimal Compose change**

Change only the three peer/upstream values and their corresponding `depends_on` entries. Make DWARF depend on `cardano-phase1-reference`; make each Amaru relay depend on its canonical honest source and, for the target, DWARF. Keep the live Cardano cluster and its volumes unchanged.

**Step 4: Render and test**

```bash
INTERNAL_NETWORK=false docker compose -f antithesis/cardano_amaru_adversarial/docker-compose.yaml config >/tmp/dwarf-mixed-compose.yaml
python3 -m unittest antithesis.cardano_amaru_adversarial.workload.tests.test_bundle_contract -v
grep -n 'cardano-phase1-reference.example:3901' /tmp/dwarf-mixed-compose.yaml
```

Expected: tests PASS; rendered Compose shows the endpoint three times; no interpolation or dependency errors.

**Step 5: Re-run the real lineage smoke**

Run Task 2 Step 5 again without overrides that mask repository Compose values.

Expected: all four reachability gates PASS.

**Step 6: Commit**

```bash
git add antithesis/cardano_amaru_adversarial/docker-compose.yaml \
  antithesis/cardano_amaru_adversarial/workload/tests/test_bundle_contract.py
git commit -m "fix: align mixed Amaru peers on reference chain"
```

### Task 4: Make the oracle state machine and coverage properties testable

**Files:**
- Modify: `antithesis/cardano_amaru_adversarial/oracle/oracle.py`
- Create: `antithesis/cardano_amaru_adversarial/oracle/test_oracle.py`

**Step 1: Write failing parser/state tests**

Cover both supported `tip.adopt` formats, log truncation, control advancement beyond slot 1189, target advancement, decoder rejection, equal-height hash disagreement, ahead-of-control violation, and fatal detection. Use temporary files and an injected assertion recorder; do not sleep in tests.

The state transition API should be:

```python
state = OracleState(baked_slot=1189, ahead_margin=3)
events = state.consume(honest_text=honest_text, adversarial_text=adversarial_text)
```

where `events` contains booleans and violation detail objects, not direct SDK calls.

**Step 2: Run tests and confirm failure**

```bash
python3 -m unittest antithesis/cardano_amaru_adversarial/oracle/test_oracle.py -v
```

Expected: FAIL because `OracleState` does not exist.

**Step 3: Extract the pure state machine**

Keep SDK calls in a small `emit_properties(state, events)` adapter. On every poll, call the coverage assertion sites with their current true/false state so cataloged properties cannot disappear. Use literal property names:

```text
honest amaru relay advances beyond baked tip
adversarial amaru relay advances beyond baked tip
adversarial amaru relay rejected a forged block at decode
adversarial amaru relay never adopts a forged fork
adversarial amaru relay never advances ahead of the honest chain
adversarial amaru relay does not panic on forged input
honest amaru relay does not panic
```

Do not infer that a missing decoder error is safety. It is a failed coverage property.

**Step 4: Run oracle tests**

Run Step 2 again.

Expected: PASS with no sleeps or network access.

**Step 5: Run all Python tests**

```bash
python3 -m unittest discover -s antithesis/cardano_amaru_adversarial -p 'test*.py' -v
```

Expected: all tests PASS.

**Step 6: Commit**

```bash
git add antithesis/cardano_amaru_adversarial/oracle/oracle.py \
  antithesis/cardano_amaru_adversarial/oracle/test_oracle.py
git commit -m "test: make mixed Amaru oracle non-vacuous"
```

### Task 5: Install a reproducible oracle assertion catalog

**Files:**
- Modify: `antithesis/cardano_amaru_adversarial/oracle/Dockerfile`
- Modify: `antithesis/cardano_amaru_adversarial/workload/tests/test_bundle_contract.py`

**Step 1: Write failing image-contract tests**

Require the oracle Dockerfile to use a digest-pinned Python 3.11 base, install exactly `antithesis==0.2.0`, omit `|| true`, copy `oracle.py` to `/oracle.py`, and copy it separately into `/opt/antithesis/catalog/oracle.py`.

**Step 2: Confirm failure**

```bash
python3 -m unittest antithesis.cardano_amaru_adversarial.workload.tests.test_bundle_contract.BundleContractTests.test_oracle_image_catalogs_pinned_assertions -v
```

Expected: FAIL against the current permissive Dockerfile.

**Step 3: Mirror the workload image contract**

Use the same digest-pinned Python base and SDK version already proven in `workload/Dockerfile`. Keep `PYTHONUNBUFFERED=1` and the existing entrypoint.

**Step 4: Build and inspect locally**

```bash
docker build -f antithesis/cardano_amaru_adversarial/oracle/Dockerfile \
  -t dwarf-adversarial-oracle:full-repair \
  antithesis/cardano_amaru_adversarial/oracle
docker run --rm --entrypoint python dwarf-adversarial-oracle:full-repair \
  -c 'import antithesis; import pathlib; assert pathlib.Path("/opt/antithesis/catalog/oracle.py").is_file()'
```

Expected: build succeeds and the inspection exits 0.

**Step 5: Run the contract suite**

```bash
python3 -m unittest antithesis.cardano_amaru_adversarial.workload.tests.test_bundle_contract -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add antithesis/cardano_amaru_adversarial/oracle/Dockerfile \
  antithesis/cardano_amaru_adversarial/workload/tests/test_bundle_contract.py
git commit -m "fix: package mixed oracle assertion catalog"
```

### Task 6: Correct phase-1 command assertion semantics

**Files:**
- Modify: `antithesis/cardano_amaru_adversarial/workload/mixed_phase1.py:20-35,527-539`
- Modify: `antithesis/cardano_amaru_adversarial/workload/test/v1/mixed-phase1/*.py`
- Modify: `antithesis/cardano_amaru_adversarial/workload/tests/test_mixed_phase1.py`

**Step 1: Write failing assertion-recorder tests**

Assert that `report_command_error("example", ValueError("bad"))` emits:

```python
unreachable("mixed phase-1 command error", details)
```

and never calls `always(False, ...)`. Add `report_command_success(command)` and test that each command calls it only after completing its main body.

**Step 2: Confirm failure**

```bash
python3 -m unittest antithesis.cardano_amaru_adversarial.workload.tests.test_mixed_phase1 -v
```

Expected: the new semantic tests FAIL.

**Step 3: Implement the minimal assertion changes**

Import `unreachable` with the other SDK assertions and add a local fallback. Replace the old `always(False)` call. Emit a reachable property named `mixed phase-1 test command completed` with the command name after each successful command body.

**Step 4: Run workload tests**

Run Step 2 again.

Expected: PASS; no source contains `mixed phase-1 test commands complete without error`.

**Step 5: Verify command scripts compile and retain execute bits**

```bash
python3 -m py_compile antithesis/cardano_amaru_adversarial/workload/mixed_phase1.py \
  antithesis/cardano_amaru_adversarial/workload/test/v1/mixed-phase1/*.py
git ls-files -s antithesis/cardano_amaru_adversarial/workload/test/v1/mixed-phase1/*.py
```

Expected: compile succeeds; every command remains mode `100755`.

**Step 6: Commit**

```bash
git add antithesis/cardano_amaru_adversarial/workload
git commit -m "fix: report mixed workload command failures correctly"
```

### Task 7: Establish and enforce the real MOOG fault scope

**Files:**
- Create: `antithesis/cardano_amaru_adversarial/tools/audit-moog-fault-scope.py`
- Modify: `antithesis/cardano_amaru_adversarial/workload/tests/test_bundle_contract.py`
- Modify: `antithesis/cardano_amaru_adversarial/SUBMIT-RUNBOOK.md`

**Step 1: Write a failing audit-script contract test**

Require the script to define the allowed SUT service set and forbidden infrastructure set. Forbidden targets include configurator, tracer, tracer-sidecar, log-tailer, sidecar, cardano-submit-api, dwarf-oracle, and mixed-phase1-workload. Do not assume Compose service names equal runtime container names; normalize project prefixes and numeric suffixes.

**Step 2: Confirm failure**

Run the bundle contract suite.

Expected: FAIL because the audit script is absent.

**Step 3: Implement an NDJSON fault-event auditor**

The script accepts NDJSON on stdin or `--input FILE`, extracts fault action and target fields, normalizes container names, prints the observed target set, and exits nonzero if a forbidden service was targeted. It must support fixtures shaped like the MOOG `antithesis events` output already captured from the completed run.

**Step 4: Add unit fixtures**

Create sanitized inline test data for one allowed node fault and the known forbidden `antithesis-tracer-sidecar-1` fault. Assert allowed input exits 0 and forbidden input exits nonzero.

**Step 5: Inspect the deployed MOOG launch path read-only**

Use MOOG release `0.5.1.3`, never `moog-head`. Inspect the deployed agent configuration and launch payload construction without printing secrets. Determine whether fault exclusions are tenant configuration, launch request fields, or unsupported repository metadata. Record the exact supported mechanism in `SUBMIT-RUNBOOK.md`.

If access does not reveal a supported exclusion mechanism, stop and request confirmation from the `amaru-cardano` tenant/FDE. Do not replace the existing label with guessed syntax.

**Step 6: Audit the completed run as the negative control**

```bash
moog antithesis events --run-id bb2ba66e1d863efbad2e0666e27d9276-59-13 --q fault --no-pretty \
  | python3 antithesis/cardano_amaru_adversarial/tools/audit-moog-fault-scope.py
```

Expected: nonzero, identifying tracer-sidecar and any other forbidden infrastructure targets.

**Step 7: Commit**

```bash
git add antithesis/cardano_amaru_adversarial/tools/audit-moog-fault-scope.py \
  antithesis/cardano_amaru_adversarial/workload/tests/test_bundle_contract.py \
  antithesis/cardano_amaru_adversarial/SUBMIT-RUNBOOK.md
git commit -m "test: audit Antithesis fault target scope"
```

### Task 8: Make the tracer path restart-safe or replace it

**Files:**
- Modify: `antithesis/cardano_amaru_adversarial/docker-compose.yaml:235-275`
- Create if wrapping is required: `antithesis/cardano_amaru_adversarial/tracer-sidecar-image/Dockerfile`
- Create if wrapping is required: `antithesis/cardano_amaru_adversarial/tracer-sidecar-image/entrypoint.sh`
- Modify: `antithesis/cardano_amaru_adversarial/workload/tests/test_bundle_contract.py`

**Step 1: Reproduce the restart failure locally**

Start the tracer pipeline in an isolated Compose project, force-stop and restart `tracer-sidecar`, and capture whether it emits `resource busy (file is locked)` or exits nonzero.

Expected against the current image: the known failure reproduces. If it does not reproduce locally, retain the Antithesis lifecycle event as the regression fixture and test the replacement under repeated restarts.

**Step 2: Select the smallest verified repair**

Preference order:

1. Pin a newer upstream tracer-sidecar digest proven restart-safe.
2. Configure a documented per-process or reopen-safe SDK sink supported by the image.
3. Build a thin owned wrapper that retries only the known transient lock condition with bounded backoff and otherwise preserves the child exit code.

Do not delete a locked SDK file and do not suppress arbitrary exit failures.

**Step 3: Add failing restart contract tests**

Require a literal digest-pinned image. If a wrapper is used, test that it retries the exact lock error, stops retrying after the configured bound, and propagates unrelated failures.

**Step 4: Implement and run a restart storm**

Perform at least 20 stop/start cycles for tracer-sidecar, oracle, workload, and submit API. After each cycle verify the service stays running and logs contain neither the lock error nor assertion-catalog warnings.

Expected: 20/20 clean cycles.

**Step 5: Commit**

```bash
git add antithesis/cardano_amaru_adversarial/docker-compose.yaml \
  antithesis/cardano_amaru_adversarial/tracer-sidecar-image \
  antithesis/cardano_amaru_adversarial/workload/tests/test_bundle_contract.py
git commit -m "fix: make mixed tracer restarts safe"
```

Omit the nonexistent wrapper directory from `git add` if an upstream digest/configuration fixes the issue.

### Task 9: Build, publish, and pin repaired images safely

**Files:**
- Modify: `antithesis/cardano_amaru_adversarial/docker-compose.yaml`
- Modify: `antithesis/cardano_amaru_adversarial/SUBMIT-RUNBOOK.md`

**Step 1: Run the complete local suite**

```bash
python3 -m unittest discover -s antithesis/cardano_amaru_adversarial -p 'test*.py' -v
INTERNAL_NETWORK=false docker compose -f antithesis/cardano_amaru_adversarial/docker-compose.yaml config >/tmp/dwarf-mixed-compose.yaml
antithesis/cardano_amaru_adversarial/fixture/verify-corpus-container.sh
```

Expected: all tests PASS, Compose renders, and all five corpus transactions verify.

**Step 2: Run both runtime smokes**

Run the mixed-lineage preflight and the restart storm from Tasks 2 and 8.

Expected: every reachability and restart gate passes.

**Step 3: Scan image contexts before authentication**

```bash
find antithesis/cardano_amaru_adversarial \
  \( -name '.env' -o -name '*.skey' -o -name '*.pem' -o -name '._*' \) -print
git status --short
```

Expected: no sensitive/image-context match. The repository-level untracked `.env` remains unadded.

**Step 4: Build versioned images**

Build the oracle and, only if required, tracer wrapper with date/version tags. Inspect their filesystem and labels locally.

**Step 5: Authenticate without exposing the PAT**

Read `/home/nigel/moog-secrets/ghcr.token` only through `docker login --password-stdin`. Never place it in argv or output.

**Step 6: Push, obtain immutable digests, and verify anonymous access**

Push versioned tags, resolve their `sha256:` digests, log out, then request manifests using anonymous GHCR pull tokens. Expected HTTP status: 200 for every custom image.

**Step 7: Pin literal digests and rerun tests**

Replace only the affected Compose image lines with literal digest references. Do not introduce `${...}` image interpolation. Repeat Step 1.

**Step 8: Commit**

```bash
git add antithesis/cardano_amaru_adversarial/docker-compose.yaml \
  antithesis/cardano_amaru_adversarial/SUBMIT-RUNBOOK.md
git commit -m "build: pin repaired mixed Antithesis images"
```

### Task 10: Final no-paid-run release gate

**Files:**
- Modify: `antithesis/cardano_amaru_adversarial/SUBMIT-RUNBOOK.md`
- Modify: `reports/README.md`

**Step 1: Run final verification from a clean checkout state**

```bash
git status --short
python3 -m unittest discover -s antithesis/cardano_amaru_adversarial -p 'test*.py' -v
INTERNAL_NETWORK=false docker compose -f antithesis/cardano_amaru_adversarial/docker-compose.yaml config --quiet
antithesis/cardano_amaru_adversarial/tools/preflight-mixed-lineage.sh
find . -name '._*' -print
git grep -nE 'ghp_|github_pat_|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|antithesisPassword'
```

Expected: only the intentionally untracked `.env` appears in status; all tests and preflight pass; no `._*` or secret pattern is found.

**Step 2: Record the acceptance matrix**

Document PASS/FAIL and evidence locations for:

- reference/Amaru intersection;
- control and target slot greater than 1189;
- `dwarf_served_mutated_block`;
- target decoder outcome;
- oracle catalog availability;
- command assertion correction;
- 20-cycle infrastructure restart storm;
- identified tenant fault-exclusion mechanism;
- anonymous image access;
- public-safety scan.

Every row must be PASS. `UNKNOWN` blocks submission.

**Step 3: Commit the release-gate record**

```bash
git add antithesis/cardano_amaru_adversarial/SUBMIT-RUNBOOK.md reports/README.md
git commit -m "docs: gate repaired mixed Amaru Antithesis run"
```

**Step 4: Stop before launch**

Report the final commit SHA and evidence matrix to the user. Do not push or invoke `moog request-test` until the user separately approves publication and a paid run.
