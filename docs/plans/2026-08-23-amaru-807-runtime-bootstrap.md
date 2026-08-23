# Amaru 807 Runtime Bootstrap Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build and locally prove a reproducible Amaru 807 custom-testnet bootstrap package, then add genuinely new lifecycle fault coverage to the additive mixed Cardano/Amaru/DWARF scenario.

**Architecture:** A single source-pinned image contains patched Amaru 807 and the pinned Cardano `db-analyser`. Its bootstrap mode snapshots a writable copy of the live Cardano ChainDB, creates three explicit epoch snapshots, and emits native schema-v5 Amaru stores. The same binary runs the two Amaru relays, eliminating schema drift; Antithesis lifecycle coverage is added only after the baseline mixed runtime passes locally.

**Tech Stack:** Docker multi-stage builds, Docker Compose, Python 3.11, `unittest`, Cardano node/db-analyser 10.7.1, Rust/Amaru `493bffba`, Antithesis Python SDK 0.2.0.

---

### Task 1: Freeze bootstrap source and patch provenance

**Files:**
- Create: `antithesis/cardano_amaru_dwarf_runtime/bootstrap-image/SOURCE.lock`
- Create: `antithesis/cardano_amaru_dwarf_runtime/bootstrap-image/patches/0001-local-custom-bootstrap.patch`
- Create: `antithesis/cardano_amaru_dwarf_runtime/bootstrap-image/patches/0002-tvar-definite-map.patch`
- Create: `antithesis/cardano_amaru_dwarf_runtime/bootstrap-image/patches/0003-custom-global-parameters.patch`
- Modify: `antithesis/cardano_amaru_dwarf_runtime/tests/test_bundle_contract.py`

**Step 1: Write the failing contract test**

Require `SOURCE.lock` to name Amaru commit `493bffba0cc4db2291643cdd6698197c374958b3`, require exactly three patch files, and require each patch to contain its expected source path.

**Step 2: Run the test and verify RED**

Run: `python3 -m unittest antithesis/cardano_amaru_dwarf_runtime/tests/test_bundle_contract.py -v`

Expected: failure because `bootstrap-image/` does not exist.

**Step 3: Add the locked source metadata and exact proven patches**

Copy the three non-sensitive diffs from `reports/amaru-custom-testnet-bootstrap-wall-evidence/amaru-custom-bootstrap-patches.diff`, splitting them by source file without changing patch content. Record the upstream repository, full commit, release tag, and expected patched-binary version in `SOURCE.lock`.

**Step 4: Run the test and verify GREEN**

Run the same unittest command; expect all contract tests to pass.

**Step 5: Commit only Task 1 files**

```bash
git add antithesis/cardano_amaru_dwarf_runtime/bootstrap-image \
  antithesis/cardano_amaru_dwarf_runtime/tests/test_bundle_contract.py
git commit -m "build: lock patched Amaru 807 bootstrap source"
```

### Task 2: Implement deterministic snapshot-point resolution

**Files:**
- Create: `antithesis/cardano_amaru_dwarf_runtime/bootstrap-image/resolve_snapshot_points.py`
- Create: `antithesis/cardano_amaru_dwarf_runtime/bootstrap-image/test_resolve_snapshot_points.py`
- Create: `antithesis/cardano_amaru_dwarf_runtime/bootstrap-image/fixtures/db-analyser-slots.txt`

**Step 1: Capture a real analyser fixture**

Copy the retained `p1` proof volume into a disposable writable volume and run:

```bash
db-analyser --db /scratch --show-slot-block-no --in-mem --config /cfg/configs/config.json
```

Store a small public excerpt spanning at least four epoch boundaries. Do not include keys or configuration secrets.

**Step 2: Write failing resolver tests**

Tests must prove that the resolver:

- accepts the real analyser format;
- selects the last block and its immediate parent for epochs 0, 1, and 2;
- emits exactly `slot.hash::parent_slot.parent_hash` in increasing epoch order;
- rejects missing parents, duplicate/conflicting slots, origin-only input, malformed hashes, and fewer than three completed epochs.

**Step 3: Run and verify RED**

Run: `python3 -m unittest bootstrap-image/test_resolve_snapshot_points.py -v`

Expected: import or missing-function failure.

**Step 4: Implement the minimal pure parser/resolver**

Keep parsing and selection free of Docker or subprocess logic. Expose `parse_rows(text)` and `select_points(rows, epoch_length, count=3)` plus a CLI that writes one point per line.

**Step 5: Run and verify GREEN**

Run the resolver unittest; expect all cases to pass.

**Step 6: Commit**

```bash
git add antithesis/cardano_amaru_dwarf_runtime/bootstrap-image
git commit -m "feat: resolve Amaru bootstrap snapshot points"
```

### Task 3: Build the reproducible combined image

**Files:**
- Create: `antithesis/cardano_amaru_dwarf_runtime/bootstrap-image/Dockerfile`
- Create: `antithesis/cardano_amaru_dwarf_runtime/bootstrap-image/test_image_contract.py`
- Create: `antithesis/cardano_amaru_dwarf_runtime/bootstrap-image/.dockerignore`

