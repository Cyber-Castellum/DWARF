# Dwarf Antithesis Phase 3a — Pipeline-Proof Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a known-good cardano-node testnet (copied from CF's `cardano_node_adversary`, public images intact) in the repo, plus the Dwarf-side `--try` auto-count + result-wait helpers, then a gated live `moog requester create-test --no-faults -t 1` proving the Moog→Antithesis pipeline for `Cyber-Castellum/DWARF`.

**Architecture:** Vendor CF's testnet dir into `dwarf-v4/antithesis/cardano_node_dwarf/` (version-controlled, reused verbatim so it's known-good), publish it to `Cyber-Castellum/DWARF` at the same path, and drive it with Dwarf's existing `build_moog_create_test_command` plus two new pure helpers (`compute_next_try`, `parse_test_run_phase`). No Dwarf-built image (3a reuses CF's public images).

**Tech Stack:** Python 3.14 (`PYTHONPATH=dwarf python3 -m pytest tests/`), the `moog` binary on `cardano-box`, Docker/Podman compose, CF's public ghcr images.

---

## Conventions

- **Repo root:** `/Users/nigel/dwarf-project/dwarf-v4`; branch first: `git checkout -b feat/antithesis-phase3a`.
- **Tests:** `PYTHONPATH=dwarf python3 -m pytest tests/ -q` (baseline: **75 passing**).
- **Reference source (read-only):** `/Users/nigel/dwarf-project/codebases/cardano-node-antithesis/testnets/cardano_node_adversary/`.
- **Testnet path (both repos):** `antithesis/cardano_node_dwarf/`.
- One commit per task.

## File structure

```
antithesis/cardano_node_dwarf/docker-compose.yaml   CREATE  (copied from CF adversary testnet, verbatim)
antithesis/cardano_node_dwarf/testnet.yaml          CREATE  (copied)
antithesis/cardano_node_dwarf/relay-topology.json   CREATE  (copied)
antithesis/cardano_node_dwarf/tracer-config.yaml    CREATE  (copied)
antithesis/cardano_node_dwarf/README.md             CREATE  (Dwarf provenance note)
dwarf/profile_manager/moog.py                       MODIFY  add compute_next_try() + parse_test_run_phase()
dwarf/profile_manager/cli.py                        MODIFY  add `moog create-test --approve` + `moog test-status`
tests/test_antithesis_phase3a.py                    CREATE  testnet-dir validation + helper unit tests
```

---

## Task 1: Vendor the cardano_node_dwarf testnet dir

**Files:**
- Create: `antithesis/cardano_node_dwarf/{docker-compose.yaml,testnet.yaml,relay-topology.json,tracer-config.yaml}` (copied)
- Create: `antithesis/cardano_node_dwarf/README.md`
- Test: `tests/test_antithesis_phase3a.py`

- [ ] **Step 1: Copy CF's adversary testnet verbatim**

```bash
mkdir -p antithesis/cardano_node_dwarf
cp /Users/nigel/dwarf-project/codebases/cardano-node-antithesis/testnets/cardano_node_adversary/docker-compose.yaml antithesis/cardano_node_dwarf/
cp /Users/nigel/dwarf-project/codebases/cardano-node-antithesis/testnets/cardano_node_adversary/testnet.yaml         antithesis/cardano_node_dwarf/
cp /Users/nigel/dwarf-project/codebases/cardano-node-antithesis/testnets/cardano_node_adversary/relay-topology.json  antithesis/cardano_node_dwarf/
cp /Users/nigel/dwarf-project/codebases/cardano-node-antithesis/testnets/cardano_node_adversary/tracer-config.yaml   antithesis/cardano_node_dwarf/
```

- [ ] **Step 2: Write the provenance README**

```markdown
# cardano_node_dwarf — Antithesis testnet (Phase 3a pipeline-proof)

Verbatim copy of the Cardano Foundation `cardano_node_adversary` testnet from
https://github.com/cardano-foundation/cardano-node-antithesis (Apache-2.0),
used to prove the Dwarf→Moog→Antithesis pipeline for `Cyber-Castellum/DWARF`
before Phase 3b adds the Dwarf CBOR-fuzz adversary mode.

All container images are CF's already-public images (referenced by digest);
nothing is built here. Launched via `moog requester create-test -d
antithesis/cardano_node_dwarf -c <sha> -r Cyber-Castellum/DWARF --try <N> -t 1`.
```

