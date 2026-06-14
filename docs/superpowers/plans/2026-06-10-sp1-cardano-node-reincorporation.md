# SP1 — cardano-node Artifact Reincorporation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Additively restore the ~197 cardano-node-implementation scenarios removed for M2 — plus their primitive/manifest/registry dependency closure — from `dwarf-deploypackage-may` into `dwarf-v4`, validated.

**Architecture:** A dependency-layered, additive restore driven by a closure script: classify the may→v4 scenario delta, compute the primitives/targets each cardano-node scenario references, then restore bottom-up (primitives + registry → manifests → scenarios), validating each layer with the existing pure-Python `scenario validate --semantic`. Never modifies dwarf-v4's existing artifacts.

**Tech Stack:** Python 3 (stdlib only: json/glob/pathlib/shutil), the existing `cardano-profile scenario validate --semantic --registry-path` CLI. Pure-Python, **runs locally** (no Haskell/Docker).

**Spec:** `docs/superpowers/specs/2026-06-10-sp1-cardano-node-reincorporation-design.md`

---

## Context for the implementer

- **Source:** `/Users/nigel/dwarf-project/dwarf-deploypackage-may/dwarf/` (464 scenarios, 109 load primitives, 98 manifests). **Dest:** `/Users/nigel/dwarf-project/dwarf-v4/dwarf/`.
- dwarf-v4 artifacts are a **byte-identical subset** of may (verified): 28 shared scenarios identical, 10 kept primitives identical. So the restore is **purely additive** — copying a may-only file can never clobber a v4 file. Guard anyway (never overwrite an existing dest file).
- **Validator semantics** (`profile_manager/scenario.py::semantic_validate_scenario`): for every primitive reference it checks the name is in the registry, family matches, `scenario.runtime ∈ entry.runtimes`, and `scenario.target.implementation ∈ entry.supports`. It does **not** check target manifests. So **primitives+registry must be complete before scenarios validate**; manifests are for the executor/spot-run.
- **Registry shape** (`dwarf/primitives/registry.json`): `{"$comment":..., "entry_schema":..., "primitives": { "<name>": {class, family, module, params_schema, runtimes, supports, version} }}`. Merge = add entries to the `primitives` map.
- **Manifest shape** (`dwarf/targets/manifests/<target_id>.yaml`, JSON content): `{id, binary, input_format, implementation, language, status, ...}`. Targets are `status: UNBUILT` skeletons — copy the manifest only; **do not build**.
- Run all commands from `/Users/nigel/dwarf-project/dwarf-v4`. Validate with: `PYTHONPATH=dwarf python3 dwarf/cardano-profile scenario validate --semantic <path>`.
- Work on a branch off `main`.

## File Structure

| Path | Responsibility |
|------|----------------|
| `tools/sp1_closure.py` (new) | Classify the may→v4 delta, compute cardano-node membership + primitive/target closure, emit manifests to `sp1-closure/` |
| `tools/sp1_merge_registry.py` (new) | Pure function `merge_registry(v4, may, names)` → additive union of registry `primitives`; conflict-flagging |
| `tools/test_sp1.py` (new) | Tests for the closure reconciliation + the registry merge |
| `sp1-closure/{scenarios,primitives,targets,deferred}.txt` (generated, gitignored) | Work lists driving the layered restore |
| `dwarf/primitives/{load,assertion,probe,teardown}/*.schema.json` | **+** restored primitive schemas (additive) |
| `dwarf/primitives/registry.json` | **merged** (additive) |
| `dwarf/targets/manifests/*.yaml` | **+** restored manifests (additive) |
| `dwarf/scenarios/*.yaml` | **+** ~197 restored scenarios (additive) |

---

## Task 1: Closure computation script

**Files:**
- Create: `dwarf-v4/tools/sp1_closure.py`
- Create: `dwarf-v4/tools/test_sp1.py`

- [ ] **Step 1: Write the closure script**

`tools/sp1_closure.py`:

```python
"""SP1 closure: classify the may->v4 scenario delta and compute the
cardano-node membership + its primitive/target dependency closure."""
import glob
import json
import os
from pathlib import Path

MAY = Path("/Users/nigel/dwarf-project/dwarf-deploypackage-may/dwarf")
V4 = Path("/Users/nigel/dwarf-project/dwarf-v4/dwarf")
OUT = Path("/Users/nigel/dwarf-project/dwarf-v4/sp1-closure")


def load(path):
    with open(path) as handle:
        return json.load(handle)


def walk_values(obj, key):
    """Collect every string value under `key`, recursively."""
    found = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key and isinstance(v, str):
                found.add(v)
            else:
                found |= walk_values(v, key)
    elif isinstance(obj, list):
        for item in obj:
            found |= walk_values(item, key)
    return found


def compute():
    may_scn = {os.path.basename(p): p for p in glob.glob(str(MAY / "scenarios" / "*.yaml"))}
    v4_scn = {os.path.basename(p) for p in glob.glob(str(V4 / "scenarios" / "*.yaml"))}
    delta = sorted(set(may_scn) - v4_scn)

    cardano, deferred = [], []
    prim_refs, target_refs = set(), set()
    for fn in delta:
        scn = load(may_scn[fn])
        impl = (scn.get("target") or {}).get("implementation")
        prims = walk_values(scn, "primitive")
        targets = walk_values(scn, "target_id")
        is_cardano = impl == "cardano-node" and not any("amaru" in t for t in targets)
        if is_cardano:
            cardano.append(fn)
            prim_refs |= prims
            target_refs |= targets
        else:
            deferred.append(fn)  # amaru + differential -> SP3
    return {
        "delta": delta,
        "cardano": sorted(cardano),
        "deferred": sorted(deferred),
        "primitives": sorted(prim_refs),
        "targets": sorted(target_refs),
        "may_scn": may_scn,
    }


def main():
    r = compute()
    OUT.mkdir(exist_ok=True)
    (OUT / "scenarios.txt").write_text("\n".join(r["cardano"]) + "\n")
    (OUT / "primitives.txt").write_text("\n".join(r["primitives"]) + "\n")
    (OUT / "targets.txt").write_text("\n".join(r["targets"]) + "\n")
    (OUT / "deferred.txt").write_text("\n".join(r["deferred"]) + "\n")
    print(f"delta scenarios:            {len(r['delta'])}")
    print(f"cardano-node (SP1):         {len(r['cardano'])}")
    print(f"deferred (amaru+diff, SP3): {len(r['deferred'])}")
    print(f"reconcile cardano+deferred: {len(r['cardano']) + len(r['deferred'])}")
    print(f"primitives referenced:      {len(r['primitives'])}")
    print(f"target manifests referenced:{len(r['targets'])}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the reconciliation test**

`tools/test_sp1.py`:

```python
import sp1_closure


def test_closure_reconciles_and_finds_cardano_node():
    r = sp1_closure.compute()
    # every delta scenario is classified exactly once
    assert len(r["cardano"]) + len(r["deferred"]) == len(r["delta"])
    # the delta is the known 436 (may 464 - v4 28)
    assert len(r["delta"]) == 436
    # cardano-node membership is the substantial majority-ish slice we expect
    assert 150 <= len(r["cardano"]) <= 210
    # closure is non-empty and references real primitive names
    assert r["primitives"]
    assert "cbor_fuzz_target" in r["primitives"]
```

- [ ] **Step 3: Run the test (expect FAIL first if script absent, then PASS)**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4/tools && python3 -m pytest test_sp1.py::test_closure_reconciles_and_finds_cardano_node -v`
Expected: PASS (delta==436, cardano in [150,210], `cbor_fuzz_target` present).

- [ ] **Step 4: Generate the closure work-lists**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && python3 tools/sp1_closure.py`
Expected output includes `reconcile cardano+deferred: 436` and a non-zero `cardano-node (SP1)` count. Confirm `sp1-closure/scenarios.txt` etc. exist.

- [ ] **Step 5: Gitignore the work dir + commit the tools**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
echo "sp1-closure/" >> .gitignore
git add tools/sp1_closure.py tools/test_sp1.py .gitignore
git commit -m "feat(sp1): closure script — classify may delta + compute cardano-node closure"
```

---

## Task 2: Layer 1 — restore primitives + merge registry

