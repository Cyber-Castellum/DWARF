import json, sys
from pathlib import Path
import pytest

ROOT = Path("${DWARF_ROOT}")
sys.path.insert(0, str(ROOT / "dwarf"))
from profile_manager import scenario as scn
from profile_manager import antithesis_generator as gen

HEADER = ROOT / "dwarf/scenarios/cardano-node-cbor-block-header-fuzz-structured.yaml"
BLOCK = ROOT / "dwarf/scenarios/cardano-node-cbor-block-fuzz-structured.yaml"


def _load(p):
    return scn.load_scenario(p)


def test_fuzz_spec_extracts_decoder_shape_seed():
    s = _load(HEADER)
    fs = gen.fuzz_spec(s)
    assert fs["target_decoder"] == "cardano-node-cbor-decode-block-header"
    assert fs["cbor_shape"]["type"] == "array"            # the Conway header shape
    assert fs["seed"] == "0xCAFE0202"
    assert fs["mutation_rate"] == 0.05
    assert set(fs["asserted_properties"]) == {
        "parse_succeeds_or_clean_error", "roundtrip_equals_original"
    }


def test_map_assertions_emits_sometimes_reachable_only():
    s = _load(HEADER)
    cat = gen.map_assertions(s)
    assert len(cat) >= 1
    kinds = {a["kind"] for a in cat}
    assert kinds <= {"sometimes", "reachable"}            # never "always"
    assert all(a["id"] and a["message"] for a in cat)


def test_map_assertions_zero_is_error():
    s = _load(HEADER)
    s.assertions.clear()
    with pytest.raises(gen.GeneratorError):
        gen.map_assertions(s)


def test_derive_adversary_header_path():
    s = _load(HEADER)
    adv = gen.derive_adversary(s)
    assert adv["image"] == gen.ADVERSARY_IMAGE
    assert adv["protocol"] == "chainsync"
    assert adv["shape"] == "block-header"
    args = adv["command_args"]
    assert "--mutation-rate" in args and "0.05" in args
    assert "--network-magic" in args
    assert "--listen-port" in args
    assert "--upstream" in args
    assert "--seed" in args


def test_derive_adversary_refuses_unmapped_decoder():
    # an unknown decoder (not in ADVERSARY_MODES) is refused with a clear error.
    s = _load(HEADER)
    s.load[0].params["target_id"] = "cardano-node-cbor-decode-bogus"
    with pytest.raises(gen.GeneratorError) as ei:
        gen.derive_adversary(s)
    assert "no adversary mapping" in str(ei.value)


def test_derive_adversary_refuses_amaru():
    s = _load(HEADER)
    s.target["implementation"] = "amaru"
    with pytest.raises(gen.GeneratorError) as ei:
        gen.derive_adversary(s)
    assert "SP3" in str(ei.value)


def test_select_testnet_base_returns_asset_dir():
    s = _load(HEADER)
    base = gen.select_testnet_base(s)
    assert (base / "testnet.yaml").exists()
    assert (base / "relay-dwarf-topology.json").exists()


def test_render_bundle_files_and_labels():
    s = _load(HEADER)
    arts = gen.render_bundle(s, registry="reg.example/x", tag="t1")
    files = arts.files
    assert "config/docker-compose.yaml" in files
    assert "relay-dwarf-topology.json" in files
    assert any(p.startswith("test/") for p in files)
    assert "dwarf-manifest.json" in files
    compose = files["config/docker-compose.yaml"]
    assert gen.ADVERSARY_IMAGE in compose
    assert "com.antithesis.exclude_from_faults" in compose
    assert "build:" not in compose
    assert "--mutation-rate" in compose
    man = json.loads(files["dwarf-manifest.json"])
    assert man["scenario_id"] == s.id
    assert man["adversary"]["protocol"] == "chainsync"
    assert len(man["assertions"]) >= 1


def test_composer_parallel_driver_no_setup_complete():
    s = _load(HEADER)
    arts = gen.render_bundle(s, registry="reg.example/x", tag="t1")
    drivers = {k: v for k, v in arts.files.items() if "parallel_driver" in k}
    assert drivers
    for body in drivers.values():
        assert "setup_complete" not in body and "antithesis_setup" not in body


def _write_bundle(tmp_path, scenario):
    arts = gen.render_bundle(scenario, registry="reg.example/x", tag="t1")
    from profile_manager.backends.base import write_artifacts
    write_artifacts(arts, str(tmp_path))
    return tmp_path


def test_verify_generated_bundle_green(tmp_path):
    s = _load(HEADER)
    b = _write_bundle(tmp_path, s)
    res = gen.verify_generated_bundle(str(b))
    assert res["state"] == "pass", res