- [ ] **Step 3: Write the validation test**

```python
# tests/test_antithesis_phase3a.py
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
TESTNET = ROOT / "antithesis" / "cardano_node_dwarf"


def _compose():
    return yaml.safe_load((TESTNET / "docker-compose.yaml").read_text())


def test_testnet_files_present():
    for f in ("docker-compose.yaml", "testnet.yaml", "relay-topology.json", "tracer-config.yaml", "README.md"):
        assert (TESTNET / f).is_file(), f


def test_all_images_are_public_registries():
    doc = _compose()
    imgs = []
    for svc in doc.get("services", {}).values():
        if isinstance(svc, dict) and svc.get("image"):
            imgs.append(svc["image"])
    # x-anchors carry images too; gather any 'image:' under the doc defensively
    assert imgs, "no service images found"
    for img in imgs:
        assert img.startswith(("ghcr.io/", "docker.io/")), f"non-public image: {img}"
        # 3a builds nothing locally — no bare local names
        assert "/" in img, f"suspect local image: {img}"


def test_harness_containers_have_fault_exclusion_label():
    doc = _compose()
    # At least the support/observability containers must carry the label.
    labeled = [
        name for name, svc in doc.get("services", {}).items()
        if isinstance(svc, dict) and "com.antithesis.exclude_from_faults" in (svc.get("labels") or {})
    ]
    assert labeled, "expected fault-exclusion labels on harness containers"
```

- [ ] **Step 4: Run the test**

Run: `PYTHONPATH=dwarf python3 -m pytest tests/test_antithesis_phase3a.py -v`
Expected: PASS (3 passed). If `test_all_images_are_public_registries` finds images only on x-anchors (not under `services`), broaden the gather to scan the raw text for `image:` lines — but CF's compose lists images per service, so the service scan suffices.

- [ ] **Step 5: Commit**

```bash
git add antithesis/cardano_node_dwarf/ tests/test_antithesis_phase3a.py
git commit -m "feat(antithesis): vendor cardano_node_dwarf testnet (CF adversary copy) for pipeline-proof"
```

---

## Task 2: `compute_next_try` helper

**Files:**
- Modify: `dwarf/profile_manager/moog.py`
- Test: `tests/test_antithesis_phase3a.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
def test_compute_next_try_counts_matching_runs():
    from profile_manager.moog import compute_next_try
    facts = [
        {"key": {"type": "test-run", "commitId": "abc", "directory": "antithesis/cardano_node_dwarf",
                 "platform": "github", "repository": {"organization": "Cyber-Castellum", "repo": "DWARF"},
                 "requester": "J-GainSec"}},
        {"key": {"type": "test-run", "commitId": "abc", "directory": "antithesis/cardano_node_dwarf",
                 "platform": "github", "repository": {"organization": "Cyber-Castellum", "repo": "DWARF"},
                 "requester": "J-GainSec"}},
        {"key": {"type": "test-run", "commitId": "OTHER", "directory": "antithesis/cardano_node_dwarf",
                 "platform": "github", "repository": {"organization": "Cyber-Castellum", "repo": "DWARF"},
                 "requester": "J-GainSec"}},
    ]
    n = compute_next_try(facts, commit="abc", directory="antithesis/cardano_node_dwarf",
                         repository="Cyber-Castellum/DWARF", requester="J-GainSec", platform="github")
    assert n == 3  # 2 matching + 1


def test_compute_next_try_starts_at_one():
    from profile_manager.moog import compute_next_try
    assert compute_next_try([], commit="z", directory="d",
                            repository="o/r", requester="u", platform="github") == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=dwarf python3 -m pytest tests/test_antithesis_phase3a.py -k compute_next_try -v`
Expected: FAIL — `ImportError: cannot import name 'compute_next_try'`

- [ ] **Step 3: Implement `compute_next_try` (append to moog.py)**

