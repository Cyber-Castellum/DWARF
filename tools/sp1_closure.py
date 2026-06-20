"""SP1 closure: classify the may->v4 scenario delta and compute the
cardano-node membership + its primitive/target dependency closure."""
import glob
import json
import os
from pathlib import Path

MAY = Path("${HOME}/dwarf-project/dwarf-deploypackage-may/dwarf")
V4 = Path("${DWARF_ROOT}/dwarf")
OUT = Path("${DWARF_ROOT}/sp1-closure")


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
    registered = set(load(MAY / "primitives" / "registry.json").get("primitives", {}).keys())

    cardano, deferred = [], []
    eligible, blocked = [], {}
    prim_refs, target_refs = set(), set()
    for fn in delta:
        scn = load(may_scn[fn])
        impl = (scn.get("target") or {}).get("implementation")
        prims = walk_values(scn, "primitive")
        targets = walk_values(scn, "target_id")
        is_cardano = impl == "cardano-node" and not any("amaru" in t for t in targets)
        if not is_cardano:
            deferred.append(fn)  # amaru + differential -> SP3
            continue
        cardano.append(fn)
        missing = sorted(p for p in prims if p not in registered)
        if missing:
            # references a primitive never implemented even in the may
            # source -> can't semantic-validate; backlog (needs-primitive).
            blocked[fn] = missing
            continue
        eligible.append(fn)
        prim_refs |= prims
        target_refs |= targets
    return {
        "delta": delta,
        "cardano": sorted(cardano),
        "eligible": sorted(eligible),
        "blocked": dict(sorted(blocked.items())),
        "deferred": sorted(deferred),
        "primitives": sorted(prim_refs),
        "targets": sorted(target_refs),
        "may_scn": may_scn,
    }


def main():
    r = compute()
    OUT.mkdir(exist_ok=True)
    (OUT / "scenarios.txt").write_text("\n".join(r["eligible"]) + "\n")
    (OUT / "primitives.txt").write_text("\n".join(r["primitives"]) + "\n")
    (OUT / "targets.txt").write_text("\n".join(r["targets"]) + "\n")
    (OUT / "deferred.txt").write_text("\n".join(r["deferred"]) + "\n")
    (OUT / "blocked.txt").write_text(
        "".join(f"{fn}\t{','.join(ms)}\n" for fn, ms in r["blocked"].items())
    )
    print(f"delta scenarios:            {len(r['delta'])}")
    print(f"cardano-node total:         {len(r['cardano'])}")
    print(f"  eligible (SP1 restore):   {len(r['eligible'])}")
    print(f"  blocked (needs-primitive):{len(r['blocked'])}")
    print(f"deferred (amaru+diff, SP3): {len(r['deferred'])}")
    print(f"reconcile (elig+blk+defer): {len(r['eligible']) + len(r['blocked']) + len(r['deferred'])}")
    print(f"primitives referenced:      {len(r['primitives'])}")
    print(f"target manifests referenced:{len(r['targets'])}")


if __name__ == "__main__":
    main()
