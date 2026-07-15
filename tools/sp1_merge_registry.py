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
