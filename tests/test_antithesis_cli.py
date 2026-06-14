import json
from profile_manager.cli import build_parser, cmd_antithesis


def test_parser_accepts_antithesis_build():
    args = build_parser().parse_args(
        ["antithesis", "build", "profile-l-amaru-closed-devnet", "--out", "/tmp/x", "--json"]
    )
    assert args.command == "antithesis"
    assert args.antithesis_command == "build"
    assert args.profile_id == "profile-l-amaru-closed-devnet"


def test_cmd_antithesis_build_writes_bundle(tmp_path, capsys):
    args = build_parser().parse_args(
        ["antithesis", "build", "profile-l-amaru-closed-devnet",
         "--out", str(tmp_path), "--registry", "REG", "--tag", "x", "--json"]
    )
    rc = cmd_antithesis(args)
    assert rc == 0
    assert (tmp_path / "config" / "docker-compose.yaml").exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "ok"
    assert "config/docker-compose.yaml" in payload["written"]


def test_emitted_repo_bundle_validates():
    from pathlib import Path
    from profile_manager.moog import validate_moog_asset, moog_asset_summary
    bundle = Path(__file__).resolve().parents[1] / "antithesis" / "amaru-single"
    summary = moog_asset_summary(validate_moog_asset(str(bundle)))
    assert summary["state"] in {"ready", "warn"}
    assert summary["error_count"] == 0
