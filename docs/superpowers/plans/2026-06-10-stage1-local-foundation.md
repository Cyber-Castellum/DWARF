# Stage 1 — Local Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the restored cardano-node scenarios genuinely runnable locally — build the `dwarf-cardano-shims` decode binaries and install them where manifests expect — and add a reusable `scenario verify` behavioral gate that passes only when a scenario runs cleanly with its declared assertions passing.

**Architecture:** Two components. (A) Compile the existing Haskell shim cabal project on cardano-box (CHaP + ghc-9.6.7, the dwarf-adversary toolchain) and copy each of the 14 executables to its manifest-declared `binary` path. (B) Add a thin `scenario verify` CLI subcommand over the existing `scenario run` that gates on `exit_status` clean + assertion tally `fail==0, pass>0`, plus a batch script.

**Tech Stack:** Haskell (GHC 9.6.7, cabal, CHaP), Python 3 (the existing `profile_manager` CLI/executor). Builds + runs on **cardano-box**.

**Spec:** `docs/superpowers/specs/2026-06-10-stage1-local-foundation-design.md`

---

## Context for the implementer

- The shim project: `dwarf/targets/cardano-node/{dwarf-cardano-shims.cabal,cabal.project,src/Decode*.hs}` — 14 executables, complete harnesses, CHaP-pinned. Verified: `cabal build all --dry-run -w ghc-9.6.7` resolves on cardano-box (no ghc-9.10 needed).
- A **background build is already running** on cardano-box: `cd /home/nigel/dwarf-v4/dwarf/targets/cardano-node && cabal build all -w ghc-9.6.7` (log `shims-build.log`, sentinel `shims-build.done`). Wait for `shims-build.done` (exit 0) before Task 1's install step.
- Each target manifest (`dwarf/targets/manifests/<id>.yaml`, JSON) has a `binary` field = where the executor looks. Install each built exe **to its manifest's declared path** (don't assume one location).
- The cbor-fuzz scenarios are `runtime: library` (no devnet needed) with `input_format: stdin_bytes`: the executor feeds fuzzed bytes to the shim on stdin; the shim exits `0`(OK)/`1`(clean error); a crash/other exit is a finding.
- Run from `/home/nigel/dwarf-v4`. The CLI entry is `PYTHONPATH=dwarf python3 dwarf/cardano-profile`.
- The local `dwarf-v4` mac checkout is the git source of truth (SP1 landed there). cardano-box's `/home/nigel/dwarf-v4` is the exec host; sync the cardano-node target dir + any CLI changes to it. Commit on the mac.
- Branch off `main`.

## File Structure

| Path | Responsibility |
|------|----------------|
| `dwarf/targets/cardano-node/bin/<name>` (build output) | the 14 installed shim binaries (gitignored) |
| `dwarf/targets/cardano-node/.gitignore` (new) | ignore `dist-newstyle/`, `bin/`, build logs |
| `tools/stage1_install_shims.py` (new) | read each manifest's `binary` path, copy the built exe there, chmod +x |
| `dwarf/profile_manager/cli.py` (modify) | add `scenario verify` subparser + handler |
| `dwarf/profile_manager/scenario.py` (modify) | add `verify_scenario(path, ...)` → run + assert tally gate |
| `tools/stage1_verify.sh` (new) | batch `scenario verify` over a scenario list; report OK=/FAIL= |
| `tools/test_stage1.py` (new) | unit test for the verify gate logic |

---

## Task 1: Build + install the shim binaries

**Files:**
- Create: `dwarf-v4/dwarf/targets/cardano-node/.gitignore`
- Create: `dwarf-v4/tools/stage1_install_shims.py`

- [ ] **Step 1: Wait for the background build to finish (exit 0)**

Run: `ssh cardano-box 'cd /home/nigel/dwarf-v4/dwarf/targets/cardano-node && cat shims-build.done 2>/dev/null; tail -3 shims-build.log'`
Expected: `shims-build.done` contains `0`; log tail shows `Linking ...` for the executables. If non-zero: read `shims-build.log`, fix the cabal/dep error (e.g. a missing system lib — `apt install`), re-run `cabal build all -w ghc-9.6.7`.

- [ ] **Step 2: Write the install script**

