import importlib.util
from pathlib import Path

WL = Path(__file__).resolve().parents[1] / "dwarf" / "antithesis_workload" / "workload.py"


def _load():
    spec = importlib.util.spec_from_file_location("dwarf_workload", WL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_workload_assertions_run_without_sdk(monkeypatch, tmp_path):
    # With no Antithesis runtime, SDK calls must no-op (or write local), never crash.
    monkeypatch.setenv("ANTITHESIS_SDK_LOCAL_OUTPUT", str(tmp_path / "sdk.jsonl"))
    mod = _load()
    # drive_once returns a dict describing what it asserted; dry-run uses a fake transport.
    result = mod.drive_once(transport=mod.NullTransport())
    assert result["assertions"] >= 3  # liveness + no-crash + one domain invariant
    assert result["panic"] is False


def test_mutate_cbor_is_deterministic_per_seed():
    mod = _load()
    a = mod.mutate_cbor(b"\x82\x01\x02", seed=7)
    b = mod.mutate_cbor(b"\x82\x01\x02", seed=7)
    assert a == b


def test_drive_differential_agreement_counts_assertions():
    mod = _load()
    t1 = mod.NullTransport(label="cardano-node-1", accepted=True)
    t2 = mod.NullTransport(label="amaru-1", accepted=True)
    r = mod.drive_differential(transports=[t1, t2])
    # 3 per-node assertions x2 nodes + 1 differential = 7
    assert r["assertions"] == 7
    assert r["targets"] == 2
    assert r["agree"] is True
    assert r["panic"] is False


def test_drive_differential_flags_divergence():
    mod = _load()
    t1 = mod.NullTransport(label="cardano-node-1", accepted=True)
    t2 = mod.NullTransport(label="amaru-1", accepted=False)
    r = mod.drive_differential(transports=[t1, t2])
    assert r["agree"] is False


def test_parse_targets_reads_workload_targets(monkeypatch):
    mod = _load()
    monkeypatch.setenv("WORKLOAD_TARGETS", "cardano-node-1=cardano-node-1:3001,amaru-1=amaru-1:3001")
    specs = mod.parse_targets()
    assert specs == [("cardano-node-1", "cardano-node-1:3001"), ("amaru-1", "amaru-1:3001")]


def test_parse_targets_falls_back_to_amaru_target(monkeypatch):
    mod = _load()
    monkeypatch.delenv("WORKLOAD_TARGETS", raising=False)
    monkeypatch.setenv("AMARU_TARGET", "amaru-1:3001")
    assert mod.parse_targets() == [("amaru-1", "amaru-1:3001")]
