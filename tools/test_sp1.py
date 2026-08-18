import sp1_closure
import sp1_merge_registry


def test_closure_classification_is_total():
    # Invariant that holds before AND after the restore: every may->v4
    # delta scenario is classified exactly once, and cardano-node splits
    # cleanly into eligible + blocked. (Counts shrink as SP1/SP3 restore
    # scenarios into v4 — only the totality invariant is permanent.)
    r = sp1_closure.compute()
    assert len(r["eligible"]) + len(r["blocked"]) + len(r["deferred"]) == len(r["delta"])
    assert len(r["cardano"]) == len(r["eligible"]) + len(r["blocked"])

    import json
    from pathlib import Path
    reg = set(json.loads((Path(sp1_closure.MAY) / "primitives" / "registry.json").read_text()).get("primitives", {}).keys())
    # eligible closure references only registered primitives;
    # blocked scenarios only ever reference unregistered ones.
    assert all(p in reg for p in r["primitives"])
    for prims in r["blocked"].values():
        assert all(p not in reg for p in prims)


def test_merge_is_additive_and_conflict_safe():
    v4 = {"primitives": {"keep": {"version": "0.1.0"}}}
    may = {"primitives": {"keep": {"version": "9.9.9"}, "new": {"version": "0.1.0"}}}
    merged, conflicts = sp1_merge_registry.merge_registry(v4, may, ["keep", "new", "missing"])
    assert merged["primitives"]["keep"]["version"] == "0.1.0"   # v4 kept, not clobbered
    assert merged["primitives"]["new"]["version"] == "0.1.0"    # new added
    assert any("keep" in c for c in conflicts)                  # conflict flagged
    assert any("missing" in c for c in conflicts)               # missing flagged
