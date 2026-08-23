# Mixed Cardano/Amaru Phase-1 Fee Corpus Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an Antithesis-steerable, correctly signed fee-boundary corpus that compares Cardano node and Amaru at `minimum + {-100,-2,-1,0,+1}` while preserving fault tolerance and public safety.

**Architecture:** Keep the existing block-216 Cardano reference and baked Amaru store. Package a manifest plus five public signed transactions; run accepted cases once before faults and repeatedly select only rejected, idempotent cases during faults. Preserve the existing `-1` commands and property names for historical continuity.

**Tech Stack:** Python 3.11, `antithesis` Python SDK, `unittest`, Cardano CLI 10.16, Docker Compose v2, snouty 0.6.1.

---

### Task 1: Commit the approved design and plan

**Files:**
- Create: `docs/plans/2026-08-22-mixed-phase1-fee-corpus-design.md`
- Create: `docs/plans/2026-08-22-mixed-phase1-fee-corpus.md`

**Step 1:** Add both approved documents.

**Step 2:** Verify that neither document contains a signing key, token, `.env` value, or private registry credential.

**Step 3:** Commit only the two documents with `docs: design mixed phase1 fee corpus`.

### Task 2: Specify the corpus loader and expected-outcome model

**Files:**
- Modify: `antithesis/cardano_amaru_adversarial/workload/tests/test_mixed_phase1.py`
- Modify: `antithesis/cardano_amaru_adversarial/workload/mixed_phase1.py`

**Step 1: Write failing tests**

Add tests proving that `load_corpus(root)`:

- loads five uniquely named cases from `corpus.json`;
- validates `actual_fee - minimum_fee == fee_delta`;
- accepts only `accepted` or `phase1_reject` expectations;
- rejects duplicate IDs, traversal paths, malformed transaction IDs, and missing files;
- preserves the existing `load_fixture(root)` behavior for `underfee.tx`.

Add a test for `matches_expected(result, expected)` covering accepted agreement,
phase-1 rejection agreement, disagreement, and unavailable observations.

**Step 2: Run tests and verify RED**

Run:

```bash
python3 -m unittest \
  antithesis.cardano_amaru_adversarial.workload.tests.test_mixed_phase1 -v
```

Expected: failures because `load_corpus` and `matches_expected` do not exist.

**Step 3: Implement the minimum model**

Extend `Fixture` with `case_id`, `fee_delta`, and `expected`. Add strict manifest
loading and the expected-outcome comparison without changing response classification.

**Step 4: Run tests and verify GREEN**

Run the focused module, then the full workload suite. Expected: all pass.

**Step 5:** Commit with `test: model mixed phase1 fee corpus`.

### Task 3: Generate and verify the signed fixture corpus

**Files:**
- Create: `antithesis/cardano_amaru_adversarial/fixture/static/corpus.json`
- Create: `antithesis/cardano_amaru_adversarial/fixture/static/underfee-minus-100.tx`
- Create: `antithesis/cardano_amaru_adversarial/fixture/static/underfee-minus-2.tx`
- Create: `antithesis/cardano_amaru_adversarial/fixture/static/minimum-exact.tx`
- Create: `antithesis/cardano_amaru_adversarial/fixture/static/minimum-plus-1.tx`
- Modify: `antithesis/cardano_amaru_adversarial/workload/tests/test_bundle_contract.py`

**Step 1: Write the failing contract test**

Require a five-case manifest, five envelope files including the existing
`underfee.tx`, only the deltas `[-100,-2,-1,0,1]`, distinct inputs for the two
accepted cases, and no `*.skey` files anywhere in the bundle.

**Step 2: Run the contract test and verify RED**

Expected: missing manifest and fixture failures.

**Step 3: Generate fixtures in a private temporary directory**

Use the read-only historical configurator key set and the retained reference node.
For each transaction, iterate `build-raw` and `calculate-min-fee` until the calculated
fee is stable, rebuild at the requested delta, sign once, and record only the public
envelope and metadata. Never print signing-key contents.

**Step 4: Independently verify generated artifacts**

