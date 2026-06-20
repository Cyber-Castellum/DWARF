import random
import sys

import pytest

sys.path.insert(0, "${DWARF_ROOT}/dwarf")
from profile_manager import scenario as s
from profile_manager import primitives as p


def test_generate_cbor_handles_all_shape_types():
    rng = random.Random(1)
    # 'map' is the shape that crashed with UnboundLocalError: 'out'
    m = p.generate_cbor(
        {"type": "map", "entries": [(0, {"type": "uint"}), ("k", {"type": "bytes", "length": 2})]},
        rng,
    )
    assert isinstance(m, (bytes, bytearray)) and len(m) > 0
    for shape in (
        {"type": "tag", "tag": 24, "inner": {"type": "null"}},
        {"type": "any"},
        {"type": "array", "elements": [{"type": "uint"}, {"type": "bool"}]},
    ):
        assert p.generate_cbor(shape, rng)
    # unknown type now raises a clear error (not UnboundLocalError)
    with pytest.raises(ValueError):
        p.generate_cbor({"type": "bogus"}, rng)


def test_gate_predicate():
    # green: clean run + assertions passed, none failed
    assert s.verify_gate("pass", {"fail": 0, "pass": 3, "total": 3}) == ("pass", "")
    # red: a failing assertion
    assert s.verify_gate("pass", {"fail": 1, "pass": 2, "total": 3})[0] == "fail"
    # red: no assertions ran at all (the "didn't actually exercise the target" trap)
    assert s.verify_gate("pass", {"fail": 0, "pass": 0, "total": 0})[0] == "fail"
    # red: run did not finish cleanly
    assert s.verify_gate("error", {"fail": 0, "pass": 1, "total": 1})[0] == "fail"
