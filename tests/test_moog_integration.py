import json

from profile_manager.cli import build_parser, cmd_moog
from profile_manager.config import DeploymentConfig, parse_config_value
from profile_manager.data.cli import cli_groups
from profile_manager.data.operate_config import apply_moog_setup_form, moog_setup_payload
from profile_manager.dashboard import build_dashboard_status_payload
from profile_manager.data.operate_status import moog_status_tile
from profile_manager.moog import (
    DEFAULT_MOOG_CONFIG,
    build_moog_bootstrap_command,
    build_moog_bootstrap_plan,
    build_moog_create_test_command,
    build_moog_create_test_plan,
    build_moog_health_command,
    build_moog_preflight_report,
    build_moog_readiness,
    build_moog_registration_plan,
    build_moog_registration_submit_command,
    moog_asset_summary,
    moog_bootstrap_summary,
    moog_create_test_summary,
    moog_health_summary,
    moog_preflight_summary,
    moog_registration_summary,
    moog_readiness_summary,
    parse_moog_bootstrap_result,
    parse_moog_health_result,
    scaffold_moog_asset,
    validate_moog_asset,
)
from profile_manager.remote import CommandResult


def test_deployment_config_round_trips_moog_block():
    value = {
        "enabled": True,
        "deploy_root": "${HOME}/moog-deploy",
        "moog_binary": "${HOME}/bin/moog",
        "secrets_root": "${HOME}/moog-secrets",
        "mpfs_host": "https://mpfs.plutimus.com",
        "token_id": "21c523c3b4565f1fc1ad7e54e82ca976f60997d8e7e9946826813fabf341069b",
        "requester_wallet_id": "moog-requester",
        "oracle_service": "moog-oracle.service",
    }
    config = DeploymentConfig.from_dict({"moog": value})

    assert config.moog["enabled"] is True
    assert config.moog["deploy_root"] == "${HOME}/moog-deploy"
    assert config.to_dict()["moog"]["token_id"].startswith("21c523")


def test_parse_config_value_accepts_json_object_for_moog():
    parsed = parse_config_value("moog", '{"enabled": false, "deploy_root": "/tmp/moog"}')

    assert parsed == {"enabled": False, "deploy_root": "/tmp/moog"}


def test_moog_setup_payload_prefers_environment_and_masks_secret_values():
    config = DeploymentConfig.from_dict({
        "moog": {
            "github_user": "config-user",
            "github_repo": "config-org/config-repo",
            "github_pat": "ghp_config",
            "antithesis_password": "config-password",
        }
    })
    payload = moog_setup_payload(
        config,
        environ={
            "MOOG_GITHUB_USER": "env-user",
            "MOOG_GITHUB_PAT": "ghp_env",
            "MOOG_ANTITHESIS_LAUNCH_URL": "https://tenant.antithesis.com/api/v1/launch/tenant",
        },
    )
    fields = {field["key"]: field for field in payload["fields"]}

    assert fields["github_user"]["value"] == "env-user"
    assert fields["github_user"]["source"] == "env:MOOG_GITHUB_USER"
    assert fields["github_repo"]["value"] == "config-org/config-repo"
    assert fields["github_repo"]["source"] == "config"
    assert fields["github_pat"]["value"] == ""
    assert fields["github_pat"]["configured"] is True
    assert fields["github_pat"]["source"] == "env:MOOG_GITHUB_PAT"
    assert fields["antithesis_password"]["value"] == ""
    assert fields["antithesis_password"]["configured"] is True
    assert fields["antithesis_password"]["source"] == "config"
    assert fields["antithesis_launch_url"]["value"] == "https://tenant.antithesis.com/api/v1/launch/tenant"


def test_apply_moog_setup_form_saves_values_and_preserves_blank_secrets():
    config = DeploymentConfig.from_dict({
        "moog": {
            "github_pat": "ghp_existing",
            "antithesis_password": "existing-password",
        }
    })

    updated = apply_moog_setup_form(
        config,
        {
            "github_user": ["real-user"],
            "github_repo": ["example-org/example-repo"],
            "github_pat": [""],
            "antithesis_launch_url": ["https://amaru-cardano.antithesis.com/api/v1/launch/amaru-cardano"],
            "antithesis_user": ["pragma"],
            "antithesis_password": ["new-password"],
            "antithesis_registry": ["us-central1-docker.pkg.dev/molten-verve-216720/cardano-repository"],
            "target_directory": ["antithesis"],
            "target_commit": ["abc123"],
            "duration_hours": ["2"],
            "enabled": ["true"],
        },
    )

    assert updated.moog["github_user"] == "real-user"
    assert updated.moog["github_repo"] == "example-org/example-repo"
    assert updated.moog["github_pat"] == "ghp_existing"
    assert updated.moog["antithesis_password"] == "new-password"
    assert updated.moog["antithesis_user"] == "pragma"
    assert updated.moog["antithesis_registry"] == "us-central1-docker.pkg.dev/molten-verve-216720/cardano-repository"
    assert updated.moog["target_directory"] == "antithesis"
    assert updated.moog["target_commit"] == "abc123"
    assert updated.moog["duration_hours"] == "2"
    assert updated.moog["enabled"] is True