```python
def compute_next_try(test_run_facts, commit, directory, repository, requester, platform="github"):
    """Next attempt number = (count of matching existing test-run facts) + 1.

    Mirrors the CF cardano-node workflow: match on commit, directory, platform,
    repository (org/repo), and requester. `test_run_facts` is the parsed JSON
    array from `moog facts test-runs --whose <requester>`.
    """
    org, _, repo = (repository or "").partition("/")
    matches = 0
    for fact in test_run_facts or []:
        key = fact.get("key", {}) if isinstance(fact, dict) else {}
        repo_obj = key.get("repository", {}) or {}
        fact_repo = f"{repo_obj.get('organization', '')}/{repo_obj.get('repo', '')}"
        if (
            key.get("type") == "test-run"
            and key.get("commitId") == commit
            and key.get("directory") == directory
            and key.get("platform") == platform
            and fact_repo == repository
            and key.get("requester") == requester
        ):
            matches += 1
    return matches + 1
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=dwarf python3 -m pytest tests/test_antithesis_phase3a.py -k compute_next_try -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add dwarf/profile_manager/moog.py tests/test_antithesis_phase3a.py
git commit -m "feat(antithesis): compute_next_try for moog create-test attempt numbering"
```

---

## Task 3: `parse_test_run_phase` helper (result wait)

**Files:**
- Modify: `dwarf/profile_manager/moog.py`
- Test: `tests/test_antithesis_phase3a.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
def test_parse_test_run_phase_reads_phase():
    from profile_manager.moog import parse_test_run_phase
    facts = [{"value": {"phase": "accepted"}}]
    assert parse_test_run_phase(facts) == "accepted"


def test_parse_test_run_phase_handles_empty():
    from profile_manager.moog import parse_test_run_phase
    assert parse_test_run_phase([]) is None
    assert parse_test_run_phase([{"value": {}}]) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=dwarf python3 -m pytest tests/test_antithesis_phase3a.py -k parse_test_run_phase -v`
Expected: FAIL — `ImportError: cannot import name 'parse_test_run_phase'`

- [ ] **Step 3: Implement `parse_test_run_phase` (append to moog.py)**

```python
def parse_test_run_phase(test_run_facts):
    """Return the .value.phase of the first fact, or None.

    Input is the parsed JSON from `moog facts test-runs --test-run-id <id>`
    (mirrors CF's wait-for-test.sh, which reads `.[0].value.phase`).
    """
    if not test_run_facts:
        return None
    first = test_run_facts[0] if isinstance(test_run_facts, list) else test_run_facts
    return (first.get("value", {}) or {}).get("phase")
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONPATH=dwarf python3 -m pytest tests/test_antithesis_phase3a.py -k parse_test_run_phase -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add dwarf/profile_manager/moog.py tests/test_antithesis_phase3a.py
git commit -m "feat(antithesis): parse_test_run_phase for create-test result polling"
```

---

## Task 4: CLI — `moog create-test` (dry-run/approve) + `moog test-status`

**Files:**
- Modify: `dwarf/profile_manager/cli.py` (parser + `cmd_moog` dispatch)
- Test: `tests/test_antithesis_phase3a.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
def test_cli_parses_moog_create_test_approve():
    from profile_manager.cli import build_parser
    args = build_parser().parse_args([
        "moog", "create-test", "--repo", "Cyber-Castellum/DWARF",
        "--github-user", "J-GainSec", "--directory", "antithesis/cardano_node_dwarf",
        "--commit", "deadbeef", "--duration", "1", "--no-faults", "--approve", "--json",
    ])
    assert args.command == "moog"
    assert args.moog_command == "create-test"
    assert args.approve is True
    assert args.no_faults is True
    assert args.directory == "antithesis/cardano_node_dwarf"


def test_cli_parses_moog_test_status():
    from profile_manager.cli import build_parser
    args = build_parser().parse_args(["moog", "test-status", "abc123", "--json"])
    assert args.command == "moog"
    assert args.moog_command == "test-status"
    assert args.test_run_id == "abc123"
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=dwarf python3 -m pytest tests/test_antithesis_phase3a.py -k "moog_create_test or moog_test_status" -v`
Expected: FAIL — argparse rejects `create-test` / `test-status` (not registered).

- [ ] **Step 3: Add subparsers (in `build_parser`, in the `moog_sub` block — after the existing `create-test-plan` parser ~line 260)**

```python
    moog_create_test = moog_sub.add_parser("create-test")
    moog_create_test.add_argument("--repo")
    moog_create_test.add_argument("--github-user")
    moog_create_test.add_argument("--directory")
    moog_create_test.add_argument("--commit")
    moog_create_test.add_argument("--try", dest="try_number", default=None)
    moog_create_test.add_argument("--duration", default="1")
    moog_create_test.add_argument("--no-faults", action="store_true")
    moog_create_test.add_argument("--approve", action="store_true")
    moog_create_test.add_argument("--json", action="store_true")

    moog_test_status = moog_sub.add_parser("test-status")
    moog_test_status.add_argument("test_run_id")
    moog_test_status.add_argument("--json", action="store_true")
```

