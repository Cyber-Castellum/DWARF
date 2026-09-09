import json
from types import SimpleNamespace

import profile_manager.data.lifecycle as lifecycle


def _write_runtime_case(state_dir):
    testcase_dir = state_dir / "testcases"
    testcase_dir.mkdir(parents=True)
    (testcase_dir / "tc-runtime.json").write_text(
        json.dumps(
            {
                "case_id": "tc-runtime",
                "bucket_id": "tb-runtime",
                "classification": "runtime_anomaly",
                "triage_reason": "regression-test",
                "target_implementation": "amaru",
            }
        ),
        encoding="utf-8",
    )
    return testcase_dir


def test_local_lifecycle_uses_configured_runtime_state(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    testcase_dir = _write_runtime_case(state_dir)
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state_dir))

    summary = lifecycle._local_testcase_lifecycle_summary()

    assert summary["state_root"] == str(testcase_dir)
    assert summary["case_count"] == 1
    assert summary["runtime_anomaly_count"] == 1


def test_control_shim_lifecycle_does_not_send_arbitrary_ssh(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    testcase_dir = _write_runtime_case(state_dir)
    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(state_dir))
    monkeypatch.setenv("ADA2_DWARF_CONTROL_SHIM", "1")
    monkeypatch.setattr(lifecycle, "config_exists", lambda: True)
    monkeypatch.setattr(
        lifecycle,
        "load_config",
        lambda: SimpleNamespace(remote_base_path="/opt/dwarf/cardano-profiles"),
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("control-shim mode must not send arbitrary remote Python")

    monkeypatch.setattr(lifecycle, "ssh_command", fail_if_called)

    summary = lifecycle._live_testcase_lifecycle_summary()

    assert summary["state_root"] == str(testcase_dir)
    assert summary["case_count"] == 1
    assert "remote_error" not in summary