def test_verify_catches_build_context(tmp_path):
    s = _load(HEADER)
    b = _write_bundle(tmp_path, s)
    p = b / "config/docker-compose.yaml"
    p.write_text(p.read_text() + "\n  evil:\n    build: .\n")
    res = gen.verify_generated_bundle(str(b))
    assert res["state"] == "fail" and any("build:" in r for r in res["reasons"])


def test_verify_catches_missing_fault_label(tmp_path):
    s = _load(HEADER)
    b = _write_bundle(tmp_path, s)
    p = b / "config/docker-compose.yaml"
    p.write_text(p.read_text().replace("com.antithesis.exclude_from_faults", "x_disabled"))
    res = gen.verify_generated_bundle(str(b))
    assert res["state"] == "fail" and any("fault" in r.lower() for r in res["reasons"])


def test_verify_catches_setup_complete_in_driver(tmp_path):
    s = _load(HEADER)
    b = _write_bundle(tmp_path, s)
    drv = next(b.glob("test/**/parallel_driver_*.sh"))
    drv.write_text(drv.read_text() + '\necho \'{"antithesis_setup":{}}\'\n')
    res = gen.verify_generated_bundle(str(b))
    assert res["state"] == "fail" and any("setup" in r.lower() for r in res["reasons"])


def test_generate_native_test_writes_and_verifies(tmp_path):
    out = tmp_path / "bundle"
    res = gen.generate_native_test(str(HEADER), out_dir=str(out),
                                   registry="reg.example/x", tag="t1")
    assert res["verify"]["state"] == "pass", res
    assert (out / "config/docker-compose.yaml").exists()
    assert res["bundle_dir"] == str(out)


def test_overlap_same_decoder_property_seed():
    s = _load(HEADER)
    fs = gen.fuzz_spec(s)
    load = s.load[0]                       # the local executor reads the same ref
    assert fs["target_decoder"] == load.params["target_id"]
    assert fs["cbor_shape"] == load.params["shape"]
    assert fs["seed"] == s.seed
    # mutation engines differ by design -- we did NOT claim kind parity
    assert "mutation_kinds" not in fs


def test_block_scenario_now_builds():
    s = _load(BLOCK)
    adv = gen.derive_adversary(s)            # no longer raises
    assert adv["protocol"] == "blockfetch"
    assert adv["shape"] == "block"
    assert "--protocol" in adv["command_args"] and "blockfetch" in adv["command_args"]
    assert "--cbor-shape" in adv["command_args"] and "block" in adv["command_args"]


def test_header_scenario_still_emits_protocol_flags():
    s = _load(HEADER)
    adv = gen.derive_adversary(s)
    assert "--protocol" in adv["command_args"] and "chainsync" in adv["command_args"]
    assert "--cbor-shape" in adv["command_args"] and "block-header" in adv["command_args"]
    assert adv["image"] == gen.ADVERSARY_IMAGE   # bumped to 0.2.0


def test_amaru_still_refused_sp3_boundary():
    # amaru/differential remain out of scope (Track B) — the SP3 boundary holds.
    s = _load(HEADER)
    s.target["implementation"] = "amaru"
    with pytest.raises(gen.GeneratorError) as ei:
        gen.derive_adversary(s)
    assert "SP3" in str(ei.value)


TXBODY = ROOT / "dwarf/scenarios/cardano-node-cbor-tx-body-fuzz-structured.yaml"
CERT = ROOT / "dwarf/scenarios/cardano-node-cbor-certificate-fuzz-structured.yaml"
AUX = ROOT / "dwarf/scenarios/cardano-node-cbor-auxiliary-data-fuzz-structured.yaml"


def test_tx_scenarios_now_build():
    for path, shape in [(TXBODY, "tx-body"), (CERT, "certificate"), (AUX, "auxiliary-data")]:
        adv = gen.derive_adversary(_load(path))   # no longer raises
        assert adv["protocol"] == "txsubmission", path
        assert adv["shape"] == shape, path
        assert "--protocol" in adv["command_args"] and "txsubmission" in adv["command_args"]
        assert "--cbor-shape" in adv["command_args"] and shape in adv["command_args"]


def test_image_bumped_to_03():
    assert gen.ADVERSARY_IMAGE.endswith(":0.5.1")
    # header + block still build (regression)
    assert gen.derive_adversary(_load(HEADER))["protocol"] == "chainsync"
    assert gen.derive_adversary(_load(BLOCK))["protocol"] == "blockfetch"