**Step 1: Write failing Dockerfile contract tests**

Assert that the Dockerfile:

- checks out the full locked Amaru commit before applying patches;
- fails if any patch does not apply cleanly;
- builds `amaru` in release mode;
- uses the exact Cardano 10.7.1 digest for the runtime stage;
- copies `amaru` and the resolver into fixed paths;
- contains no credentials, mutable Amaru tag, `|| true`, or host-built binary copy.

**Step 2: Run and verify RED**

Run: `python3 -m unittest bootstrap-image/test_image_contract.py -v`

**Step 3: Implement the Dockerfile**

Use a digest-pinned Rust builder, clone only the locked Amaru source, checkout the commit, run `git apply --check` and `git apply` for all three patches, and build the release binary. Copy `db-analyser` and its complete Nix runtime closure from the pinned Cardano image into a digest-pinned Python runtime stage. That supplies the Task 4 controller without changing the producer toolchain.

**Step 4: Run unit tests, then build**

```bash
python3 -m unittest bootstrap-image/test_image_contract.py -v
docker build -t dwarf/amaru-807-custom-bootstrap:verify bootstrap-image
```

Expected: tests pass; Docker build exits 0.

**Step 5: Inspect image identity**

Verify `/usr/local/bin/amaru --version` reports commit `493bffb`, `db-analyser` exists, and none of the build-context fixtures or `.git` data appear in the final image.

**Step 6: Commit**

```bash
git add antithesis/cardano_amaru_dwarf_runtime/bootstrap-image
git commit -m "build: add Amaru 807 custom bootstrap image"
```

### Task 4: Implement fail-closed runtime bootstrap orchestration

**Files:**
- Create: `antithesis/cardano_amaru_dwarf_runtime/bootstrap-image/bootstrap.py`
- Create: `antithesis/cardano_amaru_dwarf_runtime/bootstrap-image/test_bootstrap.py`
- Modify: `antithesis/cardano_amaru_dwarf_runtime/bootstrap-image/Dockerfile`

**Step 1: Write failing orchestration tests with fake executables**

Cover these state transitions:

- immature copied chain returns the documented retry exit code;
- analyser failure preserves stderr and exits non-zero;
- exactly three resolved points are passed as repeated `--snapshot` arguments;
- `snapshot create` receives local DB/config directories;
- `node bootstrap` receives the same network, epoch, global parameters, and era history;
- completion is refused unless both schema-v5 stores, era history, and three archives exist;
- output is committed atomically and the ready marker is written last;
- a complete existing output is idempotent.

**Step 2: Run and verify RED**

Run: `python3 -m unittest bootstrap-image/test_bootstrap.py -v`

**Step 3: Implement bootstrap.py**

Use `subprocess.run(..., check=True)` with argv arrays, bounded output, explicit directories, and no shell interpolation. Treat only immature-history and live-copy races as retryable. All schema, parser, snapshot, and import failures must fail the container.

**Step 4: Run and verify GREEN**

Run all `bootstrap-image/test_*.py` tests.

**Step 5: Exercise the image against retained proof volumes**

Mount `p1` and configs read-only, use fresh scratch/output volumes, and require a schema-v5 bundle and ready marker. Run the command twice to prove idempotency.

**Step 6: Commit**

```bash
git add antithesis/cardano_amaru_dwarf_runtime/bootstrap-image
git commit -m "feat: bootstrap native Amaru 807 stores"
```

### Task 5: Integrate the compatible image into the additive Compose package

**Files:**
- Modify: `antithesis/cardano_amaru_dwarf_runtime/docker-compose.yaml`
- Modify: `antithesis/cardano_amaru_dwarf_runtime/tests/test_bundle_contract.py`
- Modify: `antithesis/cardano_amaru_dwarf_runtime/README.md`

**Step 1: Write failing Compose contract tests**

Require the new literal image name for bootstrap and both relays, the v5 ready-marker gate, independent relay state volumes, no `--migrate-chain-db`, and absence of the historical `cf657b91` runtime image except nowhere in the final Compose.

**Step 2: Run and verify RED**

Run the bundle contract suite; expect the new assertions to fail.

**Step 3: Replace the old bootstrap flow**

Wire the combined image into `bootstrap-producer`, `amaru-relay-1`, and `amaru-relay-2`. Keep the live `p1` mount read-only, scratch writable, output atomic, and relay copies private. Remove the temporary non-launchable migration configuration.

**Step 4: Validate Compose and resolved fault targets**

```bash
INTERNAL_NETWORK=false docker compose config --quiet
INTERNAL_NETWORK=false docker compose config --format json | python3 tests/check_fault_targets.py
```

Expected: only `amaru-relay-1` and `amaru-relay-2` are fault-eligible; excluded service container names equal service keys.

**Step 5: Commit**