`tools/stage1_install_shims.py`:

```python
"""Install built dwarf-cardano-shims executables to their manifest-declared
binary paths. Run on cardano-box after `cabal build all`."""
import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path("/home/nigel/dwarf-v4/dwarf")
SHIMS = ROOT / "targets" / "cardano-node"
MANIFESTS = ROOT / "targets" / "manifests"


def built_path(exe_name):
    """Locate the compiled exe under dist-newstyle (arch/ghc-version agnostic)."""
    matches = glob.glob(
        str(SHIMS / "dist-newstyle" / "build" / "*" / "*" /
            "dwarf-cardano-shims-*" / "x" / exe_name / "build" / exe_name / exe_name)
    )
    return matches[0] if matches else None


def main():
    installed, missing = [], []
    for mpath in sorted(glob.glob(str(MANIFESTS / "cardano-node-*.yaml"))):
        m = json.load(open(mpath))
        exe = m["id"]
        dst_rel = m.get("binary")  # e.g. dwarf/targets/cardano-node/bin/<id>
        if not dst_rel:
            continue
        src = built_path(exe)
        if not src:
            missing.append(exe)
            continue
        dst = Path("/home/nigel/dwarf-v4") / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        os.chmod(dst, 0o755)
        installed.append(exe)
    print(f"installed={len(installed)} missing-build={len(missing)}")
    if missing:
        print("MISSING BUILDS:", missing)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the install on cardano-box**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
rsync -az tools/stage1_install_shims.py cardano-box:/home/nigel/dwarf-v4/tools/
ssh cardano-box 'cd /home/nigel/dwarf-v4 && python3 tools/stage1_install_shims.py'
```
Expected: `installed=14 missing-build=0` (every cardano-node manifest's binary now present). If `missing-build>0`: that exe didn't compile — check `shims-build.log` for that target.

- [ ] **Step 4: Smoke-test one binary directly**

```bash
ssh cardano-box 'cd /home/nigel/dwarf-v4 && \
  printf "\x82\x00\x81\x00" | dwarf/targets/cardano-node/bin/cardano-node-mini-protocol-decode-chainsync; echo "exit=$?"'
```
Expected: a clean `OK`/`ERR` line and exit `0` or `1` (NOT a crash/127/segfault). Confirms the binary runs and decodes stdin bytes.

- [ ] **Step 5: Gitignore build output + commit the install script**

`dwarf/targets/cardano-node/.gitignore`:
```
dist-newstyle/
bin/
shims-build.log
shims-build.done
```

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git checkout -b stage1-local-foundation
git add dwarf/targets/cardano-node/.gitignore tools/stage1_install_shims.py
git commit -m "feat(stage1): build + install cardano-node decode shims (install script + gitignore)"
```

---

## Task 2: `scenario verify` behavioral gate

**Files:**
- Modify: `dwarf-v4/dwarf/profile_manager/scenario.py`
- Modify: `dwarf-v4/dwarf/profile_manager/cli.py`
- Create: `dwarf-v4/tools/test_stage1.py`

- [ ] **Step 1: Write the failing test for the gate logic**

`tools/test_stage1.py` (pure-logic test of the gate predicate, no devnet):

```python
import sys
sys.path.insert(0, "/Users/nigel/dwarf-project/dwarf-v4/dwarf")
from profile_manager import scenario as s


def test_gate_predicate():
    # green: clean exit + assertions passed, none failed
    assert s.verify_gate({"exit_status": "ok"}, {"fail": 0, "pass": 3, "total": 3}) == ("pass", "")
    # red: a failing assertion
    assert s.verify_gate({"exit_status": "ok"}, {"fail": 1, "pass": 2, "total": 3})[0] == "fail"
    # red: no assertions ran at all (the "didn't actually exercise" trap)
    assert s.verify_gate({"exit_status": "ok"}, {"fail": 0, "pass": 0, "total": 0})[0] == "fail"
    # red: executor error
    assert s.verify_gate({"exit_status": "error"}, {"fail": 0, "pass": 1, "total": 1})[0] == "fail"
```

- [ ] **Step 2: Run it — expect FAIL (no `verify_gate`)**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && python3 -m pytest tools/test_stage1.py -q`
Expected: FAIL — `module 'scenario' has no attribute 'verify_gate'`.

- [ ] **Step 3: Add `verify_gate` + `verify_scenario` to `scenario.py`**

Append to `dwarf/profile_manager/scenario.py`:

```python
def verify_gate(run_summary: dict, assertions: dict) -> tuple[str, str]:
    """Pure gate predicate: 'pass' iff the run completed cleanly AND at least
    one assertion passed AND none failed. Returns (state, reason)."""
    if run_summary.get("exit_status") != "ok":
        return "fail", f"executor exit_status={run_summary.get('exit_status')!r}"
    if assertions.get("fail", 0) > 0:
        return "fail", f"{assertions['fail']} assertion(s) failed"
    if assertions.get("pass", 0) <= 0:
        return "fail", "no assertions passed (scenario did not exercise the target)"
    return "pass", ""


def verify_scenario(path, *, runs_dir, state_dir, registry_path=None):
    """Run a scenario locally and apply verify_gate. Returns
    {state, reason, run_id, assertions}."""
    result = run_scenario(path, runs_dir=runs_dir, state_dir=state_dir,
                          registry_path=registry_path)
    summary = {"exit_status": result.get("exit_status")} if isinstance(result, dict) else {"exit_status": getattr(result, "exit_status", None)}
    assertions = (result.get("assertions") if isinstance(result, dict)
                  else getattr(result, "assertions", {})) or {}
    state, reason = verify_gate(summary, assertions)
    run_id = (result.get("run_id") if isinstance(result, dict)
              else getattr(result, "run_id", None))
    return {"state": state, "reason": reason, "run_id": run_id, "assertions": assertions}
```

> **Implementer note:** confirm `run_scenario`'s return shape (it printed `run_id`/`exit_status`/`assertions` in SP1's spot-run). Adapt the field access in `verify_scenario` to that shape (dict vs object). The CLI's existing `scenario run` handler shows how it reads the result — mirror it.