**Files:**
- Create: `dwarf-v4/tools/sp1_merge_registry.py`
- Modify: `dwarf-v4/dwarf/primitives/registry.json`
- Add: `dwarf-v4/dwarf/primitives/{family}/*.schema.json` (the referenced primitives)

- [ ] **Step 1: Write the registry-merge function**

`tools/sp1_merge_registry.py`:

```python
"""Additive merge of may primitive registry entries into the v4 registry."""
import json
from pathlib import Path

MAY_REG = Path("/Users/nigel/dwarf-project/dwarf-deploypackage-may/dwarf/primitives/registry.json")
V4_REG = Path("/Users/nigel/dwarf-project/dwarf-v4/dwarf/primitives/registry.json")


def merge_registry(v4: dict, may: dict, names: list[str]) -> tuple[dict, list[str]]:
    """Return (merged, conflicts). Adds may's entry for each name into v4's
    'primitives' map. If a name already exists in v4 with a DIFFERENT entry,
    it is left as v4's and reported as a conflict (never overwritten)."""
    merged = json.loads(json.dumps(v4))  # deep copy
    prims = merged.setdefault("primitives", {})
    may_prims = may.get("primitives", {})
    conflicts = []
    for name in names:
        if name not in may_prims:
            conflicts.append(f"{name}: not in may registry")
            continue
        if name in prims:
            if prims[name] != may_prims[name]:
                conflicts.append(f"{name}: differs between v4 and may (kept v4)")
            continue
        prims[name] = may_prims[name]
    return merged, conflicts


def main(names: list[str]):
    v4 = json.loads(V4_REG.read_text())
    may = json.loads(MAY_REG.read_text())
    merged, conflicts = merge_registry(v4, may, names)
    V4_REG.write_text(json.dumps(merged, indent=2) + "\n")
    for c in conflicts:
        print(f"CONFLICT: {c}")
    print(f"registry primitives now: {len(merged['primitives'])}")
```

- [ ] **Step 2: Write the merge test**

Add to `tools/test_sp1.py`:

```python
import sp1_merge_registry


def test_merge_is_additive_and_conflict_safe():
    v4 = {"primitives": {"keep": {"version": "0.1.0"}}}
    may = {"primitives": {"keep": {"version": "9.9.9"}, "new": {"version": "0.1.0"}}}
    merged, conflicts = sp1_merge_registry.merge_registry(v4, may, ["keep", "new", "missing"])
    assert merged["primitives"]["keep"]["version"] == "0.1.0"   # v4 kept, not clobbered
    assert merged["primitives"]["new"]["version"] == "0.1.0"    # new added
    assert any("keep" in c for c in conflicts)                  # conflict flagged
    assert any("missing" in c for c in conflicts)               # missing flagged
```

- [ ] **Step 3: Run the merge test**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4/tools && python3 -m pytest test_sp1.py::test_merge_is_additive_and_conflict_safe -v`
Expected: PASS.

- [ ] **Step 4: Copy the referenced primitive schema files (additive, no overwrite)**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
python3 - <<'PY'
import json, shutil
from pathlib import Path
may=Path("/Users/nigel/dwarf-project/dwarf-deploypackage-may/dwarf")
v4=Path("/Users/nigel/dwarf-project/dwarf-v4/dwarf")
may_reg=json.loads((may/"primitives/registry.json").read_text())["primitives"]
names=[n for n in (v4/".."/"sp1-closure"/"primitives.txt").read_text().split() if n]
copied=skipped=missing=0
for n in names:
    e=may_reg.get(n)
    if not e: missing+=1; print("MISSING in may registry:", n); continue
    rel=e.get("params_schema")
    if not rel: continue
    src=may/rel; dst=v4/rel
    if dst.exists(): skipped+=1; continue
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst); copied+=1
print(f"schemas copied={copied} skipped(existing)={skipped} missing={missing}")
PY
```
Expected: `missing=0`; `copied` ≈ (closure primitives − 10 already-present).