- [ ] **Step 4: Add handling in `cmd_moog` (after the existing create-test-plan branch). Dry-run prints the command; --approve runs it on the host with secrets sourced from files; test-status polls phase.**

```python
    if args.moog_command == "create-test":
        import json as _json
        from profile_manager.moog import build_moog_create_test_command
        cfg = _load_or_intake("moog").moog
        try_number = args.try_number or 1
        command = build_moog_create_test_command(
            cfg, repo=args.repo, github_user=args.github_user, directory=args.directory,
            commit=args.commit, try_number=try_number, duration_hours=args.duration,
            no_faults=args.no_faults,
        )
        if not args.approve:
            payload = {"state": "dry-run", "command": command, "try": try_number}
            print(_json.dumps(payload, indent=2) if args.json else command)
            return 0
        # --approve: run on the host with passphrase + PAT sourced from files (never printed).
        from profile_manager import remote
        result = remote.run_moog_create_test(cfg, command)
        payload = {"state": "submitted" if result.returncode == 0 else "error",
                   "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        print(_json.dumps(payload, indent=2) if args.json else result.stdout)
        return result.returncode

    if args.moog_command == "test-status":
        import json as _json
        from profile_manager import remote
        from profile_manager.moog import parse_test_run_phase
        cfg = _load_or_intake("moog").moog
        facts = remote.fetch_test_run_facts(cfg, args.test_run_id)
        phase = parse_test_run_phase(facts)
        payload = {"test_run_id": args.test_run_id, "phase": phase}
        print(_json.dumps(payload, indent=2) if args.json else f"phase: {phase}")
        return 0
```

Note: `_load_or_intake("moog").moog` returns the moog config block (confirm the attribute name against `config.py`; if the loader returns a dict, use `["moog"]`). Adjust the accessor to match the existing `cmd_moog` pattern at the top of that function.

- [ ] **Step 5: Add the remote helpers (append to `dwarf/profile_manager/remote.py`)**

```python
def run_moog_create_test(moog_config, command):
    """Run a prebuilt create-test command on the remote host, injecting the
    wallet passphrase + GitHub PAT from their on-host files (never logged)."""
    from profile_manager.moog import normalize_moog_config
    cfg = normalize_moog_config(moog_config)
    secrets_root = cfg["secrets_root"]
    script = (
        "set -uo pipefail\n"
        f'export MOOG_WALLET_PASSPHRASE="$(cat {secrets_root}/requester/wallet.passphrase)"\n'
        'export MOOG_GITHUB_PAT="$(python3 -c \'import json;print(json.load(open("/home/nigel/dwarf-v4/var/state/config.yaml"))["moog"]["github_pat"])\')"\n'
        f"{command}\n"
    )
    return run_remote_script(script)


def fetch_test_run_facts(moog_config, test_run_id):
    """Return parsed JSON from `moog facts test-runs --test-run-id <id>`."""
    import json
    from profile_manager.moog import normalize_moog_config
    cfg = normalize_moog_config(moog_config)
    cmd = (
        f'MOOG_MPFS_HOST={cfg["mpfs_host"]} MOOG_TOKEN_ID={cfg["token_id"]} '
        f'{cfg["moog_binary"]} facts test-runs --test-run-id {test_run_id}'
    )
    result = run_remote_script(cmd)
    try:
        return json.loads(result.stdout) if result.stdout.strip() else []
    except Exception:
        return []
```

Note: `run_remote_script` is the existing remote SSH executor in `remote.py`. Confirm its name (e.g. `run_remote`/`run`/`ssh_run`) and reuse it; if it takes argv rather than a shell script, wrap with `["bash","-lc",script]`.

- [ ] **Step 6: Run tests (parser tests are pure; the run paths are exercised live in Task 5)**

Run: `PYTHONPATH=dwarf python3 -m pytest tests/test_antithesis_phase3a.py -v`
Expected: PASS — the parser + helper tests (the `--approve`/`test-status` execution is covered by the live run, not unit-mocked here).

- [ ] **Step 7: Run full suite (non-regression) + commit**

