from pathlib import Path

from scripts import validate_scenarios as gate


def test_ci_moog_asset_gate_rejects_invalid_fault_exclusion(tmp_path, monkeypatch):
    bundle = tmp_path / "antithesis" / "broken"
    bundle.mkdir(parents=True)
    (bundle / "docker-compose.yaml").write_text(
        """services:
  oracle:
    image: example/oracle:latest
    labels:
      com.antithesis.exclude_from_faults: "true"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "MOOG_ASSET_DIRS", (bundle,), raising=False)

    check = getattr(gate, "check_moog_assets", None)
    assert check is not None
    failures, ok, messages = check()

    assert failures == 1
    assert ok == 0
    assert 'oracle: unknown fault class "true"' in messages[0]


def test_ci_moog_asset_set_covers_documented_launch_bundles():
    relative = {
        str(Path(path).relative_to(gate.REPO_ROOT))
        for path in gate.MOOG_ASSET_DIRS
    }

    assert relative == {
        "antithesis/cardano_amaru_adversarial",
        "antithesis/cardano_node_dwarf",
    }