- [ ] **Step 5: Merge the registry + verify it loads**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
python3 -c "import sys; sys.path.insert(0,'tools'); import sp1_merge_registry as m; m.main([n for n in open('sp1-closure/primitives.txt').read().split() if n])"
PYTHONPATH=dwarf python3 -c "from profile_manager import primitives; r=primitives.load_registry('dwarf/primitives/registry.json'); print('registry loaded; entries:', len(r))"
```
Expected: `CONFLICT:` lines only for the 10 identical kept primitives at most (which won't conflict since identical → no line), registry loads without error.

- [ ] **Step 6: Commit Layer 1**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add tools/sp1_merge_registry.py tools/test_sp1.py dwarf/primitives/
git commit -m "feat(sp1): layer 1 — restore cardano-node primitive schemas + merge registry"
```

---

## Task 3: Layer 2 — restore target manifests

**Files:**
- Add: `dwarf-v4/dwarf/targets/manifests/*.yaml` (the referenced manifests)

- [ ] **Step 1: Copy referenced manifests (additive, no overwrite)**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
python3 - <<'PY'
import shutil
from pathlib import Path
may=Path("/Users/nigel/dwarf-project/dwarf-deploypackage-may/dwarf/targets/manifests")
v4=Path("/Users/nigel/dwarf-project/dwarf-v4/dwarf/targets/manifests")
v4.mkdir(parents=True, exist_ok=True)
targets=[t for t in open("sp1-closure/targets.txt").read().split() if t]
copied=skipped=missing=[]; c=s=0; miss=[]
for t in targets:
    src=may/f"{t}.yaml"; dst=v4/f"{t}.yaml"
    if not src.exists(): miss.append(t); continue
    if dst.exists(): s+=1; continue
    shutil.copy2(src,dst); c+=1
print(f"manifests copied={c} skipped(existing)={s} missing={len(miss)}")
if miss: print("MISSING manifests:", miss)
PY
```
Expected: `missing=0` (every cardano-node target_id has a manifest in may). If any are missing, record them — a scenario referencing a non-existent manifest is a data issue to flag, not silently pass.

- [ ] **Step 2: Commit Layer 2**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add dwarf/targets/manifests/
git commit -m "feat(sp1): layer 2 — restore cardano-node target manifests"
```

---

## Task 4: Layer 3 — restore scenarios + validate all

**Files:**
- Add: `dwarf-v4/dwarf/scenarios/*.yaml` (the ~197 cardano-node scenarios)
- Create: `dwarf-v4/tools/sp1_validate_all.sh`

- [ ] **Step 1: Copy the cardano-node scenarios (additive, no overwrite)**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
python3 - <<'PY'
import shutil
from pathlib import Path
may=Path("/Users/nigel/dwarf-project/dwarf-deploypackage-may/dwarf/scenarios")
v4=Path("/Users/nigel/dwarf-project/dwarf-v4/dwarf/scenarios")
names=[n for n in open("sp1-closure/scenarios.txt").read().split() if n]
c=s=0
for n in names:
    src=may/n; dst=v4/n
    if dst.exists(): s+=1; continue
    shutil.copy2(src,dst); c+=1
print(f"scenarios copied={c} skipped(existing)={s}")
PY
```
Expected: `copied` ≈ closure cardano count, `skipped=0`.

- [ ] **Step 2: Write the batch validator**

`tools/sp1_validate_all.sh`:

```bash
#!/usr/bin/env bash
# Validate every restored SP1 scenario semantically; exit non-zero if any fail.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0; ok=0
while read -r name; do
  [ -z "$name" ] && continue
  if PYTHONPATH=dwarf python3 dwarf/cardano-profile scenario validate --semantic \
       "dwarf/scenarios/$name" >/tmp/sp1val.out 2>&1; then
    ok=$((ok+1))
  else
    fail=$((fail+1)); echo "FAIL: $name"; tail -2 /tmp/sp1val.out
  fi
done < sp1-closure/scenarios.txt
echo "validated OK=$ok FAIL=$fail"
[ "$fail" -eq 0 ]
```

- [ ] **Step 3: Run the batch validator**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && chmod +x tools/sp1_validate_all.sh && ./tools/sp1_validate_all.sh`
Expected: `validated OK=<N> FAIL=0`. If any FAIL: the message names the scenario + error — fix is one of: (a) a missing primitive (add it to `primitives.txt` closure + re-run Task 2), (b) genuine schema drift (edit the scenario minimally to current schema), (c) a hidden amaru dependency (move that scenario name from `scenarios.txt` to the SP3 deferred list, `git rm` the copied file, re-run). Re-run until `FAIL=0`.