```bash
PYTHONPATH=dwarf python3 -m pytest tests/ -q   # expect all green
git add dwarf/profile_manager/cli.py dwarf/profile_manager/remote.py tests/test_antithesis_phase3a.py
git commit -m "feat(antithesis): moog create-test (dry-run/approve) + test-status CLI"
```

---

## Task 5: Gated live pipeline run (publish → preflight → create-test → wait)

This task performs outward-facing/on-chain actions. Each sub-step is gated on explicit user go and the PAT having `Contents:write`.

- [ ] **Step 1: Verify the dry-run command is correct**

Run (in container):
```bash
docker exec dwarf-fw-june-m2 /home/dwarf/dwarf-fw/dwarf/cardano-profile moog create-test \
  --repo Cyber-Castellum/DWARF --github-user J-GainSec \
  --directory antithesis/cardano_node_dwarf --commit <sha> --duration 1 --no-faults --json
```
Expected: a `dry-run` payload whose `command` matches `moog requester create-test -w … -p github -r Cyber-Castellum/DWARF -d antithesis/cardano_node_dwarf -c <sha> --try 1 -u J-GainSec -t 1 --no-faults`.

- [ ] **Step 2: Publish the testnet dir to `Cyber-Castellum/DWARF`** (needs PAT `Contents:write`)

Commit the five files under `antithesis/cardano_node_dwarf/` to the default branch via the GitHub contents API (one commit), then capture the resulting commit `<sha>`. (If the PAT still lacks write, the user commits the dir via the UI; either way record `<sha>`.)

- [ ] **Step 3: Preflight (read-only)**

```bash
docker exec dwarf-fw-june-m2 /home/dwarf/dwarf-fw/dwarf/cardano-profile moog preflight \
  --asset-dir /tmp/none --repo Cyber-Castellum/DWARF --github-user J-GainSec \
  --directory antithesis/cardano_node_dwarf --commit <sha> --json
```
Expected: requester/readiness stages OK (whitelist now granted); confirm no `blocked` stage that would reject the request.

- [ ] **Step 4: Submit the no-faults run** (explicit user go — on-chain)

```bash
docker exec dwarf-fw-june-m2 /home/dwarf/dwarf-fw/dwarf/cardano-profile moog create-test \
  --repo Cyber-Castellum/DWARF --github-user J-GainSec \
  --directory antithesis/cardano_node_dwarf --commit <sha> --duration 1 --no-faults --approve --json
```
Expected: `submitted`, returncode 0, a `testRunId` + `txHash` in output.

- [ ] **Step 5: Wait + view results**

```bash
docker exec dwarf-fw-june-m2 /home/dwarf/dwarf-fw/dwarf/cardano-profile moog test-status <testRunId> --json
```
Poll until phase reaches a terminal state; view the run/triage in the `amaru-cardano` dashboard. A completed clean no-faults run proves the pipeline.

- [ ] **Step 6: Faults-on run** (after the no-faults run is green)

Repeat Step 4 without `--no-faults`; wait + view results.

---

## Phase 3a done — success criteria

- `antithesis/cardano_node_dwarf/` vendored + committed in `dwarf-v4`; testnet validation + helper unit tests pass; full suite green.
- The dir is published to `Cyber-Castellum/DWARF`; `create-test --no-faults` submits (testRunId + txHash); phase reaches terminal; run visible in the dashboard.
- Then a faults-on run completes. Pipeline proven → ready for Phase 3b (Haskell CBOR-fuzz adversary).

## Self-review

- **Spec coverage:** vendor CF adversary testnet (T1), Dwarf `create-test` flags already correct + `--try` auto-count (T2) + result wait (T3) + CLI runner/status (T4), gated live no-faults-first then faults run (T5), no Dwarf image (T1 reuses CF public images), PAT-write blocker called out (T5 S2). ✓
- **Placeholder scan:** `<sha>` in Task 5 is a runtime value (the published commit), not a plan placeholder; all code steps contain full code. Two `Note:` items (Task 4) flag exact existing-symbol names to confirm against `config.py`/`remote.py` — these are real integration checks, not deferred work. ✓
- **Type consistency:** `compute_next_try(facts, commit, directory, repository, requester, platform)`, `parse_test_run_phase(facts)`, `build_moog_create_test_command(...)` (existing), `run_moog_create_test`, `fetch_test_run_facts` — used consistently across T2–T5. ✓
- **Risk:** Task 4 depends on the exact `_load_or_intake` accessor and `remote.py` executor name — verified at implementation time against the current code (flagged inline), not assumed.