def test_moog_health_command_checks_public_state_without_reading_secrets():
    command = build_moog_health_command(DEFAULT_MOOG_CONFIG)

    assert "${HOME}/moog-deploy" in command
    assert "${HOME}/bin/moog" in command
    assert "requester-wallet-info.json" in command
    assert "oracle-wallet-info.json" in command
    assert "moog-oracle.service" in command
    assert "cat ${HOME}/moog-secrets" not in command
    assert "requester.json" not in command
    assert "oracle.json" not in command


def test_parse_moog_health_result_summarizes_remote_json():
    remote_payload = {
        "checks": [
            {"id": "binary", "state": "ok", "detail": "moog 0.5.1.3"},
            {"id": "deploy_root", "state": "ok", "detail": "${HOME}/moog-deploy"},
            {"id": "oracle_service_active", "state": "warn", "detail": "inactive"},
        ],
        "wallets": {
            "requester": {"address": "addr_test1...", "owner": "dd93"},
            "oracle": {"address": "addr_test1...", "owner": "a09e"},
        },
    }
    result = CommandResult(
        returncode=0,
        stdout=json.dumps(remote_payload),
        stderr="",
        rendered_command="ssh build-host moog-health",
    )

    parsed = parse_moog_health_result(result)
    summary = moog_health_summary(parsed)

    assert parsed["state"] == "warn"
    assert summary["state"] == "warn"
    assert summary["check_count"] == 3
    assert summary["ok_count"] == 2
    assert summary["warn_count"] == 1
    assert summary["error_count"] == 0
    assert summary["requester_address"] == "addr_test1..."


def test_cli_parser_accepts_moog_status_json():
    args = build_parser().parse_args(["moog", "status", "--json"])

    assert args.command == "moog"
    assert args.moog_command == "status"
    assert args.json is True


def test_cli_parser_accepts_moog_bootstrap_json():
    args = build_parser().parse_args(["moog", "bootstrap", "--json"])

    assert args.command == "moog"
    assert args.moog_command == "bootstrap"
    assert args.approve is False
    assert args.json is True


def test_cli_parser_accepts_moog_bootstrap_approve_json():
    args = build_parser().parse_args(["moog", "bootstrap", "--approve", "--json"])

    assert args.command == "moog"
    assert args.moog_command == "bootstrap"
    assert args.approve is True
    assert args.json is True


def test_moog_bootstrap_plan_is_opt_in_and_includes_healthcheck_plan():
    plan = build_moog_bootstrap_plan(DEFAULT_MOOG_CONFIG)
    summary = moog_bootstrap_summary(plan)
    actions = {action["id"]: action for action in plan["actions"]}
    healthcheck_commands = [step["command"] for step in plan["healthcheck_plan"]]

    assert plan["state"] == "planned"
    assert summary["state"] == "planned"
    assert actions["create_deploy_root"]["mode"] == "opt-in"
    assert actions["create_secrets_root"]["mode"] == "opt-in"
    assert actions["install_binaries"]["state"] == "external"
    assert "cardano-profile moog healthcheck --json" in healthcheck_commands
    assert "cardano-profile moog preflight --asset-dir <dir> --repo <org/repo> --github-user <user> --directory <path> --commit <sha> --json" in healthcheck_commands


def test_moog_bootstrap_command_prepares_dirs_without_reading_or_starting_secrets():
    command = build_moog_bootstrap_command(DEFAULT_MOOG_CONFIG)

    assert "${HOME}/moog-deploy" in command
    assert "${HOME}/moog-secrets" in command
    assert "requester-wallet-info.json" in command
    assert "oracle-wallet-info.json" in command
    assert "cat ${HOME}/moog-secrets" not in command
    assert "requester.json" not in command
    assert "oracle.json" not in command
    assert "systemctl --user start" not in command
    assert "systemctl --user enable" not in command