- [ ] **Step 4: Run the test — expect PASS**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && python3 -m pytest tools/test_stage1.py -q`
Expected: PASS.

- [ ] **Step 5: Add the `scenario verify` CLI subcommand**

In `cli.py`, next to the `scenario run`/`scenario validate` subparsers, add:

```python
    scenario_verify = scenario_sub.add_parser("verify")
    scenario_verify.add_argument("path")
    scenario_verify.add_argument("--runs-dir")
    scenario_verify.add_argument("--state-dir")
    scenario_verify.add_argument("--registry-path")
    scenario_verify.add_argument("--json", action="store_true")
```

And in `cmd_scenario` (next to the `verify`-adjacent handlers):

```python
    if args.scenario_command == "verify":
        runs_dir = _forensic_runs_dir(args)
        state_dir = _forensic_state_dir(args)
        payload = scenario_module.verify_scenario(
            args.path, runs_dir=runs_dir, state_dir=state_dir,
            registry_path=Path(args.registry_path) if args.registry_path else None,
        )
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            tag = "OK" if payload["state"] == "pass" else "FAIL"
            print(f"{tag}: {args.path} (assertions={payload['assertions']}) {payload['reason']}".rstrip())
        return 0 if payload["state"] == "pass" else 1
```

> **Implementer note:** reuse the same `_forensic_runs_dir`/`_forensic_state_dir` helpers the `scenario run` handler uses (grep `cmd_scenario` for them). Match argument-default behavior to `scenario run`.

- [ ] **Step 6: Commit the gate**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add dwarf/profile_manager/scenario.py dwarf/profile_manager/cli.py tools/test_stage1.py
git commit -m "feat(stage1): scenario verify behavioral gate (run + assertion-tally check)"
```

---

## Task 3: Prove it — verify the cbor-fuzz scenarios run + pass

**Files:**
- Create: `dwarf-v4/tools/stage1_verify.sh`

- [ ] **Step 1: Write the batch verifier**

`tools/stage1_verify.sh`:

```bash
#!/usr/bin/env bash
# Run `scenario verify` over a list of scenarios; report OK/FAIL totals.
# Usage: stage1_verify.sh <scenario-glob-or-listfile>
set -uo pipefail
cd "$(dirname "$0")/.."
ok=0; fail=0
for f in dwarf/scenarios/cardano-node-cbor-*-fuzz.yaml; do
  name=$(basename "$f")
  if PYTHONPATH=dwarf python3 dwarf/cardano-profile scenario verify "$f" \
       --runs-dir /tmp/stage1-runs --state-dir /tmp/stage1-state >/tmp/s1v.out 2>&1; then
    ok=$((ok+1))
  else
    fail=$((fail+1)); echo "FAIL: $name"; tail -2 /tmp/s1v.out
  fi
done
echo "verify OK=$ok FAIL=$fail"
[ "$fail" -eq 0 ]
```

- [ ] **Step 2: Sync CLI changes + run the batch verifier on cardano-box**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
rsync -az dwarf/profile_manager/ cardano-box:/home/nigel/dwarf-v4/dwarf/profile_manager/
rsync -az tools/stage1_verify.sh cardano-box:/home/nigel/dwarf-v4/tools/
ssh cardano-box 'cd /home/nigel/dwarf-v4 && chmod +x tools/stage1_verify.sh && ./tools/stage1_verify.sh'
```
Expected: `verify OK=<N> FAIL=0` for the `cardano-node-cbor-*-fuzz` scenarios — i.e. the shims actually decode the fuzzed inputs and `roundtrip_equals_original`/`parse_succeeds_or_clean_error`/`parser_exit_status` pass. If any FAIL: the message + run bundle say why (missing binary → re-run Task 1 install; assertion fail → a real decode finding to record; 0 assertions → the scenario's load/assert wiring needs the input corpus — inspect the run bundle).

- [ ] **Step 3: Confirm the gate catches a real failure (negative test)**

```bash
ssh cardano-box 'cd /home/nigel/dwarf-v4 && mv dwarf/targets/cardano-node/bin/cardano-node-cbor-decode-block /tmp/_blk && \
  PYTHONPATH=dwarf python3 dwarf/cardano-profile scenario verify dwarf/scenarios/cardano-node-cbor-block-fuzz.yaml --runs-dir /tmp/s1neg --state-dir /tmp/s1negs; echo "rc=$?"; \
  mv /tmp/_blk dwarf/targets/cardano-node/bin/cardano-node-cbor-decode-block'
```
Expected: `FAIL` + `rc=1` (missing binary → executor error → gate red). Confirms the gate doesn't false-green when the target is absent — the exact Phase-3b-class trap.

- [ ] **Step 4: Commit + finish**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add tools/stage1_verify.sh
git commit -m "feat(stage1): batch verify harness — cbor-fuzz scenarios run + assertions pass"
```
Then use `superpowers:finishing-a-development-branch` (tests `tools/test_stage1.py` green + batch verify green → merge options).

---

## Self-Review

**1. Spec coverage:**
- "Build the 14 shim binaries + install to manifest paths" → Task 1 (build wait + install script reading each manifest `binary`). ✓
- "Local behavioral gate: run + exit clean + fail==0/pass>0" → Task 2 (`verify_gate`/`verify_scenario` + CLI). ✓
- "Done when cbor-fuzz scenarios run + assertions pass; gate green; corrupt→clean error; crash→finding" → Task 3 Steps 2–3. ✓
- "No false-green (0 assertions = red)" → `verify_gate` 0-pass branch + Task 2 test + Task 3 Step 3 negative test. ✓
- "cardano-node only; library runtime first; binaries gitignored" → Task 1 gitignore; Task 3 globs `cardano-node-cbor-*-fuzz`. ✓
- GHC/CHaP risk → resolved (dry-run passed with 9.6.7; build already running).

**2. Placeholder scan:** No TBD/TODO. The two implementer notes (run_scenario return shape; reuse `_forensic_*` helpers) are concrete "match the existing handler" instructions, not deferrals — the exact shapes are read from code that exists. Every code step has real code; every command has expected output.

**3. Type consistency:** `verify_gate(run_summary, assertions) -> (state, reason)` used identically in the test, `verify_scenario`, and the CLI handler. `verify_scenario(...) -> {state, reason, run_id, assertions}` consumed consistently by the CLI handler (`payload["state"]`, `payload["assertions"]`, `payload["reason"]`). ✓