```bash
git add antithesis/cardano_amaru_dwarf_runtime
git commit -m "feat: wire native Amaru 807 runtime bootstrap"
```

### Task 6: Prove the complete mixed runtime from empty volumes

**Files:**
- Create: `antithesis/cardano_amaru_dwarf_runtime/tests/prove_runtime.py`
- Create: `antithesis/cardano_amaru_dwarf_runtime/tests/test_prove_runtime.py`
- Create: `antithesis/cardano_amaru_dwarf_runtime/proof/README.md`

**Step 1: Write failing proof-runner unit tests**

Test JSON parsing and condition-based waits for bootstrap exit 0, relay adoption, epoch crossing, decoder rejection, consumer advancement, producer-tip match, panic detection, and restart counts. Avoid fixed sleeps in the proof logic.

**Step 2: Run and verify RED, then implement and verify GREEN**

Run: `python3 -m unittest tests/test_prove_runtime.py -v`

**Step 3: Run a clean isolated proof**

Use a unique Compose project and empty named volumes. The proof must fail on any historical reward-discrepancy signature, schema mismatch, missing mutation rejection, stalled relay, or consumer that never advances and matches a producer.

**Step 4: Preserve bounded public evidence**

Record image IDs/digests, bootstrap completion, initial/final relay heights, epoch transition, decoder rejection count, consumer seed/final tip, producer match, container exits/restarts, and exact verification commands. Do not copy full noisy logs, `.env`, keys, or genesis signing material.

**Step 5: Run the full verification suite**

```bash
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s oracle -p 'test_*.py' -v
python3 -m unittest discover -s bootstrap-image -p 'test_*.py' -v
INTERNAL_NETWORK=false docker compose config --quiet
```

**Step 6: Commit**

```bash
git add antithesis/cardano_amaru_dwarf_runtime/tests \
  antithesis/cardano_amaru_dwarf_runtime/proof \
  antithesis/cardano_amaru_dwarf_runtime/README.md
git commit -m "test: prove runtime Cardano Amaru DWARF network"
```

### Task 7: Design and add genuinely new lifecycle fault coverage

**Files:**
- Create: `antithesis/cardano_amaru_dwarf_runtime/lifecycle-workload/Dockerfile`
- Create: `antithesis/cardano_amaru_dwarf_runtime/lifecycle-workload/workload.py`
- Create: `antithesis/cardano_amaru_dwarf_runtime/lifecycle-workload/test_workload.py`
- Modify: `antithesis/cardano_amaru_dwarf_runtime/oracle/oracle.py`
- Modify: `antithesis/cardano_amaru_dwarf_runtime/oracle/test_oracle.py`
- Modify: `antithesis/cardano_amaru_dwarf_runtime/docker-compose.yaml`
- Modify: `antithesis/cardano_amaru_dwarf_runtime/README.md`

**Step 1: Re-check the Antithesis workbench/API contract**

Confirm how the MOOG Amaru tenant exposes lifecycle hooks and native fault scheduling. Do not add Docker-socket control or duplicate platform fault injection.

**Step 2: Write failing workload/oracle tests**

At minimum require non-vacuous evidence for:

- target and control startup observed;
- DWARF rejection observed before and after a target restart/fault cycle;
- target re-adopts after restart without panic or fatal store error;
- isolated consumer resumes advancement after relay recovery;
- coverage details include seed, relay generation/start count, pre/post heights, and mutation evidence.

Do not restore the false historical equal-height hash assertion.

**Step 3: Implement the minimal SDK workload and recovery state machine**

Use Antithesis lifecycle/setup signaling and native platform faults. The workload supplies traffic and evidence; it does not control Docker directly. Keep properties catalogued under the new package only.

**Step 4: Run unit, image, Compose, and local no-fault degradation tests**

Verify the workload remains healthy when no restart occurs locally while its `sometimes` recovery property remains correctly unsatisfied rather than falsely green.

**Step 5: Commit**

```bash
git add antithesis/cardano_amaru_dwarf_runtime
git commit -m "feat: add Amaru lifecycle fault coverage"
```

### Task 8: Prepare publication and submission artifacts without launching

**Files:**
- Modify: `antithesis/cardano_amaru_dwarf_runtime/README.md`
- Create: `antithesis/cardano_amaru_dwarf_runtime/SUBMIT-RUNBOOK.md`

**Step 1: Run sensitive-file and metadata scans**

Reject `.env`, `*.skey`, private-key headers, PAT/API-key patterns, and `._*` files. Confirm Git changes remain under the new package plus approved plan documents.

**Step 2: Build final local images**

Build bootstrap, oracle, and lifecycle workload images from clean contexts and record local digests.

**Step 3: Stop before external publication**

Present the exact new package names and proposed immutable version tags. Publishing, anonymous-pull verification, public Git push, MOOG request, and Antithesis launch require explicit authorization at that point.

**Step 4: Run verification-before-completion**

Re-run all tests, Compose validation, image builds, scope checks, and sensitive-file scans before reporting readiness.