def test_parse_moog_bootstrap_result_summarizes_remote_json():
    remote_payload = {
        "state": "warn",
        "applied": True,
        "checks": [
            {"id": "deploy_root", "state": "ok", "detail": "${HOME}/moog-deploy"},
            {"id": "binary", "state": "warn", "detail": "${HOME}/bin/moog"},
        ],
        "healthcheck_plan": [{"command": "cardano-profile moog healthcheck --json"}],
    }
    result = CommandResult(
        returncode=0,
        stdout=json.dumps(remote_payload),
        stderr="",
        rendered_command="ssh build-host moog-bootstrap",
    )

    parsed = parse_moog_bootstrap_result(result)
    summary = moog_bootstrap_summary(parsed)

    assert parsed["state"] == "warn"
    assert parsed["applied"] is True
    assert summary["warn_count"] == 1
    assert summary["ok_count"] == 1
    assert summary["healthcheck_steps"] == 1


def test_cli_parser_accepts_moog_readiness_json():
    args = build_parser().parse_args([
        "moog",
        "readiness",
        "--repo",
        "cardano-foundation/moog",
        "--github-user",
        "cfhal",
        "--json",
    ])

    assert args.command == "moog"
    assert args.moog_command == "readiness"
    assert args.repo == "cardano-foundation/moog"
    assert args.github_user == "cfhal"
    assert args.json is True


def test_cli_parser_accepts_moog_registration_plan_json():
    args = build_parser().parse_args([
        "moog",
        "registration-plan",
        "--repo",
        "cardano-foundation/moog",
        "--github-user",
        "cfhal",
        "--json",
    ])

    assert args.command == "moog"
    assert args.moog_command == "registration-plan"
    assert args.repo == "cardano-foundation/moog"
    assert args.github_user == "cfhal"
    assert args.json is True


def test_cli_parser_accepts_moog_registration_submit_json():
    args = build_parser().parse_args([
        "moog",
        "registration-submit",
        "--repo",
        "cardano-foundation/moog",
        "--github-user",
        "cfhal",
        "--approve",
        "--json",
    ])

    assert args.command == "moog"
    assert args.moog_command == "registration-submit"
    assert args.repo == "cardano-foundation/moog"
    assert args.github_user == "cfhal"
    assert args.approve is True
    assert args.json is True


def test_cli_parser_accepts_moog_create_test_plan_json(tmp_path):
    args = build_parser().parse_args([
        "moog",
        "create-test-plan",
        "--repo",
        "example-org/example-repo",
        "--github-user",
        "example-user",
        "--directory",
        "antithesis",
        "--commit",
        "abc123",
        "--try",
        "2",
        "--duration-hours",
        "3",
        "--asset-dir",
        str(tmp_path),
        "--json",
    ])

    assert args.command == "moog"
    assert args.moog_command == "create-test-plan"
    assert args.repo == "example-org/example-repo"
    assert args.github_user == "example-user"
    assert args.directory == "antithesis"
    assert args.commit == "abc123"
    assert args.try_number == 2
    assert args.duration_hours == 3
    assert args.asset_dir == str(tmp_path)
    assert args.json is True


def test_cli_parser_accepts_moog_asset_scaffold_json(tmp_path):
    args = build_parser().parse_args([
        "moog",
        "asset",
        "scaffold",
        "--to",
        str(tmp_path / "asset"),
        "--json",
    ])

    assert args.command == "moog"
    assert args.moog_command == "asset"
    assert args.asset_command == "scaffold"
    assert args.to == str(tmp_path / "asset")
    assert args.json is True


def test_cli_parser_accepts_moog_asset_validate_json(tmp_path):
    args = build_parser().parse_args([
        "moog",
        "asset",
        "validate",
        "--asset-dir",
        str(tmp_path / "asset"),
        "--json",
    ])

    assert args.command == "moog"
    assert args.moog_command == "asset"
    assert args.asset_command == "validate"
    assert args.asset_dir == str(tmp_path / "asset")
    assert args.json is True


def test_cli_parser_accepts_moog_preflight_json(tmp_path):
    args = build_parser().parse_args([
        "moog",
        "preflight",
        "--repo",
        "example-org/example-repo",
        "--github-user",
        "example-user",
        "--directory",
        "antithesis",
        "--commit",
        "abc123",
        "--asset-dir",
        str(tmp_path / "asset"),
        "--json",
    ])

    assert args.command == "moog"
    assert args.moog_command == "preflight"
    assert args.repo == "example-org/example-repo"
    assert args.github_user == "example-user"
    assert args.directory == "antithesis"
    assert args.commit == "abc123"
    assert args.asset_dir == str(tmp_path / "asset")
    assert args.json is True


