# Antithesis Relay Entrypoint Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make both Amaru relays start reliably when their entrypoint script is bind-mounted by Antithesis.

**Architecture:** Retain the existing read-only script mount and execute it through `/bin/sh`, avoiding reliance on bind-mounted executable permissions. Lock the deployment contract with a focused test that rejects the direct-exec form seen in the failed run.

**Tech Stack:** Docker Compose, POSIX shell, Python `unittest`, release MOOG 0.5.1.3.

---

### Task 1: Add the failing deployment-contract test

**Files:**
- Modify: `antithesis/cardano_amaru_adversarial/workload/tests/test_bundle_contract.py`

**Step 1: Write the failing test**

Add a test that asserts the rendered Compose source contains exactly two uses of:

```python
safe_invocation = "exec /bin/sh /usr/local/bin/dwarf-amaru-entrypoint.sh"
self.assertEqual(self.compose.count(safe_invocation), 2)
self.assertNotIn("exec /usr/local/bin/dwarf-amaru-entrypoint.sh", self.compose)
```

**Step 2: Run the focused test to verify it fails**

Run:

```bash
python3 -m unittest antithesis/cardano_amaru_adversarial/workload/tests/test_bundle_contract.py
```

Expected: FAIL because the current Compose file directly executes the mounted script.

### Task 2: Invoke both mounted scripts through `/bin/sh`

**Files:**
- Modify: `antithesis/cardano_amaru_adversarial/docker-compose.yaml`

**Step 1: Implement the minimal fix**

Replace both occurrences of:

```yaml
exec /usr/local/bin/dwarf-amaru-entrypoint.sh
```

with:

```yaml
exec /bin/sh /usr/local/bin/dwarf-amaru-entrypoint.sh
```

Keep logging, mounts, environments, networks, and fault labels unchanged.

**Step 2: Run the focused test to verify it passes**

Run the Task 1 command again.

Expected: PASS.

### Task 3: Verify the complete public bundle

**Files:**
- Verify: `antithesis/cardano_amaru_adversarial/`

**Step 1: Run the full workload test suite**

```bash
python3 -m unittest discover -s antithesis/cardano_amaru_adversarial/workload/tests -v
```

Expected: all tests pass.

**Step 2: Render the Compose configuration**

```bash
INTERNAL_NETWORK=false docker compose \
  -f antithesis/cardano_amaru_adversarial/docker-compose.yaml config --quiet
```

Expected: exit status 0.

**Step 3: Check the staged patch**

Run `git diff --check`, review `git diff`, and repeat the existing secret,
credential, private-key filename, and AppleDouble (`._`) scans.

Expected: no whitespace errors, secrets, key material, or AppleDouble files.

### Task 4: Publish and replace the invalid run

**Files:**
- Modify: `antithesis/cardano_amaru_adversarial/SUBMIT-RUNBOOK.md`
- Create: `docs/plans/2026-08-22-antithesis-relay-entrypoint-design.md`
- Create: `docs/plans/2026-08-22-antithesis-relay-entrypoint.md`

**Step 1: Record the failed run evidence and guardrail**

Document the run ID, permission error, invalid coverage conclusion, and required
interpreter invocation.

**Step 2: Commit and push the public-safe patch**

Stage only the two plan documents, Compose file, contract test, and runbook.
Commit with `fix: invoke mounted Amaru entrypoints through shell`, then push to
the public `main` branch using the existing non-printing credential flow.

**Step 3: Submit a replacement one-hour run**

Use release `/home/nigel/bin/moog`, the new commit, `--try 1`, `-t 1`, and omit
`--no-faults`.

**Step 4: Verify live behavior**

Confirm MOOG acceptance, then inspect the `amaru-cardano` logs for Amaru process
startup, absence of the permission error, and at least one
`both implementations returned classifiable phase-1 results` event.