Use `cardano-cli debug transaction view` and `transaction txid` to check input,
output, fee, signature presence, transaction ID, and distinct accepted-case inputs.

**Step 5: Remove the private temporary key directory**

Delete only `/tmp/dwarf-corpus-key-20260822` after explicitly verifying the path.

**Step 6: Run tests and verify GREEN**

Expected: the focused contract test and full workload suite pass.

**Step 7:** Commit public artifacts with `test: add signed phase1 fee corpus`.

### Task 4: Add granular Antithesis commands and assertions

**Files:**
- Modify: `antithesis/cardano_amaru_adversarial/workload/mixed_phase1.py`
- Create: `antithesis/cardano_amaru_adversarial/workload/test/v1/mixed-phase1/first_fee_valid_boundaries.py`
- Create: `antithesis/cardano_amaru_adversarial/workload/test/v1/mixed-phase1/parallel_driver_underfee_corpus.py`
- Modify: `antithesis/cardano_amaru_adversarial/workload/test/v1/mixed-phase1/eventually_underfee_recovery.py`
- Modify: `antithesis/cardano_amaru_adversarial/workload/tests/test_mixed_phase1.py`
- Modify: `antithesis/cardano_amaru_adversarial/workload/tests/test_bundle_contract.py`

**Step 1: Write failing tests**

Test Antithesis-random selection from only negative cases, readiness probing before
accepted submissions, exactly-once valid-case dispatch, case-rich assertion details,
and command filename/executable contracts.

**Step 2: Run tests and verify RED**

Expected: missing command/helper failures.

**Step 3: Implement minimum command behavior**

Use `antithesis.random.random_choice` at the decision point. Keep the existing `-1`
driver and assertion messages unchanged. Make every command bounded and exit zero;
never emit lifecycle events from test commands.

**Step 4: Run focused and full tests and verify GREEN**

Expected: all workload tests pass with no warnings.

**Step 5:** Commit with `test: exercise phase1 fee corpus in Antithesis`.

### Task 5: Update research, runbook, and image contracts

**Files:**
- Modify: `antithesis/cardano_amaru_adversarial/scratchbook/property-catalog.md`
- Modify: `antithesis/cardano_amaru_adversarial/scratchbook/property-relationships.md`
- Modify: `antithesis/cardano_amaru_adversarial/scratchbook/properties/phase1-underfee-admission-agreement.md`
- Modify: `antithesis/cardano_amaru_adversarial/RUN-DESIGN.md`
- Modify: `antithesis/cardano_amaru_adversarial/SUBMIT-RUNBOOK.md`
- Modify: `antithesis/cardano_amaru_adversarial/README.md`
- Modify: `antithesis/cardano_amaru_adversarial/workload/Dockerfile`
- Modify: `antithesis/cardano_amaru_adversarial/docker-compose.yaml`

**Step 1:** Update provenance to the implementation commit and record the completed
Antithesis run as validation of the original fixture.

**Step 2:** Document the new menu, exactly-once accepted cases, negative-case replay,
assertion meanings, and image rebuild/publish procedure.

**Step 3:** Build the workload image, smoke it locally, publish it, then pin Compose
to the resulting immutable digest. Do not place registry credentials in tracked
files or command output.

**Step 4:** Commit with `docs: document mixed phase1 fee corpus`.

### Task 6: Full verification and public handoff

**Files:**
- Modify only if verification exposes a defect.

**Step 1:** Run all workload unit and bundle tests.

**Step 2:** Render Compose with `INTERNAL_NETWORK=false docker compose ... config --quiet`.

**Step 3:** Run official `/home/nigel/.local/bin/snouty validate ... --timeout 180`.

**Step 4:** Scan the exact Git diff and tracked tree for signing-key markers,
credentials, `.env`, absolute temporary paths, `._*`, and oversized unintended files.

**Step 5:** Verify `git status --short` contains only the intentionally untracked
local `.env`, and verify every intended commit is based on the public head.

**Step 6:** Prepare a minimal archive or push instructions. Do not launch a paid run
until the public commit is pushed and the user requests submission.