def test_moog_asset_scaffold_creates_target_agnostic_compose(tmp_path):
    asset_dir = tmp_path / "moog-asset"

    result = scaffold_moog_asset(str(asset_dir))
    validation = validate_moog_asset(str(asset_dir))
    summary = moog_asset_summary(validation)

    assert result["state"] == "created"
    assert (asset_dir / "docker-compose.yaml").is_file()
    assert (asset_dir / "README.md").is_file()
    assert "MOOG_MPFS_HOST" not in (asset_dir / "docker-compose.yaml").read_text(encoding="utf-8")
    assert "MOOG_TOKEN_ID" not in (asset_dir / "docker-compose.yaml").read_text(encoding="utf-8")
    assert validation["state"] == "ready"
    assert summary["state"] == "ready"
    assert summary["error_count"] == 0


def test_moog_asset_scaffold_refuses_to_overwrite_existing_files(tmp_path):
    asset_dir = tmp_path / "moog-asset"
    asset_dir.mkdir()
    (asset_dir / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")

    result = scaffold_moog_asset(str(asset_dir))

    assert result["state"] == "blocked"
    assert "docker-compose.yaml" in result["existing_files"]


def test_moog_asset_validate_reports_missing_compose(tmp_path):
    asset_dir = tmp_path / "moog-asset"
    asset_dir.mkdir()

    validation = validate_moog_asset(str(asset_dir))
    checks = {check["id"]: check for check in validation["checks"]}

    assert validation["state"] == "blocked"
    assert checks["docker_compose"]["state"] == "error"


def test_moog_asset_validate_rejects_secret_like_files(tmp_path):
    asset_dir = tmp_path / "moog-asset"
    asset_dir.mkdir()
    (asset_dir / "docker-compose.yaml").write_text("services:\n  workload:\n    image: alpine:3.20\n", encoding="utf-8")
    (asset_dir / "wallet.passphrase").write_text("not-real", encoding="utf-8")

    validation = validate_moog_asset(str(asset_dir))
    checks = {check["id"]: check for check in validation["checks"]}

    assert validation["state"] == "blocked"
    assert checks["secret_files"]["state"] == "error"
    assert "wallet.passphrase" in checks["secret_files"]["detail"]


def test_moog_asset_cli_scaffold_and_validate_return_json(monkeypatch, tmp_path, capsys):
    config = DeploymentConfig.from_dict({"moog": DEFAULT_MOOG_CONFIG})
    monkeypatch.setattr("profile_manager.cli._load_or_intake", lambda _command: config)
    asset_dir = tmp_path / "asset"
    scaffold_args = build_parser().parse_args([
        "moog",
        "asset",
        "scaffold",
        "--to",
        str(asset_dir),
        "--json",
    ])
    validate_args = build_parser().parse_args([
        "moog",
        "asset",
        "validate",
        "--asset-dir",
        str(asset_dir),
        "--json",
    ])

    assert cmd_moog(scaffold_args) == 0
    scaffold_payload = json.loads(capsys.readouterr().out)
    assert scaffold_payload["asset"]["state"] == "created"
    assert cmd_moog(validate_args) == 0
    validate_payload = json.loads(capsys.readouterr().out)
    assert validate_payload["summary"]["state"] == "ready"


def test_moog_bootstrap_cli_without_approval_is_plan_only(monkeypatch, capsys):
    config = DeploymentConfig.from_dict({"moog": DEFAULT_MOOG_CONFIG})
    monkeypatch.setattr("profile_manager.cli._load_or_intake", lambda _command: config)
    args = build_parser().parse_args(["moog", "bootstrap", "--json"])

    assert cmd_moog(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["approved"] is False
    assert payload["bootstrap"]["state"] == "planned"
    assert payload["summary"]["state"] == "planned"
    assert "command" in payload


def test_moog_bootstrap_cli_approve_executes_remote_command(monkeypatch, capsys):
    config = DeploymentConfig.from_dict({"moog": DEFAULT_MOOG_CONFIG})
    executed = {}

    def fake_ssh(_config, command, timeout=None, dry_run=False):
        executed["command"] = command
        executed["timeout"] = timeout
        executed["dry_run"] = dry_run
        return CommandResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "state": "ok",
                    "applied": True,
                    "checks": [{"id": "deploy_root", "state": "ok", "detail": "${HOME}/moog-deploy"}],
                    "healthcheck_plan": [{"command": "cardano-profile moog healthcheck --json"}],
                }
            ),
            stderr="",
            rendered_command="ssh build-host moog-bootstrap",
        )

    monkeypatch.setattr("profile_manager.cli._load_or_intake", lambda _command: config)
    monkeypatch.setattr("profile_manager.cli.ssh_command", fake_ssh)
    args = build_parser().parse_args(["moog", "bootstrap", "--approve", "--json"])

    assert cmd_moog(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["approved"] is True
    assert payload["bootstrap"]["applied"] is True
    assert executed["dry_run"] is False
    assert "DWARF_MOOG_APPLY=1" in executed["command"]


def test_moog_preflight_report_blocks_when_stage_blocks(tmp_path):
    asset_dir = tmp_path / "asset"
    scaffold_moog_asset(str(asset_dir))
    asset_validation = validate_moog_asset(str(asset_dir))
    create_test = build_moog_create_test_plan(
        moog_config=DEFAULT_MOOG_CONFIG,
        repo=None,
        github_user="example-user",
        directory="antithesis",
        commit="abc123",
        asset_dir=str(asset_dir),
    )

    report = build_moog_preflight_report(
        health={"state": "warn", "checks": []},
        readiness={"state": "ok", "checks": [], "repo": None, "github_user": "example-user"},
        asset_validation=asset_validation,
        create_test=create_test,
    )
    summary = moog_preflight_summary(report)

    assert report["state"] == "blocked"
    assert summary["state"] == "blocked"
    stages = {stage["id"]: stage for stage in report["stages"]}
    assert stages["moog_health"]["state"] == "warn"
    assert stages["asset"]["state"] == "ready"
    assert stages["create_test"]["state"] == "blocked"


def test_moog_preflight_cli_returns_json(monkeypatch, tmp_path, capsys):
    asset_dir = tmp_path / "asset"
    scaffold_moog_asset(str(asset_dir))
    config = DeploymentConfig.from_dict({"moog": DEFAULT_MOOG_CONFIG})
    health = {
        "state": "warn",
        "checks": [{"id": "oracle_service_active", "state": "warn", "detail": "inactive"}],
        "wallets": {"requester": {"address": "addr_test1requester"}, "oracle": {}},
        "deploy_root": "${HOME}/moog-deploy",
        "mpfs_host": "https://mpfs.plutimus.com",
        "token_id": DEFAULT_MOOG_CONFIG["token_id"],
        "oracle_service": "moog-oracle.service",
    }
    readiness = {
        "state": "ok",
        "checks": [],
        "repo": "example-org/example-repo",
        "github_user": "example-user",
        "requester_wallet_id": "moog-requester",
        "requester_address": "addr_test1requester",
        "requester_balance_tada": "10000.000000",
    }
    monkeypatch.setattr("profile_manager.cli._load_or_intake", lambda _command: config)
    monkeypatch.setattr("profile_manager.cli.query_moog_health", lambda _config: health)
    monkeypatch.setattr("profile_manager.cli.query_moog_readiness", lambda *args, **kwargs: readiness)
    args = build_parser().parse_args([
        "moog",
        "preflight",
        "--repo",
        "example-org/example-repo",
        "--github-user",
        "example-user",
        "--directory",
        "antithesis",
        "--commit",
        "abc123",
        "--asset-dir",
        str(asset_dir),
        "--json",
    ])

    assert cmd_moog(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["state"] == "warn"
    assert payload["preflight"]["stages"][0]["id"] == "moog_health"
    assert payload["preflight"]["create_test"]["state"] == "ready"


def test_moog_create_test_command_uses_requester_wallet_without_reading_secret():
    command = build_moog_create_test_command(
        DEFAULT_MOOG_CONFIG,
        repo="example-org/example-repo",
        github_user="example-user",
        directory="antithesis",
        commit="abc123",
        try_number=2,
        duration_hours=3,
    )

    assert "MOOG_MPFS_HOST=https://mpfs.plutimus.com" in command
    assert f"MOOG_TOKEN_ID={DEFAULT_MOOG_CONFIG['token_id']}" in command
    assert "moog requester create-test" in command
    assert "-w ${HOME}/moog-secrets/requester/requester.json" in command
    assert "-r example-org/example-repo" in command
    assert "-u example-user" in command
    assert "-d antithesis" in command
    assert "-c abc123" in command
    assert "--try 2" in command
    assert "-t 3" in command
    assert "cat ${HOME}/moog-secrets/requester/requester.json" not in command
    assert "walletPassphrase" not in command
    assert "mnemonics" not in command


def test_moog_create_test_plan_validates_local_asset_directory(tmp_path):
    asset_dir = tmp_path / "antithesis"
    asset_dir.mkdir()
    (asset_dir / "docker-compose.yaml").write_text(
        "services:\n  workload:\n    image: alpine:3.20\n    command: ['sh', '-c', 'true']\n",
        encoding="utf-8",
    )

    plan = build_moog_create_test_plan(
        moog_config=DEFAULT_MOOG_CONFIG,
        repo="example-org/example-repo",
        github_user="example-user",
        directory="antithesis",
        commit="abc123",
        try_number=2,
        duration_hours=3,
        asset_dir=str(asset_dir),
    )
    summary = moog_create_test_summary(plan)
    checks = {check["id"]: check for check in plan["checks"]}

    assert plan["state"] == "ready"
    assert summary["state"] == "ready"
    assert summary["error_count"] == 0
    assert checks["asset_dir"]["state"] == "ok"
    assert checks["docker_compose"]["state"] == "ok"
    assert checks["docker_compose"]["detail"].endswith("docker-compose.yaml")
    assert plan["asset"]["docker_compose"].endswith("docker-compose.yaml")
    assert "moog requester create-test" in plan["command"]


def test_moog_create_test_plan_reports_deferred_target_fields(tmp_path):
    plan = build_moog_create_test_plan(
        moog_config=DEFAULT_MOOG_CONFIG,
        repo=None,
        github_user=None,
        directory=None,
        commit=None,
        try_number=1,
        duration_hours=1,
        asset_dir=str(tmp_path / "missing-assets"),
    )
    checks = {check["id"]: check for check in plan["checks"]}

    assert plan["state"] == "blocked"
    assert checks["repo"]["state"] == "error"
    assert checks["github_user"]["state"] == "error"
    assert checks["directory"]["state"] == "error"
    assert checks["commit"]["state"] == "error"
    assert checks["asset_dir"]["state"] == "error"
    assert "<org/repo>" in plan["command"]
    assert "<github-user>" in plan["command"]
    assert "<test-directory>" in plan["command"]
    assert "<commit>" in plan["command"]


def test_moog_create_test_plan_cli_returns_json(monkeypatch, tmp_path, capsys):
    asset_dir = tmp_path / "antithesis"
    asset_dir.mkdir()
    (asset_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    config = DeploymentConfig.from_dict({"moog": DEFAULT_MOOG_CONFIG})
    monkeypatch.setattr("profile_manager.cli._load_or_intake", lambda _command: config)
    args = build_parser().parse_args([
        "moog",
        "create-test-plan",
        "--repo",
        "example-org/example-repo",
        "--github-user",
        "example-user",
        "--directory",
        "antithesis",
        "--commit",
        "abc123",
        "--asset-dir",
        str(asset_dir),
        "--json",
    ])

    assert cmd_moog(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["state"] == "ready"
    assert payload["create_test"]["state"] == "ready"
    assert payload["create_test"]["asset"]["docker_compose"].endswith("docker-compose.yml")


def test_moog_readiness_marks_registered_requester_ready():
    public_key = "vkey1requester"
    readiness = build_moog_readiness(
        moog_config=DEFAULT_MOOG_CONFIG,
        health={
            "state": "ok",
            "checks": [],
            "wallets": {"requester": {"address": "addr_test1requester", "publicKey": public_key}},
        },
        wallet_status_rows=[
            {
                "id": "moog-requester",
                "state": "ok",
                "balance_lovelace": 42_000_000,
                "balance_tada": "42.000000",
                "network": "preprod",
                "address": "addr_test1requester",
            }
        ],
        github={
            "profile_vkey": public_key,
            "profile_repo": "cfhal/cfhal",
            "codeowners_path": ".github/CODEOWNERS",
            "codeowners": "antithesis: @cfhal\n",
        },
        facts={
            "users": [{"key": {"type": "register-user", "platform": "github", "user": "cfhal", "vkey": public_key}}],
            "roles": [
                {
                    "key": {
                        "type": "register-role",
                        "platform": "github",
                        "user": "cfhal",
                        "repository": {"organization": "cardano-foundation", "project": "moog"},
                    }
                }
            ],
            "white_list": [
                {
                    "key": {
                        "repository": {"organization": "cardano-foundation", "project": "moog"},
                    }
                }
            ],
        },
        repo="cardano-foundation/moog",
        github_user="cfhal",
    )
    summary = moog_readiness_summary(readiness)

    assert readiness["state"] == "ok"
    assert summary["ok_count"] >= 7
    assert summary["error_count"] == 0
    assert summary["requester_address"] == "addr_test1requester"
    assert summary["requester_balance_tada"] == "42.000000"


def test_moog_readiness_reports_missing_registration_artifacts():
    readiness = build_moog_readiness(
        moog_config=DEFAULT_MOOG_CONFIG,
        health={
            "state": "ok",
            "checks": [],
            "wallets": {"requester": {"address": "addr_test1requester", "publicKey": "vkey1requester"}},
        },
        wallet_status_rows=[
            {"id": "moog-requester", "state": "empty", "balance_lovelace": 0, "balance_tada": "0.000000"}
        ],
        github={"profile_error": "404", "codeowners_error": "not found"},
        facts={"users": [], "roles": [], "white_list": []},
        repo="cardano-foundation/moog",
        github_user="cfhal",
    )

    assert readiness["state"] == "error"
    checks = {check["id"]: check for check in readiness["checks"]}
    assert checks["requester_wallet_funded"]["state"] == "error"
    assert checks["github_profile_vkey"]["state"] == "error"
    assert checks["github_codeowners"]["state"] == "error"
    assert checks["moog_user_registered"]["state"] == "error"
    assert checks["moog_role_registered"]["state"] == "error"


def test_moog_registration_plan_reports_required_user_update():
    public_key = "vkey1active"
    plan = build_moog_registration_plan(
        moog_config=DEFAULT_MOOG_CONFIG,
        health={
            "state": "ok",
            "checks": [],
            "wallets": {"requester": {"address": "addr_test1requester", "publicKey": public_key}},
        },
        github={
            "profile_vkey": "vkey1old",
            "profile_repo": "cfhal/cfhal",
            "codeowners_path": "CODEOWNERS",
            "codeowners": "antithesis: @cfhal\n",
        },
        facts={
            "users": [],
            "roles": [
                {
                    "key": {
                        "type": "register-role",
                        "platform": "github",
                        "user": "cfhal",
                        "repository": {"organization": "cardano-foundation", "project": "moog"},
                    }
                }
            ],
            "white_list": [],
        },
        repo="cardano-foundation/moog",
        github_user="cfhal",
    )
    summary = moog_registration_summary(plan)

    assert plan["state"] == "blocked"
    assert summary["needed_count"] == 1
    assert plan["github"]["profile_repo"] == "cfhal/cfhal"
    assert plan["github"]["required_moog_vkey"] == public_key
    assert plan["github"]["required_codeowners_line"] == "antithesis: @cfhal"
    actions = {action["id"]: action for action in plan["actions"]}
    assert actions["publish_moog_vkey"]["state"] == "blocked"
    assert actions["register_user"]["state"] == "needed"
    assert actions["register_role"]["state"] == "satisfied"
    assert "moog requester register-user" in actions["register_user"]["command"]
    assert "-v vkey1active" in actions["register_user"]["command"]
    assert "moog requester register-role" in actions["register_role"]["command"]


def test_moog_registration_submit_command_uses_wallet_without_reading_secret():
    public_key = "vkey1active"
    config = {
        **DEFAULT_MOOG_CONFIG,
        "requester_wallet_file": "${HOME}/moog-secrets/requester/requester.json",
    }
    plan = build_moog_registration_plan(
        moog_config=config,
        health={
            "state": "ok",
            "checks": [],
            "wallets": {"requester": {"address": "addr_test1requester", "publicKey": public_key}},
        },
        github={
            "profile_vkey": public_key,
            "profile_repo": "cfhal/cfhal",
            "codeowners_path": "CODEOWNERS",
            "codeowners": "antithesis: @cfhal\n",
        },
        facts={"users": [], "roles": [], "white_list": []},
        repo="cardano-foundation/moog",
        github_user="cfhal",
    )

    command = build_moog_registration_submit_command(plan, config)

    assert "MOOG_MPFS_HOST=https://mpfs.plutimus.com" in command
    assert f"MOOG_TOKEN_ID={DEFAULT_MOOG_CONFIG['token_id']}" in command
    assert "moog requester register-user" in command
    assert "moog requester register-role" in command
    assert "-w ${HOME}/moog-secrets/requester/requester.json" in command
    assert "cat ${HOME}/moog-secrets/requester/requester.json" not in command
    assert "walletPassphrase" not in command
    assert "mnemonics" not in command


def test_moog_registration_submit_cli_reports_blocked_plan(monkeypatch, capsys):
    config = DeploymentConfig.from_dict({"moog": DEFAULT_MOOG_CONFIG})
    plan = {
        "state": "blocked",
        "blocking": ["publish_moog_vkey"],
        "actions": [{"id": "publish_moog_vkey", "state": "blocked", "detail": "cfhal/cfhal"}],
    }
    monkeypatch.setattr("profile_manager.cli._load_or_intake", lambda _command: config)
    monkeypatch.setattr("profile_manager.cli.query_moog_registration_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr("profile_manager.cli.build_moog_registration_submit_command", lambda *_args, **_kwargs: "true")
    args = build_parser().parse_args([
        "moog",
        "registration-submit",
        "--repo",
        "cardano-foundation/moog",
        "--github-user",
        "cfhal",
        "--dry-run",
        "--json",
    ])

    assert cmd_moog(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["blocked"] is True
    assert payload["blockers"] == ["publish_moog_vkey"]


def test_dashboard_payload_includes_moog_summary(monkeypatch):
    config = DeploymentConfig.from_dict({"moog": {"enabled": True}})
    health = {
        "state": "ok",
        "checks": [{"id": "binary", "state": "ok", "detail": "moog 0.5.1.3"}],
        "wallets": {"requester": {"address": "addr_test1..."}, "oracle": {}},
        "deploy_root": "${HOME}/moog-deploy",
        "mpfs_host": "https://mpfs.plutimus.com",
        "token_id": DEFAULT_MOOG_CONFIG["token_id"],
        "oracle_service": "moog-oracle.service",
    }

    monkeypatch.setattr("profile_manager.dashboard.config_exists", lambda: True)
    monkeypatch.setattr("profile_manager.dashboard.load_config", lambda: config)
    monkeypatch.setattr("profile_manager.dashboard.query_moog_health", lambda _config, timeout=10: health)
    monkeypatch.setattr("profile_manager.dashboard._wallet_status_rows_for_payload", lambda: [])
    monkeypatch.setattr("profile_manager.dashboard._latest_profile_health", lambda: (None, ""))
    monkeypatch.setattr("profile_manager.dashboard._profile_rows", lambda: [])
    monkeypatch.setattr("profile_manager.dashboard._package_rows", lambda: [])
    monkeypatch.setattr("profile_manager.dashboard._smoke_rows", lambda: [])
    monkeypatch.setattr("profile_manager.dashboard._fuzz_rows", lambda: [])
    monkeypatch.setattr("profile_manager.dashboard._document_rows", lambda: [])
    monkeypatch.setattr("profile_manager.dashboard._deliverable_rows", lambda: [])
    monkeypatch.setattr("profile_manager.dashboard._command_rows", lambda: [])
    monkeypatch.setattr("profile_manager.dashboard._latest_evidence_rows", lambda: [])
    monkeypatch.setattr("profile_manager.dashboard._local_testcase_lifecycle_summary", lambda: {})

    payload = build_dashboard_status_payload(live=False)

    assert payload["moog"]["state"] == "ok"
    assert payload["moog"]["summary"]["requester_address"] == "addr_test1..."


def test_operate_status_moog_tile_uses_summary_and_checks():
    payload = {
        "moog": {
            "state": "warn",
            "summary": {
                "state": "warn",
                "check_count": 2,
                "ok_count": 1,
                "warn_count": 1,
                "error_count": 0,
                "deploy_root": "${HOME}/moog-deploy",
                "mpfs_host": "https://mpfs.plutimus.com",
                "token_id": DEFAULT_MOOG_CONFIG["token_id"],
                "oracle_service": "moog-oracle.service",
                "requester_address": "addr_test1requester",
                "oracle_address": "addr_test1oracle",
            },
            "checks": [
                {"id": "binary", "state": "ok", "detail": "moog 0.5.1.3"},
                {"id": "oracle_service_active", "state": "warn", "detail": "inactive"},
            ],
        }
    }

    tile = moog_status_tile(payload)

    assert tile["state"] == "warn"
    assert tile["metric"] == "WARN"
    assert tile["deploy_root"] == "${HOME}/moog-deploy"
    assert tile["requester_address"] == "addr_test1requester"
    assert tile["checks"][1]["id"] == "oracle_service_active"


def test_learn_cli_catalog_documents_moog_workflow_commands():
    groups = {group["slug"]: group for group in cli_groups()}

    assert "moog" in groups
    commands = {command["name"] for command in groups["moog"]["commands"]}
    assert "cardano-profile moog bootstrap --json" in commands
    assert "cardano-profile moog preflight --asset-dir <dir> --repo <org/repo> --github-user <user> --directory <path> --commit <sha> --json" in commands
    assert "cardano-profile moog asset scaffold --to <dir> --json" in commands
    assert "cardano-profile moog create-test-plan --asset-dir <dir> --repo <org/repo> --github-user <user> --directory <path> --commit <sha> --json" in commands