- [ ] **Step 4: Commit Layer 3**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
git add dwarf/scenarios/ tools/sp1_validate_all.sh
git commit -m "feat(sp1): layer 3 — restore ~197 cardano-node scenarios (all semantic-validate)"
```

---

## Task 5: Spot-run + additive-diff guard

**Files:** none (verification only).

- [ ] **Step 1: Spot-run two restored scenarios locally**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
for s in cardano-node-mini-protocol-chainsync-fuzz runtime-substrate-serdes-keepalive-cookie-mismatch-example-smoke; do
  echo "=== $s ==="
  PYTHONPATH=dwarf python3 dwarf/cardano-profile scenario run "dwarf/scenarios/$s.yaml" \
    --runs-dir /tmp/sp1-runs --state-dir /tmp/sp1-state 2>&1 | tail -6
done
```
Expected: the executor drives each scenario without an executor/registry error (a target being `UNBUILT` may produce a clean scenario-level failure — that is acceptable; an *executor* exception/traceback is not). Record which outcome each produced.

- [ ] **Step 2: Verify the restore was additive-only (no pre-existing artifact changed)**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
echo "=== any MODIFY/DELETE of pre-existing files in this branch? (want: only additions + registry.json + tools) ==="
git diff --name-status main...HEAD -- dwarf/scenarios dwarf/primitives dwarf/targets | grep -vE '^A' || echo "only additions (good)"
```
Expected: the only non-`A` entry is `M dwarf/primitives/registry.json` (the additive merge). Any other `M`/`D` on a pre-existing scenario/primitive/manifest is a violation — investigate.

- [ ] **Step 3: Reconcile the membership count**

```bash
cd /Users/nigel/dwarf-project/dwarf-v4
echo "restored: $(git diff --name-only main...HEAD -- dwarf/scenarios | wc -l)"
echo "deferred to SP3: $(wc -l < sp1-closure/deferred.txt)"
echo "(restored + deferred should equal 436)"
```
Expected: restored + deferred == 436.

---

## Task 6: Finish the branch

- [ ] **Step 1: Final full validation pass**

Run: `cd /Users/nigel/dwarf-project/dwarf-v4 && ./tools/sp1_validate_all.sh`
Expected: `FAIL=0`.

- [ ] **Step 2: Finish per the finishing-a-development-branch skill**

Announce and use `superpowers:finishing-a-development-branch` (verify tests → present merge/PR options → execute). Tests = `tools/test_sp1.py` + the batch validator both green.

---

## Self-Review

**1. Spec coverage:**
- "Restore ~197 cardano-node scenarios + closure" → Tasks 1 (closure), 2 (primitives/registry), 3 (manifests), 4 (scenarios). ✓
- "Additive, never clobber" → no-overwrite guards in every copy step + Task 5 Step 2 additive-diff guard. ✓
- "Dependency-layered, validate per layer" → Task 2 registry-load check, Task 3 missing-manifest check, Task 4 semantic validate-all. ✓
- "Defer differential + amaru to SP3" → closure script routes non-cardano-node to `deferred.txt`; Task 4 Step 3 reclassification path; Task 5 Step 3 reconciliation. ✓
- "Validation gate = definitions not builds" → Task 4 semantic validate; Task 5 spot-run tolerates UNBUILT targets. ✓
- "Registry merge format risk" → Task 2 merge function + test, conflict-flagging. ✓
- "Out of scope: profiles, generator, amaru, builds" → none touched. ✓

**2. Placeholder scan:** No TBD/TODO; every step has real code or an exact command + expected output. The `~197`/`[150,210]` range is intentional (the closure script computes the exact set; the test asserts a sane band + reconciliation, not a hardcoded count that could be brittle).

**3. Type consistency:** `merge_registry(v4, may, names) -> (merged, conflicts)` used identically in the test and `main`. `compute()` keys (`delta/cardano/deferred/primitives/targets`) consistent across script, test, and the copy steps reading `sp1-closure/*.txt`. Paths consistent (`MAY`/`V4`). ✓
