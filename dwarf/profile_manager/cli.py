import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from profile_manager.config import config_exists, config_path, load_config
from profile_manager.config import CONFIG_FIELDS, list_config_values, save_config, set_config_value
from profile_manager import profile_templates
from profile_manager import scenario_templates
from scripts import dwarf_backup as dwarf_backup_script
from scripts import dwarf_restore as dwarf_restore_script
from profile_manager.custom_packages import (
    create_interactive_bundle,
    find_installed_package,
    install_bundle,
    load_bundle,
    package_deploy_blockers,
    package_deploy_command,
    package_deploy_dry_run_text,
    package_list_text,
    package_remote_commands,
    package_run_blockers,
    package_run_summary,
    package_status_text,
    refusal_text,
    validation_text,
    write_custom_package_evidence,
)
from profile_manager.dashboard import (
    dashboard_serve_text,
    dashboard_status_text,
    generate_dashboard,
    port_available,
    serve_dashboard,
)
from profile_manager import forensic
from profile_manager.evidence import write_evidence
from profile_manager.evidence_packages import (
    evidence_package_dry_run_text,
    evidence_package_list_text,
    evidence_package_status_text,
    find_evidence_package,
    package_c_note,
    package_c_remote_command,
    unsupported_package_result,
)
from profile_manager.fuzz import (
    find_fuzz_test,
    fuzz_list_text,
    fuzz_remote_command,
    fuzz_v1_scenario_bytes,
    fuzz_status_text,
    validate_fuzz_test,
    write_fuzz_evidence,
)
from profile_manager.intake import ensure_config_or_intake, run_intake
from profile_manager.inspect import (
    command_for_view,
    component_command,
    doctor_command,
    logs_command,
    resolve_runtime,
)
from profile_manager.prereqs import format_check_results, install_command, run_checks
from profile_manager.profiles import (
    active_profile_command,
    deploy_command,
    deploy_dry_run_text,
    find_profile,
    load_profiles,
    profile_list_text,
    profile_diff_text,
    remove_command,
    remove_dry_run_text,
    status_command,
)
from profile_manager.remote import CommandResult, rsync_to, ssh_command
from profile_manager.smoke import (
    find_smoke_test,
    smoke_list_text,
    smoke_remote_command,
    smoke_status_text,
    write_smoke_evidence,
)
from scripts.bundle_chain_helpers import format_bundle_audit_trail as _format_bundle_audit_trail
from scripts.bundle_chain_helpers import walk_bundle_audit_trail as _walk_bundle_audit_trail


CONFIG_COMMANDS = {
    "status",
    "prereq-check",
    "prereq-install",
    "deploy",
    "remove",
    "snapshot",
    "inspect",
    "doctor",
    "logs",
    "component",
    "test",
    "evidence",
    "package",
    "fuzz",
    "dashboard",
}
BANNER = r"""
 ____  __        ___    ____  _____
|  _ \ \ \      / / \  |  _ \|  ___|
| | | | \ \ /\ / / _ \ | |_) | |_
| |_| |  \ V  V / ___ \|  _ <|  _|
|____/    \_/\_/_/   \_\_| \_\_|

 ___ _   _   _____ _   _ _____
|_ _| \ | | |_   _| | | | ____|
 | ||  \| |   | | | |_| |  _|
 | || |\  |   | | |  _  | |___
|___|_| \_|   |_| |_| |_|_____|

 _____ _     _    ____  _  __
|  ___| |   / \  / ___|| |/ /
| |_  | |  / _ \ \___ \| ' /
|  _| | |_/ ___ \ ___) | . \
|_|   |___/_/   \_\____/|_|\_\

by GainSec
""".strip("\n")


def print_banner():
    print(BANNER)
    print()


def build_parser():
    parser = argparse.ArgumentParser(prog="cardano-profile")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("intake")
    subcommands.add_parser("list-profiles")
    backup = subcommands.add_parser("backup")
    backup.add_argument("--to", required=True)
    backup.add_argument("--include-bundles", action="store_true")
    restore = subcommands.add_parser("restore")
    restore.add_argument("archive_path")
    restore.add_argument("--dry-run", action="store_true")

    config_p = subcommands.add_parser("config")
    config_sub = config_p.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("list")
    config_get = config_sub.add_parser("get")
    config_get.add_argument("key")
    config_set = config_sub.add_parser("set")
    config_set.add_argument("key")
    config_set.add_argument("value")

    status = subcommands.add_parser("status")
    status.add_argument("--dry-run", action="store_true")

    prereq_check = subcommands.add_parser("prereq-check")
    prereq_check.add_argument("--dry-run", action="store_true")

    prereq_install = subcommands.add_parser("prereq-install")
    prereq_install.add_argument("--dry-run", action="store_true")

    deploy = subcommands.add_parser("deploy")
    deploy.add_argument("profile_id")
    deploy.add_argument("--dry-run", action="store_true")
    deploy.add_argument("--replace", action="store_true")
    deploy.add_argument("--approve", action="store_true",
                        help="Skip the interactive y/N prompt. Required for non-interactive use (e.g. dashboard).")

    remove = subcommands.add_parser("remove")
    remove.add_argument("--dry-run", action="store_true")
    remove.add_argument("--approve", action="store_true",
                        help="Skip the interactive y/N prompt. Required for non-interactive use (e.g. dashboard).")

    snapshot = subcommands.add_parser("snapshot")
    snapshot.add_argument("--dry-run", action="store_true")

    inspect = subcommands.add_parser("inspect")
    inspect.add_argument("view", choices=("env", "nodes", "health", "all"))
    inspect.add_argument("--profile-id")
    inspect.add_argument("--runtime-root")
    inspect.add_argument("--dry-run", action="store_true")

    doctor = subcommands.add_parser("doctor")
    doctor.add_argument("--profile-id")
    doctor.add_argument("--runtime-root")
    doctor.add_argument("--dry-run", action="store_true")

    logs = subcommands.add_parser("logs")
    logs_subcommands = logs.add_subparsers(dest="log_action", required=True)
    for action in ("collect", "scan", "tail"):
        log_parser = logs_subcommands.add_parser(action)
        log_parser.add_argument("--profile-id")
        log_parser.add_argument("--runtime-root")
        log_parser.add_argument("--node")
        log_parser.add_argument("--lines", type=int, default=200)
        log_parser.add_argument("--dry-run", action="store_true")

    component = subcommands.add_parser("component")
    component.add_argument("component")
    component.add_argument("view", choices=("status", "logs", "tip", "config", "health"))
    component.add_argument("--profile-id")
    component.add_argument("--runtime-root")
    component.add_argument("--lines", type=int, default=200)
    component.add_argument("--dry-run", action="store_true")

    diff = subcommands.add_parser("diff")
    diff.add_argument("left_profile_id")
    diff.add_argument("right_profile_id")

    test = subcommands.add_parser("test")
    test_subcommands = test.add_subparsers(dest="test_command", required=True)
    smoke = test_subcommands.add_parser("smoke")
    smoke_subcommands = smoke.add_subparsers(dest="smoke_command", required=True)
    smoke_subcommands.add_parser("list")
    smoke_status = smoke_subcommands.add_parser("status")
    smoke_status.add_argument("smoke_id")
    smoke_run = smoke_subcommands.add_parser("run")
    smoke_run.add_argument("smoke_id")
    smoke_run.add_argument("--dry-run", action="store_true")

    evidence = subcommands.add_parser("evidence")
    evidence_subcommands = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_subcommands.add_parser("list")
    evidence_status = evidence_subcommands.add_parser("status")
    evidence_status.add_argument("package_id")
    evidence_run = evidence_subcommands.add_parser("run")
    evidence_run.add_argument("package_id")
    evidence_run.add_argument("--dry-run", action="store_true")

    fuzz = subcommands.add_parser("fuzz")
    fuzz_subcommands = fuzz.add_subparsers(dest="fuzz_command", required=True)
    fuzz_subcommands.add_parser("list")
    fuzz_status = fuzz_subcommands.add_parser("status")
    fuzz_status.add_argument("fuzz_id")
    fuzz_run = fuzz_subcommands.add_parser("run")
    fuzz_run.add_argument("fuzz_id")
    fuzz_run.add_argument("--dry-run", action="store_true")
    fuzz_run.add_argument("--approve", action="store_true")
    fuzz_campaign = fuzz_subcommands.add_parser("campaign")
    fuzz_campaign.add_argument("fuzz_id")
    fuzz_campaign.add_argument("--duration-seconds", type=int, required=True)
    fuzz_campaign.add_argument("--checkpoint-seconds", type=int, default=900)
    fuzz_campaign.add_argument("--child-seconds", type=int, default=900)
    fuzz_campaign.add_argument("--retry-budget", type=int, default=1)
    fuzz_campaign.add_argument("--simulate-interrupt-once-after-seconds", type=int)
    fuzz_campaign.add_argument("--dry-run", action="store_true")
    fuzz_campaign.add_argument("--approve", action="store_true")

    coverage = subcommands.add_parser("coverage")
    coverage_subcommands = coverage.add_subparsers(dest="coverage_command", required=True)
    coverage_aggregate = coverage_subcommands.add_parser("aggregate")
    coverage_aggregate.add_argument("--runs-dir")
    coverage_aggregate.add_argument("--state-dir")
    coverage_aggregate.add_argument("--manifests-dir")

    package = subcommands.add_parser("package")
    package_subcommands = package.add_subparsers(dest="package_command", required=True)
    package_validate = package_subcommands.add_parser("validate")
    package_validate.add_argument("bundle_path")
    package_install = package_subcommands.add_parser("install")
    package_install.add_argument("bundle_path")
    package_install.add_argument("--replace", action="store_true")
    package_subcommands.add_parser("list")
    package_status = package_subcommands.add_parser("status")
    package_status.add_argument("package_id")
    package_run = package_subcommands.add_parser("run")
    package_run.add_argument("package_id")
    package_run.add_argument("--dry-run", action="store_true")
    package_run.add_argument("--approve", action="store_true")
    package_deploy = package_subcommands.add_parser("deploy")
    package_deploy.add_argument("package_id")
    package_deploy.add_argument("--dry-run", action="store_true")
    package_deploy.add_argument("--replace", action="store_true")
    package_create = package_subcommands.add_parser("create")
    package_create.add_argument("--interactive", action="store_true")
    package_create.add_argument("--output", required=True)

    bundle = subcommands.add_parser("bundle")
    bundle_subcommands = bundle.add_subparsers(dest="bundle_command", required=True)
    bundle_promote = bundle_subcommands.add_parser("promote")
    bundle_promote.add_argument("run_id")
    bundle_promote.add_argument("--runs-dir")
    bundle_promote.add_argument("--reason-code", required=True)
    bundle_promote.add_argument("--reason-text", required=True)
    bundle_promote.add_argument("--operator-notes", default="")
    bundle_promote.add_argument("--actor", default=os.environ.get("USER", "operator"))
    bundle_dedupe = bundle_subcommands.add_parser("dedupe")
    bundle_dedupe.add_argument("run_id")
    bundle_dedupe.add_argument("--runs-dir")
    bundle_dedupe.add_argument("--signature-primitive", default=None)
    bundle_sign = bundle_subcommands.add_parser("sign")
    bundle_sign.add_argument("run_id")
    bundle_sign.add_argument("--runs-dir")
    bundle_sign.add_argument("--signing-actor", default="dwarf")
    bundle_export = bundle_subcommands.add_parser("export")
    bundle_export.add_argument("run_id")
    bundle_export.add_argument("--runs-dir")
    bundle_export.add_argument("--signing-actor", default="dwarf")
    bundle_export.add_argument("--to")
    bundle_import = bundle_subcommands.add_parser("import")
    bundle_import.add_argument("tarball_path")
    bundle_import.add_argument("--signature-path")
    bundle_import.add_argument("--runs-dir")
    bundle_verify = bundle_subcommands.add_parser("verify")
    bundle_verify.add_argument("bundle_ref")
    bundle_verify.add_argument("--runs-dir")
    bundle_verify.add_argument("--json", action="store_true")
    bundle_search = bundle_subcommands.add_parser("search")
    bundle_search.add_argument("--runs-dir")
    bundle_search.add_argument("--tag")
    bundle_search.add_argument("--status", choices=("signed", "unsigned", "promoted", "deduped"))
    bundle_search.add_argument("--since")
    bundle_search.add_argument("--until")
    bundle_search.add_argument("--by-scenario")
    bundle_search.add_argument("--assertion-fail", action="store_true")
    bundle_search.add_argument("--target")
    bundle_search.add_argument("--has-evidence-key")
    bundle_search.add_argument("--json", action="store_true")
    bundle_stats = bundle_subcommands.add_parser("stats")
    bundle_stats.add_argument("--runs-dir")
    bundle_list_promoted = bundle_subcommands.add_parser("list-promoted")
    bundle_list_promoted.add_argument("--runs-dir")
    bundle_audit_trail = bundle_subcommands.add_parser("audit-trail")
    bundle_audit_trail.add_argument("run_id")
    bundle_audit_trail.add_argument("--runs-dir")
    bundle_audit_trail.add_argument("--json", action="store_true")
    bundle_replay_and_diff = bundle_subcommands.add_parser("replay-and-diff")
    bundle_replay_and_diff.add_argument("run_id")
    bundle_replay_and_diff.add_argument("--runs-dir")
    bundle_replay_and_diff.add_argument("--state-dir")
    bundle_replay_and_diff.add_argument("--registry-path")
    bundle_replay_and_diff.add_argument("--json", action="store_true")
    bundle_replay_and_diff.add_argument("--compare-relpath", action="append", dest="compare_relpaths")
    bundle_reproduce = bundle_subcommands.add_parser("reproduce")
    bundle_reproduce.add_argument("run_id")
    bundle_reproduce.add_argument("--runs-dir")
    bundle_reproduce.add_argument("--state-dir")
    bundle_reproduce.add_argument("--registry-path")
    bundle_reproduce.add_argument("--json", action="store_true")
    bundle_reproduce.add_argument("--compare-relpath", action="append", dest="compare_relpaths")

    verify = subcommands.add_parser("verify")
    verify.add_argument("run_id")
    verify.add_argument("--runs-dir")
    verify.add_argument("--state-dir")

    replay = subcommands.add_parser("replay")
    replay.add_argument("run_id")
    replay.add_argument("--runs-dir")
    replay.add_argument("--state-dir")
    replay.add_argument("--registry-path")

    compare = subcommands.add_parser("compare")
    compare.add_argument("path")
    compare.add_argument("--runs-dir")
    compare.add_argument("--state-dir")
    compare.add_argument("--registry-path")
    compare.add_argument("--amaru-manifests-dir",
                         help="Directory holding Amaru target manifests (overrides scenario's manifests_dir for the Amaru run).")
    compare.add_argument("--cardano-node-manifests-dir",
                         help="Directory holding cardano-node target manifests (overrides scenario's manifests_dir for the cardano-node run).")

    testcase = subcommands.add_parser("testcase")
    testcase_subcommands = testcase.add_subparsers(dest="testcase_command", required=True)
    testcase_replay = testcase_subcommands.add_parser("replay")
    testcase_replay.add_argument("case_id")
    testcase_replay.add_argument("--target", choices=("amaru", "cardano-node"), required=True)
    testcase_replay.add_argument("--runs-dir")
    testcase_replay.add_argument("--state-dir")
    testcase_replay.add_argument("--registry-path")
    testcase_replay.add_argument("--manifests-dir")
    testcase_minimize = testcase_subcommands.add_parser("minimize")
    testcase_minimize.add_argument("case_id")
    testcase_minimize.add_argument("--target", choices=("amaru", "cardano-node"), required=True)
    testcase_minimize.add_argument("--runs-dir")
    testcase_minimize.add_argument("--state-dir")
    testcase_minimize.add_argument("--manifests-dir", required=True)
    testcase_minimize.add_argument("--backend", choices=("oracle", "afl-tmin"), default="oracle")
    testcase_compare = testcase_subcommands.add_parser("compare")
    testcase_compare.add_argument("case_id")
    testcase_compare.add_argument("--runs-dir")
    testcase_compare.add_argument("--state-dir")
    testcase_compare.add_argument("--registry-path")
    testcase_compare.add_argument("--amaru-manifests-dir")
    testcase_compare.add_argument("--cardano-node-manifests-dir")
    testcase_ingest_run = testcase_subcommands.add_parser("ingest-run")
    testcase_ingest_run.add_argument("run_id")
    testcase_ingest_run.add_argument("--classification", required=True)
    testcase_ingest_run.add_argument("--triage-reason", required=True)
    testcase_ingest_run.add_argument("--producer", default="scenario")
    testcase_ingest_run.add_argument("--source-artifact-path", default="manifest.json")
    testcase_ingest_run.add_argument("--runs-dir")
    testcase_ingest_run.add_argument("--state-dir")
    testcase_replay_queue = testcase_subcommands.add_parser("replay-queue")
    testcase_replay_queue_subcommands = testcase_replay_queue.add_subparsers(dest="testcase_queue_command", required=True)
    testcase_replay_queue_run = testcase_replay_queue_subcommands.add_parser("run")
    testcase_replay_queue_run.add_argument("--runs-dir")
    testcase_replay_queue_run.add_argument("--state-dir")
    testcase_replay_queue_run.add_argument("--registry-path")
    testcase_replay_queue_run.add_argument("--manifests-dir")
    testcase_replay_queue_run.add_argument("--amaru-manifests-dir")
    testcase_replay_queue_run.add_argument("--cardano-node-manifests-dir")
    testcase_replay_queue_run.add_argument("--limit", type=int)
    testcase_replay_queue_run.add_argument("--case-id")
    testcase_compare_queue = testcase_subcommands.add_parser("compare-queue")
    testcase_compare_queue_subcommands = testcase_compare_queue.add_subparsers(dest="testcase_compare_queue_command", required=True)
    testcase_compare_queue_run = testcase_compare_queue_subcommands.add_parser("run")
    testcase_compare_queue_run.add_argument("--runs-dir")
    testcase_compare_queue_run.add_argument("--state-dir")
    testcase_compare_queue_run.add_argument("--registry-path")
    testcase_compare_queue_run.add_argument("--amaru-manifests-dir")
    testcase_compare_queue_run.add_argument("--cardano-node-manifests-dir")
    testcase_compare_queue_run.add_argument("--limit", type=int)
    testcase_compare_queue_run.add_argument("--case-id")
    testcase_promote = testcase_subcommands.add_parser("promote")
    testcase_promote_subcommands = testcase_promote.add_subparsers(dest="testcase_promote_command", required=True)
    testcase_promote_bucket = testcase_promote_subcommands.add_parser("bucket")
    testcase_promote_bucket.add_argument("bucket_id")
    testcase_promote_bucket.add_argument("--state", choices=("candidate", "validated", "finding"), required=True)
    testcase_promote_bucket.add_argument("--summary", required=True)
    testcase_promote_bucket.add_argument("--source", required=True)
    testcase_promote_bucket.add_argument("--actor")
    testcase_promote_bucket.add_argument("--state-dir")
    testcase_buckets = testcase_subcommands.add_parser("buckets")
    testcase_buckets_subcommands = testcase_buckets.add_subparsers(dest="testcase_buckets_command", required=True)
    testcase_buckets_summary = testcase_buckets_subcommands.add_parser("summary")
    testcase_buckets_summary.add_argument("--state-dir")
    testcase_buckets_summary.add_argument("--limit", type=int, default=10)
    testcase_repair_state = testcase_subcommands.add_parser("repair-state")
    testcase_repair_state.add_argument("--state-dir")

    scenario_p = subcommands.add_parser("scenario")
    scenario_sub = scenario_p.add_subparsers(dest="scenario_command", required=True)
    scenario_validate = scenario_sub.add_parser("validate")
    scenario_validate.add_argument("path")
    scenario_validate.add_argument("--semantic", action="store_true")
    scenario_validate.add_argument("--registry-path")
    scenario_new = scenario_sub.add_parser("new")
    scenario_new.add_argument("--template", required=True)
    scenario_new.add_argument("--name", required=True)
    scenario_new.add_argument("--output")
    scenario_run = scenario_sub.add_parser("run")
    scenario_run.add_argument("path")
    scenario_run.add_argument("--runs-dir")
    scenario_run.add_argument("--state-dir")
    scenario_run.add_argument("--registry-path")

    profile_p = subcommands.add_parser("profile")
    profile_sub = profile_p.add_subparsers(dest="profile_command", required=True)
    profile_new = profile_sub.add_parser("new")
    profile_new.add_argument("--template", required=True)
    profile_new.add_argument("--name", required=True)
    profile_new.add_argument("--output")

    primitive_p = subcommands.add_parser("primitive")
    primitive_sub = primitive_p.add_subparsers(dest="primitive_command", required=True)
    primitive_new = primitive_sub.add_parser("new")
    primitive_new.add_argument("--family", required=True, choices=("setup", "load", "probe", "assertion", "fault", "teardown"))
    primitive_new.add_argument("--name", required=True)
    primitive_new.add_argument("--repo-root")

    dashboard = subcommands.add_parser("dashboard")
    dashboard_subcommands = dashboard.add_subparsers(dest="dashboard_command", required=True)
    dashboard_status = dashboard_subcommands.add_parser("status")
    dashboard_status.add_argument("--output-dir")
    dashboard_generate = dashboard_subcommands.add_parser("generate")
    dashboard_generate.add_argument("--output-dir")
    dashboard_serve = dashboard_subcommands.add_parser("serve")
    dashboard_serve.add_argument("--output-dir")
    dashboard_serve.add_argument("--port", type=int, default=8787)
    dashboard_serve.add_argument("--bind", default="0.0.0.0")
    dashboard_serve.add_argument("--token", default=None,
                                 help="Token required for any future mutating endpoint. "
                                      "Defaults to ADA2_DWARF_TOKEN env var or 'dwarf'. Read-only routes are open.")
    dashboard_serve.add_argument("--dry-run", action="store_true")

    return parser


def _load_or_intake(command):
    if command in CONFIG_COMMANDS and not config_exists():
        ensure_config_or_intake(command)
    return load_config()


def _print_config_header(config):
    print(f"Config path: {config_path()}")
    print(f"Target: {config.ssh_user}@{config.host}")
    print(f"Remote base path: {config.remote_base_path}")


def cmd_status(args):
    config = _load_or_intake("status")
    _print_config_header(config)
    result = ssh_command(config, status_command(), timeout=60, dry_run=args.dry_run)
    write_evidence(
        "manual-status",
        "status-dry-run" if args.dry_run else "status",
        config_path(),
        config,
        [result],
        limitations=["dry-run status" if args.dry_run else "read-only status"],
    )
    if args.dry_run:
        print("DRY RUN")
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def cmd_config(args):
    config = _load_or_intake("status")
    if args.config_command == "list":
        print(json.dumps(list_config_values(config), indent=2, sort_keys=True))
        return 0
    if args.config_command == "get":
        if args.key not in CONFIG_FIELDS:
            print(f"unknown config key: {args.key}", file=sys.stderr)
            return 1
        value = list_config_values(config)[args.key]
        print(json.dumps(value))
        return 0
    if args.config_command == "set":
        if args.key not in CONFIG_FIELDS:
            print(f"unknown config key: {args.key}", file=sys.stderr)
            return 1
        updated = set_config_value(config, args.key, args.value)
        path = save_config(updated)
        print(f"Updated {args.key} in {path}")
        return 0
    return 2


def cmd_backup(args):
    result = dwarf_backup_script.export_backup(
        dwarf_root=DWARF_ROOT,
        destination=Path(args.to),
        include_bundles=args.include_bundles,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


def cmd_restore(args):
    result = dwarf_restore_script.restore_backup(
        tarball_path=Path(args.archive_path),
        dry_run=args.dry_run,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["verdict"] in {"restored", "dry-run"} else 1


def cmd_prereq_check(args):
    config = _load_or_intake("prereq-check")
    results = run_checks(config, dry_run=args.dry_run)
    write_evidence(
        "manual-prereq-check",
        "prereq-check-dry-run" if args.dry_run else "prereq-check",
        config_path(),
        config,
        [result for _, result in results],
        limitations=["dry-run prerequisite check" if args.dry_run else "read-only prerequisite check"],
    )
    print(format_check_results(results), end="")
    return 0 if all(result.returncode == 0 for _, result in results) else 1


def cmd_prereq_install(args):
    config = _load_or_intake("prereq-install")
    command = install_command()
    if args.dry_run:
        print("DRY RUN")
        print(command)
        write_evidence(
            "manual-prereq-install",
            "prereq-install-dry-run",
            config_path(),
            config,
            [],
            limitations=[f"Would run: {command}", "No remote state changed."],
        )
        return 0
    if not (config.allow_prereq_install and config.allow_sudo):
        print("Prerequisite installation is disabled by config.")
        print("Run intake again or edit config only after confirming sudo/install policy.")
        return 1
    answer = input("Install missing prerequisites now? [y/N]: ").strip().lower()
    if answer not in {"y", "yes"}:
        print("Install cancelled.")
        return 1
    result = ssh_command(config, command, timeout=600)
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def cmd_deploy(args):
    config = _load_or_intake("deploy")
    profile = find_profile(args.profile_id)
    active = ssh_command(config, active_profile_command(), timeout=30, dry_run=args.dry_run)
    if args.dry_run:
        print(deploy_dry_run_text(profile), end="")
        print("Active-profile check command:")
        print(active.rendered_command)
        write_evidence(
            profile.id,
            "deploy-dry-run",
            config_path(),
            config,
            [active],
            limitations=[
                deploy_dry_run_text(profile).strip(),
                "No remote state changed.",
            ],
        )
        _record_forensic_for_legacy_run(
            args,
            scenario_id=profile.id,
            scenario_yaml=_synthetic_scenario_yaml("deploy", profile.id, profile.label, {}, [active.rendered_command], profile.id, dry_run=True),
            profile_id=profile.id,
            command_result=active,
        )
        return 0
    if active.stdout.strip() and not args.replace:
        print("Active profile or legacy devnet detected:")
        print(active.stdout)
        print("Refusing deploy without --replace and explicit confirmation.")
        return 1
    if active.stdout.strip() and args.replace:
        if args.approve:
            answer = "y"
        else:
            answer = input("Remove active profile before deploy? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Deploy cancelled.")
            return 1
        removal = ssh_command(config, remove_command(config.remote_base_path), timeout=120)
        write_evidence(
            "manual-remove",
            "remove-before-deploy",
            config_path(),
            config,
            [active, removal],
            limitations=["Explicit --replace path was used before deploy."],
        )
        if removal.returncode != 0:
            print(removal.stdout, end="")
            print(removal.stderr, file=sys.stderr, end="")
            return removal.returncode
    if args.approve:
        answer = "y"
    else:
        answer = input(f"Deploy {profile.id} to {config.host}? [y/N]: ").strip().lower()
    if answer not in {"y", "yes"}:
        print("Deploy cancelled.")
        return 1
    result = ssh_command(config, deploy_command(profile), timeout=300)
    path = write_evidence(
        profile.id,
        "deploy",
        config_path(),
        config,
        [active, result],
        limitations=["Profile deployment command executed over SSH."],
    )
    _record_forensic_for_legacy_run(
        args,
        scenario_id=profile.id,
        scenario_yaml=_synthetic_scenario_yaml("deploy", profile.id, profile.label, {}, [result.rendered_command], profile.id),
        profile_id=profile.id,
        command_result=result,
    )
    print(f"Wrote evidence: {path}")
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def cmd_remove(args):
    config = _load_or_intake("remove")
    active = ssh_command(config, active_profile_command(), timeout=30, dry_run=args.dry_run)
    if args.dry_run:
        print(remove_dry_run_text(), end="")
        print("Active-profile check command:")
        print(active.rendered_command)
        write_evidence(
            "manual-remove",
            "remove-dry-run",
            config_path(),
            config,
            [active],
            limitations=[
                remove_dry_run_text().strip(),
                "No remote state changed.",
            ],
        )
        _record_forensic_for_legacy_run(
            args,
            scenario_id="remove",
            scenario_yaml=_synthetic_scenario_yaml("remove", "remove", "Remove active devnet", {}, [active.rendered_command], None, dry_run=True),
            profile_id=None,
            command_result=active,
        )
        return 0
    print("Active profile or legacy devnet state:")
    print(active.stdout or "(none detected)")
    if args.approve:
        answer = "y"
    else:
        answer = input("Stop active Cardano profile sessions and archive runtime directories? [y/N]: ").strip().lower()
    if answer not in {"y", "yes"}:
        print("Remove cancelled.")
        return 1
    result = ssh_command(config, remove_command(config.remote_base_path), timeout=120)
    path = write_evidence(
        "manual-remove",
        "remove",
        config_path(),
        config,
        [active, result],
        limitations=["Explicit remove command executed over SSH."],
    )
    _record_forensic_for_legacy_run(
        args,
        scenario_id="remove",
        scenario_yaml=_synthetic_scenario_yaml("remove", "remove", "Remove active devnet", {}, [result.rendered_command], None),
        profile_id=None,
        command_result=result,
    )
    print(f"Wrote evidence: {path}")
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def cmd_snapshot(args):
    config = _load_or_intake("snapshot")
    result = ssh_command(config, status_command(), timeout=60, dry_run=args.dry_run)
    path = write_evidence(
        "manual-snapshot",
        "snapshot",
        config_path(),
        config,
        [result],
        limitations=["dry-run snapshot" if args.dry_run else "read-only status snapshot"],
    )
    print(f"Wrote evidence: {path}")
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def cmd_inspect(args):
    config = _load_or_intake("inspect")
    runtime_root, evidence_label = resolve_runtime(args.profile_id, args.runtime_root)
    command = command_for_view(args.view, runtime_root)
    result = ssh_command(config, command, timeout=90, dry_run=args.dry_run)
    action = f"inspect-{args.view}-dry-run" if args.dry_run else f"inspect-{args.view}"
    path = write_evidence(
        evidence_label,
        action,
        config_path(),
        config,
        [result],
        limitations=[
            "read-only inspect command",
            f"runtime_root={runtime_root}",
            "dry-run only; no remote state changed" if args.dry_run else "remote state was not modified",
        ],
    )
    if args.dry_run:
        print("DRY RUN")
    print(f"Runtime root: {runtime_root}")
    print(f"Wrote evidence: {path}")
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def cmd_doctor(args):
    config = _load_or_intake("doctor")
    runtime_root, evidence_label = resolve_runtime(args.profile_id, args.runtime_root)
    result = ssh_command(config, doctor_command(runtime_root), timeout=120, dry_run=args.dry_run)
    action = "doctor-dry-run" if args.dry_run else "doctor"
    path = write_evidence(
        evidence_label,
        action,
        config_path(),
        config,
        [result],
        limitations=[
            "read-only doctor command",
            f"runtime_root={runtime_root}",
            "dry-run only; no remote state changed" if args.dry_run else "remote state was not modified",
        ],
    )
    if args.dry_run:
        print("DRY RUN")
    print(f"Runtime root: {runtime_root}")
    print(f"Wrote evidence: {path}")
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def cmd_logs(args):
    config = _load_or_intake("logs")
    runtime_root, evidence_label = resolve_runtime(args.profile_id, args.runtime_root)
    result = ssh_command(
        config,
        logs_command(args.log_action, runtime_root, args.node, args.lines),
        timeout=120,
        dry_run=args.dry_run,
    )
    action = f"logs-{args.log_action}-dry-run" if args.dry_run else f"logs-{args.log_action}"
    path = write_evidence(
        evidence_label,
        action,
        config_path(),
        config,
        [result],
        limitations=[
            "read-only logs command",
            f"runtime_root={runtime_root}",
            f"log_action={args.log_action}",
            f"node={args.node or 'all'}",
            "dry-run only; no remote state changed" if args.dry_run else "remote state was not modified",
        ],
    )
    if args.dry_run:
        print("DRY RUN")
    print(f"Runtime root: {runtime_root}")
    print(f"Wrote evidence: {path}")
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def cmd_component(args):
    config = _load_or_intake("component")
    runtime_root, evidence_label = resolve_runtime(args.profile_id, args.runtime_root)
    result = ssh_command(
        config,
        component_command(args.component, args.view, runtime_root, args.lines),
        timeout=90,
        dry_run=args.dry_run,
    )
    action = f"component-{args.component}-{args.view}-dry-run" if args.dry_run else f"component-{args.component}-{args.view}"
    path = write_evidence(
        evidence_label,
        action,
        config_path(),
        config,
        [result],
        limitations=[
            "read-only component command",
            f"runtime_root={runtime_root}",
            f"component={args.component}",
            f"component_view={args.view}",
            "dry-run only; no remote state changed" if args.dry_run else "remote state was not modified",
        ],
    )
    if args.dry_run:
        print("DRY RUN")
    print(f"Runtime root: {runtime_root}")
    print(f"Wrote evidence: {path}")
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def cmd_evidence(args):
    if args.evidence_command == "list":
        print(evidence_package_list_text(), end="")
        return 0

    package = find_evidence_package(args.package_id)
    if args.evidence_command == "status":
        print(evidence_package_status_text(package), end="")
        return 0

    if args.evidence_command == "run":
        if args.dry_run:
            print(evidence_package_dry_run_text(package), end="")
            return 0 if package.runnable else 1
        if not package.runnable:
            result = unsupported_package_result(package)
            print(result.stdout, end="")
            return result.returncode
        if package.id != "package-c":
            print(f"No runner implemented for {package.id}.")
            return 1
        config = _load_or_intake("evidence")
        result = _run_evidence_via_remote_scenario(args, config, package)
        path = package_c_note(package, result)
        print(f"Wrote Package C note: {path}")
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        return result.returncode

    return 2


def cmd_test(args):
    if args.test_command != "smoke":
        return 2
    if args.smoke_command == "list":
        print(smoke_list_text(), end="")
        return 0
    smoke = find_smoke_test(args.smoke_id)
    if args.smoke_command == "status":
        print(smoke_status_text(smoke), end="")
        return 0
    if args.smoke_command == "run":
        config = _load_or_intake("test")
        if args.dry_run:
            command = smoke_remote_command(smoke)
            result = ssh_command(config, command, timeout=smoke.timeout_seconds, dry_run=True)
        else:
            result = _run_smoke_via_remote_scenario(args, config, smoke)
        action = "run-dry-run" if args.dry_run else "run"
        path = write_smoke_evidence(smoke, action, config_path(), config, result, dry_run=args.dry_run)
        if args.dry_run:
            _record_forensic_for_legacy_run(
                args,
                scenario_id=smoke.id,
                scenario_yaml=_synthetic_scenario_yaml("smoke", smoke.id, smoke.label, dict(smoke.environment), list(smoke.commands), None, dry_run=True),
                profile_id=None,
                command_result=result,
            )
        if args.dry_run:
            print("DRY RUN")
        print(f"Wrote smoke evidence: {path}")
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        return result.returncode
    return 2


def cmd_fuzz(args):
    if args.fuzz_command == "list":
        print(fuzz_list_text(), end="")
        return 0
    try:
        fuzz = find_fuzz_test(args.fuzz_id)
    except KeyError as error:
        print(str(error))
        return 1
    if args.fuzz_command == "status":
        print(fuzz_status_text(fuzz), end="")
        return 0
    if args.fuzz_command == "run":
        errors = validate_fuzz_test(fuzz)
        if errors:
            print("Refusing fuzz run")
            print()
            for error in errors:
                print(f"- {error}")
            return 1
        if fuzz.requires_deployed_testnet and not args.approve:
            print("Refusing fuzz run")
            print()
            print("- live deployed-testnet fuzzing requires --approve")
            return 1
        config = _load_or_intake("fuzz")
        if args.dry_run:
            command = fuzz_remote_command(fuzz)
            result = ssh_command(config, command, timeout=fuzz.timeout_seconds, dry_run=True)
        else:
            result = _run_fuzz_via_remote_scenario(args, config, fuzz)
        action = "run-dry-run" if args.dry_run else "run"
        path = write_fuzz_evidence(fuzz, action, config_path(), config, result, dry_run=args.dry_run)
        if args.dry_run:
            _record_forensic_for_legacy_run(
                args,
                scenario_id=fuzz.id,
                scenario_yaml=_synthetic_scenario_yaml("fuzz", fuzz.id, fuzz.label, dict(fuzz.environment), list(fuzz.commands), fuzz.profile_required, dry_run=True),
                profile_id=fuzz.profile_required,
                command_result=result,
            )
        if args.dry_run:
            print("DRY RUN")
        print(f"Wrote fuzz evidence: {path}")
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        return result.returncode
    if args.fuzz_command == "campaign":
        errors = validate_fuzz_test(fuzz)
        if errors:
            print("Refusing fuzz campaign")
            print()
            for error in errors:
                print(f"- {error}")
            return 1
        if fuzz.requires_deployed_testnet and not args.approve:
            print("Refusing fuzz campaign")
            print()
            print("- live deployed-testnet fuzzing requires --approve")
            return 1
        if args.duration_seconds <= 0 or args.checkpoint_seconds <= 0 or args.child_seconds <= 0:
            print("Refusing fuzz campaign")
            print()
            print("- duration, checkpoint, and child seconds must be positive")
            return 1
        if args.child_seconds > args.checkpoint_seconds:
            print("Refusing fuzz campaign")
            print()
            print("- child-seconds must be less than or equal to checkpoint-seconds")
            return 1
        config = _load_or_intake("fuzz")
        if args.dry_run:
            remote_root = _remote_dwarf_root()
            remote_command = (
                f"cd {shlex.quote(str(remote_root))} && "
                f"python3 scripts/fuzz_campaign_manager.py "
                f"--fuzz-id {shlex.quote(fuzz.id)} "
                f"--duration-seconds {int(args.duration_seconds)} "
                f"--checkpoint-seconds {int(args.checkpoint_seconds)} "
                f"--child-seconds {int(args.child_seconds)} "
                f"--retry-budget {int(args.retry_budget)} "
                f"--campaign-root {shlex.quote(str(remote_root / 'runs-campaigns'))}"
            )
            if args.simulate_interrupt_once_after_seconds is not None:
                remote_command += (
                    f" --simulate-interrupt-once-after-seconds {int(args.simulate_interrupt_once_after_seconds)}"
                )
            result = ssh_command(config, remote_command, timeout=30, dry_run=True)
        else:
            result = _run_fuzz_campaign_via_remote_manager(args, config, fuzz)
        action = "campaign-run-dry-run" if args.dry_run else "campaign-run"
        path = write_fuzz_evidence(fuzz, action, config_path(), config, result, dry_run=args.dry_run)
        if args.dry_run:
            print("DRY RUN")
        print(f"Wrote fuzz evidence: {path}")
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        return result.returncode
    return 2


def cmd_package(args):
    if args.package_command == "validate":
        result = load_bundle(args.bundle_path)
        print(validation_text(result), end="")
        return 0 if result.ok else 1
    if args.package_command == "install":
        code, text = install_bundle(args.bundle_path, replace=args.replace)
        print(text, end="")
        return code
    if args.package_command == "list":
        print(package_list_text(), end="")
        return 0
    if args.package_command == "status":
        try:
            print(package_status_text(args.package_id), end="")
            return 0
        except KeyError as error:
            print(str(error))
            return 1
    if args.package_command == "run":
        try:
            package_dir, result = find_installed_package(args.package_id)
        except KeyError as error:
            print(str(error))
            return 1
        blockers = package_run_blockers(result, approved=args.approve)
        if blockers:
            print(refusal_text(blockers), end="")
            return 1
        try:
            commands = package_remote_commands(result.package, result.profile, package_dir=package_dir)
        except ValueError as error:
            print(refusal_text((str(error),)), end="")
            return 1
        config = _load_or_intake("package")
        if args.dry_run:
            command_results = [
                ssh_command(config, command, timeout=120, dry_run=True)
                for command in commands
            ]
        else:
            command_results = [_run_package_via_remote_scenario(args, config, result.package, result.profile, commands)]
        action = "run-dry-run" if args.dry_run else "run"
        path = write_custom_package_evidence(
            result.package,
            result.profile,
            action,
            config_path(),
            config,
            command_results,
            dry_run=args.dry_run,
            limitations=[
                "custom package runner supports approved read-only command specs only",
                "mutating or destructive package specs require --approve and remain restricted by package safety gates",
                "no public-network contact is performed by this runner",
                "dry-run only; no remote state changed" if args.dry_run else "remote commands were limited to read-only package specs",
            ],
        )
        if args.dry_run:
            package_id = result.package.get("id") if isinstance(result.package, dict) else getattr(result.package, "id", "custom-package")
            package_label = result.package.get("label", package_id) if isinstance(result.package, dict) else getattr(result.package, "label", package_id)
            profile_id = result.profile.get("id") if isinstance(result.profile, dict) else getattr(result.profile, "id", None)
            for cmd_str, cmd_result in zip(commands, command_results):
                _record_forensic_for_legacy_run(
                    args,
                    scenario_id=package_id,
                    scenario_yaml=_synthetic_scenario_yaml("custom-package", package_id, package_label, {}, [cmd_str], profile_id, dry_run=True),
                    profile_id=profile_id,
                    command_result=cmd_result,
                )
        print(package_run_summary(result.package, result.profile, commands, dry_run=args.dry_run), end="")
        print(f"Wrote evidence: {path}")
        for command_result in command_results:
            if command_result.stdout:
                print(command_result.stdout, end="")
            if command_result.stderr:
                print(command_result.stderr, file=sys.stderr, end="")
        return 0 if all(command_result.returncode == 0 for command_result in command_results) else 1
    if args.package_command == "deploy":
        try:
            _, result = find_installed_package(args.package_id)
        except KeyError as error:
            print(str(error))
            return 1
        blockers = package_deploy_blockers(result)
        if blockers:
            print("Refusing custom package deploy")
            print()
            for blocker in blockers:
                print(f"- {blocker}")
            return 1
        config = _load_or_intake("package")
        active = ssh_command(config, active_profile_command(), timeout=30, dry_run=args.dry_run)
        deploy_text = package_deploy_dry_run_text(result.package, result.profile)
        if args.dry_run:
            print(deploy_text, end="")
            print("Active-runtime check command:")
            print(active.rendered_command)
            path = write_custom_package_evidence(
                result.package,
                result.profile,
                "deploy-dry-run",
                config_path(),
                config,
                [active],
                dry_run=True,
                limitations=[
                    deploy_text.strip(),
                    "No remote state changed.",
                    "Deployment will not reuse an active package/profile runtime.",
                ],
            )
            print(f"Wrote evidence: {path}")
            return 0
        if active.stdout.strip():
            print("Active Cardano runtime detected:")
            print(active.stdout)
            answer = input("Remove the currently deployed package/profile before deploying this one? [y/N]: ").strip().lower()
            if answer not in {"y", "yes"}:
                print("Deploy cancelled.")
                return 1
            removal = ssh_command(config, remove_command(config.remote_base_path), timeout=120)
            if removal.returncode != 0:
                path = write_custom_package_evidence(
                    result.package,
                    result.profile,
                    "deploy-remove-failed",
                    config_path(),
                    config,
                    [active, removal],
                    limitations=["Attempted removal of currently deployed runtime before package deploy."],
                )
                print(f"Wrote evidence: {path}")
                print(removal.stdout, end="")
                print(removal.stderr, file=sys.stderr, end="")
                return removal.returncode
        else:
            removal = None
        answer = input(f"Deploy custom package {result.package['id']} to {config.host}? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Deploy cancelled.")
            return 1
        deploy_result = ssh_command(config, package_deploy_command(result.package, result.profile), timeout=300)
        command_results = [active]
        if removal is not None:
            command_results.append(removal)
        command_results.append(deploy_result)
        path = write_custom_package_evidence(
            result.package,
            result.profile,
            "deploy",
            config_path(),
            config,
            command_results,
            limitations=[
                "Custom package deploy executed over SSH.",
                "Any active runtime was removed only after explicit confirmation.",
                "Deployment does not reuse an already deployed package/profile runtime.",
            ],
        )
        print(f"Wrote evidence: {path}")
        print(deploy_result.stdout, end="")
        if deploy_result.stderr:
            print(deploy_result.stderr, file=sys.stderr, end="")
        return deploy_result.returncode
    if args.package_command == "create":
        if not args.interactive:
            print("package create currently requires --interactive")
            return 1
        return create_interactive_bundle(args.output)
    return 2


def cmd_dashboard(args):
    if args.dashboard_command == "status":
        print(dashboard_status_text(args.output_dir), end="")
        return 0
    if args.dashboard_command == "generate":
        result = generate_dashboard(args.output_dir)
        print(f"Wrote dashboard: {result.path}")
        print(f"Open: {result.url}")
        return 0
    if args.dashboard_command == "serve":
        text = dashboard_serve_text(args.output_dir, args.port, args.bind, token=args.token)
        if args.dry_run:
            print("DRY RUN")
            print(text, end="")
            return 0
        if not port_available(args.port):
            print(f"Port {args.port} is already in use.")
            return 1
        serve_dashboard(args.output_dir, args.port, args.bind, token=args.token)
        return 0
    return 2


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _forensic_runs_dir(args):
    if getattr(args, "runs_dir", None):
        return Path(args.runs_dir)
    env = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env:
        return Path(env)
    return PACKAGE_ROOT / "runs"


def _forensic_state_dir(args):
    if getattr(args, "state_dir", None):
        return Path(args.state_dir)
    env = os.environ.get("ADA2_DWARF_STATE_DIR")
    if env:
        return Path(env)
    return PACKAGE_ROOT / "state"


def _synthetic_scenario_yaml(kind, scenario_id, label, environment, commands, profile, dry_run=False):
    """Render a YAML-like text capturing what a legacy fuzz/smoke/evidence/package run was.

    Not a v1 scenario; an attestation of what the legacy CLI flow actually did. Goes into
    scenario.yaml of the forensic bundle so the bundle is self-describing.
    """
    import json as _json
    body = {
        "spec_version": "legacy-v1",
        "kind": kind,
        "id": scenario_id,
        "label": label,
        "profile": profile,
        "dry_run": bool(dry_run),
        "environment": environment,
        "commands": commands,
    }
    return ("# legacy CLI run; not a v1 scenario\n" + _json.dumps(body, sort_keys=True, indent=2) + "\n").encode("utf-8")


REMOTE_DWARF_ROOT_ENV = "ADA2_DWARF_REMOTE_ROOT"


def _remote_dwarf_root():
    return Path(os.environ.get(REMOTE_DWARF_ROOT_ENV, "/home/nigel/dwarf-fw"))


def _manifest_target_implementation(*parts):
    text = " ".join(str(part) for part in parts if part).lower()
    if "amaru" in text:
        return "amaru"
    return "cardano-node"


def _manifest_runtime_and_profile(working_directory, explicit_profile_id=None):
    if explicit_profile_id:
        return "devnet", explicit_profile_id
    working_directory = Path(working_directory)
    for profile in load_profiles():
        runtime_root = Path(profile.remote_runtime_root)
        if working_directory == runtime_root or runtime_root in working_directory.parents:
            return "devnet", profile.id
    return "library", None


def _smoke_v1_scenario_yaml(smoke):
    command = f"bash -lc {shlex.quote(smoke_remote_command(smoke))}"
    runtime, profile_id = _manifest_runtime_and_profile(smoke.working_directory)
    body = {
        "spec_version": "v1",
        "id": smoke.id,
        "title": smoke.label,
        "authors": ["dwarf"],
        "tags": ["smoke", smoke.category],
        "target": {
            "implementation": _manifest_target_implementation(
                smoke.id,
                smoke.label,
                smoke.source_reference,
                smoke.working_directory,
            ),
            "version": "any",
        },
        "runtime": runtime,
        "profile": profile_id,
        "seed": "0xD00D0001",
        "load": [
            {
                "primitive": "load_shell_command",
                "command": command,
                "timeout_seconds": smoke.timeout_seconds,
                "expect_exit": 0,
            }
        ],
        "probes": [],
        "assertions": [{"primitive": "load_events_are_ok", "min_completed": 1}],
        "teardown": [],
    }
    return (json.dumps(body, indent=2) + "\n").encode("utf-8")


def _fuzz_v1_scenario_yaml(fuzz):
    return fuzz_v1_scenario_bytes(fuzz)


def _run_fuzz_campaign_via_remote_manager(args, config, fuzz):
    remote_root = _remote_dwarf_root()
    remote_command = (
        f"cd {shlex.quote(str(remote_root))} && "
        f"python3 scripts/fuzz_campaign_manager.py "
        f"--fuzz-id {shlex.quote(fuzz.id)} "
        f"--duration-seconds {int(args.duration_seconds)} "
        f"--checkpoint-seconds {int(args.checkpoint_seconds)} "
        f"--child-seconds {int(args.child_seconds)} "
        f"--retry-budget {int(args.retry_budget)} "
        f"--campaign-root {shlex.quote(str(remote_root / 'runs-campaigns'))}"
    )
    if args.simulate_interrupt_once_after_seconds is not None:
        remote_command += (
            f" --simulate-interrupt-once-after-seconds {int(args.simulate_interrupt_once_after_seconds)}"
        )
    return ssh_command(config, remote_command, timeout=int(args.duration_seconds) + 3600, dry_run=False)


def _evidence_v1_scenario_yaml(package):
    command = f"bash -lc {shlex.quote(package_c_remote_command(package))}"
    runtime, profile_id = _manifest_runtime_and_profile(package.runtime_root, package.runtime_profile or None)
    body = {
        "spec_version": "v1",
        "id": package.id,
        "title": package.label,
        "authors": ["dwarf"],
        "tags": ["evidence", package.id],
        "target": {
            "implementation": _manifest_target_implementation(
                package.id,
                package.label,
                package.runtime_root,
            ),
            "version": "any",
        },
        "runtime": runtime,
        "profile": profile_id,
        "seed": "0xD00D0001",
        "load": [
            {
                "primitive": "load_shell_command",
                "command": command,
                "timeout_seconds": 120,
                "expect_exit": 0,
            }
        ],
        "probes": [],
        "assertions": [{"primitive": "load_events_are_ok", "min_completed": 1}],
        "teardown": [],
    }
    return (json.dumps(body, indent=2) + "\n").encode("utf-8")


def _package_v1_scenario_yaml(package, profile, commands):
    runtime, profile_id = _manifest_runtime_and_profile(profile["runtime_root"], profile.get("id"))
    body = {
        "spec_version": "v1",
        "id": package["id"],
        "title": package.get("label", package["id"]),
        "authors": ["dwarf"],
        "tags": ["package", package.get("execution_type", "custom-package")],
        "target": {
            "implementation": _manifest_target_implementation(
                package["id"],
                package.get("label", ""),
                profile.get("runtime_root", ""),
            ),
            "version": "any",
        },
        "runtime": runtime,
        "profile": profile_id,
        "seed": "0xD00D0001",
        "load": [
            {
                "primitive": "load_shell_command",
                "command": f"bash -lc {shlex.quote(command)}",
                "timeout_seconds": 120,
                "expect_exit": 0,
            }
            for command in commands
        ],
        "probes": [],
        "assertions": [{"primitive": "load_events_are_ok", "min_completed": len(commands)}],
        "teardown": [],
    }
    return (json.dumps(body, indent=2) + "\n").encode("utf-8")


def _extract_run_id(text):
    match = re.search(r"run_id:\s+([A-Za-z0-9T\-Z]+)", text or "")
    return match.group(1) if match else None


def _run_smoke_via_remote_scenario(args, config, smoke):
    remote_root = _remote_dwarf_root()
    remote_tmp_dir = remote_root / "tmp-generated-smokes"
    remote_scenario_path = remote_tmp_dir / f"{smoke.id}.json"
    mkdir_result = ssh_command(
        config,
        f"mkdir -p {shlex.quote(str(remote_tmp_dir))}",
        timeout=30,
        dry_run=False,
    )
    if mkdir_result.returncode != 0:
        return mkdir_result
    with tempfile.TemporaryDirectory(prefix="dwarf-smoke-scenario-") as tmpdir:
        scenario_path = Path(tmpdir) / f"{smoke.id}.json"
        scenario_path.write_bytes(_smoke_v1_scenario_yaml(smoke))
        upload_result = rsync_to(config, scenario_path, str(remote_scenario_path), dry_run=False)
        if upload_result.returncode != 0:
            return upload_result
    run_command = (
        f"cd {shlex.quote(str(remote_root))} && "
        f"./cardano-profile scenario run {shlex.quote(str(remote_scenario_path))}"
    )
    run_result = ssh_command(config, run_command, timeout=smoke.timeout_seconds + 120, dry_run=False)
    run_id = _extract_run_id(run_result.stdout)
    if run_id and run_result.returncode == 0:
        log_result = ssh_command(
            config,
            f"cd {shlex.quote(str(remote_root))} && cat runs/{shlex.quote(run_id)}/log.ndjson",
            timeout=60,
            dry_run=False,
        )
        if log_result.returncode == 0 and log_result.stdout:
            stdout = run_result.stdout.rstrip() + "\n\n--- remote log.ndjson ---\n" + log_result.stdout
            return CommandResult(run_result.returncode, stdout, run_result.stderr, run_result.rendered_command)
    return run_result


def _run_fuzz_via_remote_scenario(args, config, fuzz):
    remote_root = _remote_dwarf_root()
    remote_tmp_dir = remote_root / "tmp-generated-fuzz"
    remote_scenario_path = remote_tmp_dir / f"{fuzz.id}.json"
    mkdir_result = ssh_command(
        config,
        f"mkdir -p {shlex.quote(str(remote_tmp_dir))}",
        timeout=30,
        dry_run=False,
    )
    if mkdir_result.returncode != 0:
        return mkdir_result
    with tempfile.TemporaryDirectory(prefix="dwarf-fuzz-scenario-") as tmpdir:
        scenario_path = Path(tmpdir) / f"{fuzz.id}.json"
        scenario_path.write_bytes(_fuzz_v1_scenario_yaml(fuzz))
        upload_result = rsync_to(config, scenario_path, str(remote_scenario_path), dry_run=False)
        if upload_result.returncode != 0:
            return upload_result
    run_command = (
        f"cd {shlex.quote(str(remote_root))} && "
        f"./cardano-profile scenario run {shlex.quote(str(remote_scenario_path))}"
    )
    run_result = ssh_command(config, run_command, timeout=fuzz.timeout_seconds + 120, dry_run=False)
    run_id = _extract_run_id(run_result.stdout)
    if run_id and run_result.returncode == 0:
        log_result = ssh_command(
            config,
            f"cd {shlex.quote(str(remote_root))} && cat runs/{shlex.quote(run_id)}/log.ndjson",
            timeout=60,
            dry_run=False,
        )
        if log_result.returncode == 0 and log_result.stdout:
            stdout = run_result.stdout.rstrip() + "\n\n--- remote log.ndjson ---\n" + log_result.stdout
            return CommandResult(run_result.returncode, stdout, run_result.stderr, run_result.rendered_command)
    return run_result


def _run_evidence_via_remote_scenario(args, config, package):
    remote_root = _remote_dwarf_root()
    remote_tmp_dir = remote_root / "tmp-generated-evidence"
    remote_scenario_path = remote_tmp_dir / f"{package.id}.json"
    mkdir_result = ssh_command(
        config,
        f"mkdir -p {shlex.quote(str(remote_tmp_dir))}",
        timeout=30,
        dry_run=False,
    )
    if mkdir_result.returncode != 0:
        return mkdir_result
    with tempfile.TemporaryDirectory(prefix="dwarf-evidence-scenario-") as tmpdir:
        scenario_path = Path(tmpdir) / f"{package.id}.json"
        scenario_path.write_bytes(_evidence_v1_scenario_yaml(package))
        upload_result = rsync_to(config, scenario_path, str(remote_scenario_path), dry_run=False)
        if upload_result.returncode != 0:
            return upload_result
    run_command = (
        f"cd {shlex.quote(str(remote_root))} && "
        f"./cardano-profile scenario run {shlex.quote(str(remote_scenario_path))}"
    )
    run_result = ssh_command(config, run_command, timeout=240, dry_run=False)
    run_id = _extract_run_id(run_result.stdout)
    if run_id and run_result.returncode == 0:
        log_result = ssh_command(
            config,
            f"cd {shlex.quote(str(remote_root))} && cat runs/{shlex.quote(run_id)}/log.ndjson",
            timeout=60,
            dry_run=False,
        )
        if log_result.returncode == 0 and log_result.stdout:
            stdout = run_result.stdout.rstrip() + "\n\n--- remote log.ndjson ---\n" + log_result.stdout
            return CommandResult(run_result.returncode, stdout, run_result.stderr, run_result.rendered_command)
    return run_result


def _run_package_via_remote_scenario(args, config, package, profile, commands):
    remote_root = _remote_dwarf_root()
    remote_tmp_dir = remote_root / "tmp-generated-packages"
    remote_scenario_path = remote_tmp_dir / f"{package['id']}.json"
    mkdir_result = ssh_command(
        config,
        f"mkdir -p {shlex.quote(str(remote_tmp_dir))}",
        timeout=30,
        dry_run=False,
    )
    if mkdir_result.returncode != 0:
        return mkdir_result
    with tempfile.TemporaryDirectory(prefix="dwarf-package-scenario-") as tmpdir:
        scenario_path = Path(tmpdir) / f"{package['id']}.json"
        scenario_path.write_bytes(_package_v1_scenario_yaml(package, profile, commands))
        upload_result = rsync_to(config, scenario_path, str(remote_scenario_path), dry_run=False)
        if upload_result.returncode != 0:
            return upload_result
    run_command = (
        f"cd {shlex.quote(str(remote_root))} && "
        f"./cardano-profile scenario run {shlex.quote(str(remote_scenario_path))}"
    )
    run_result = ssh_command(config, run_command, timeout=(120 * max(len(commands), 1)) + 120, dry_run=False)
    run_id = _extract_run_id(run_result.stdout)
    if run_id and run_result.returncode == 0:
        log_result = ssh_command(
            config,
            f"cd {shlex.quote(str(remote_root))} && cat runs/{shlex.quote(run_id)}/log.ndjson",
            timeout=60,
            dry_run=False,
        )
        if log_result.returncode == 0 and log_result.stdout:
            stdout = run_result.stdout.rstrip() + "\n\n--- remote log.ndjson ---\n" + log_result.stdout
            return CommandResult(run_result.returncode, stdout, run_result.stderr, run_result.rendered_command)
    return run_result


def _record_forensic_for_legacy_run(args, *, scenario_id, scenario_yaml, profile_id, command_result):
    forensic.record_remote_run(
        scenario_id=scenario_id,
        scenario_yaml=scenario_yaml,
        target={"implementation": "cardano-node", "version": "any"},
        runtime="devnet",
        profile_id=profile_id,
        profile_resolved={"id": profile_id} if profile_id else None,
        command_result=command_result,
        runs_dir=_forensic_runs_dir(args),
        state_dir=_forensic_state_dir(args),
    )


def cmd_compare(args):
    from profile_manager import scenario as scenario_module
    runs_dir = _forensic_runs_dir(args)
    state_dir = _forensic_state_dir(args)
    registry_arg = getattr(args, "registry_path", None)
    registry_path = Path(registry_arg) if registry_arg else None
    impl_dirs = {}
    amaru_md = args.amaru_manifests_dir or os.environ.get("ADA2_DWARF_AMARU_MANIFESTS_DIR")
    cardano_md = args.cardano_node_manifests_dir or os.environ.get("ADA2_DWARF_CARDANO_NODE_MANIFESTS_DIR")
    if amaru_md:
        impl_dirs["amaru"] = amaru_md
    if cardano_md:
        impl_dirs["cardano-node"] = cardano_md
    result = scenario_module.compare_run(
        args.path,
        runs_dir=runs_dir,
        state_dir=state_dir,
        registry_path=registry_path,
        implementation_manifest_dirs=impl_dirs or None,
    )
    print(f"amaru run_id:        {result.runs['amaru'].run_id}")
    print(f"cardano-node run_id: {result.runs['cardano-node'].run_id}")
    print(f"Result: {'AGREED' if result.agreed else 'DIVERGED'}")
    print(f"Comparison: {result.comparison_path}")
    return 0 if result.agreed else 1


def cmd_testcase(args):
    from profile_manager import scenario as scenario_module
    from profile_manager import testcase_lifecycle

    runs_dir = _forensic_runs_dir(args)
    state_dir = _forensic_state_dir(args)
    registry_arg = getattr(args, "registry_path", None)
    registry_path = Path(registry_arg) if registry_arg else None

    if args.testcase_command == "replay":
        handle = testcase_lifecycle.run_replay_case(
            case_id=args.case_id,
            target_implementation=args.target,
            runs_dir=runs_dir,
            state_dir=state_dir,
            scenario_module=scenario_module,
            registry_path=registry_path,
            manifests_dir=args.manifests_dir,
        )
        manifest = json.loads((handle.run_dir / "manifest.json").read_text())
        print(f"Replay case: {args.case_id}")
        print(f"Target:      {args.target}")
        print(f"run_id:      {handle.run_id}")
        print(f"exit_status: {manifest['exit_status']}")
        print(f"bundle:      {handle.run_dir}")
        return 0 if manifest["exit_status"] == "pass" else 1

    if args.testcase_command == "minimize":
        result = testcase_lifecycle.run_minimize_case_with_backend(
            case_id=args.case_id,
            target_implementation=args.target,
            runs_dir=runs_dir,
            state_dir=state_dir,
            manifests_dir=args.manifests_dir,
            backend=args.backend,
        )
        print(f"Minimized case: {args.case_id}")
        print(f"Target:         {args.target}")
        print(f"Tool:           {result['tool']}")
        print(f"Input:          {result['input_path']}")
        print(f"Output:         {result['output_path']}")
        print(f"Size:           {result['original_size']} -> {result['minimized_size']}")
        return 0

    if args.testcase_command == "compare":
        result = testcase_lifecycle.compare_replay_case(
            case_id=args.case_id,
            runs_dir=runs_dir,
            state_dir=state_dir,
            scenario_module=scenario_module,
            registry_path=registry_path,
            amaru_manifests_dir=args.amaru_manifests_dir,
            cardano_node_manifests_dir=args.cardano_node_manifests_dir,
        )
        print(f"Compare case: {args.case_id}")
        print(f"amaru run_id:        {result.runs['amaru'].run_id}")
        print(f"cardano-node run_id: {result.runs['cardano-node'].run_id}")
        print(f"Result: {'AGREED' if result.agreed else 'DIVERGED'}")
        print(f"Comparison: {result.comparison_path}")
        return 0 if result.agreed else 1

    if args.testcase_command == "ingest-run":
        result = testcase_lifecycle.ingest_run_issue(
            runs_dir=runs_dir,
            state_dir=state_dir,
            run_id=args.run_id,
            classification=args.classification,
            triage_reason=args.triage_reason,
            producer=args.producer,
            source_artifact_path=args.source_artifact_path,
        )
        print(f"Ingested run: {args.run_id}")
        print(f"case_id:      {result['case_id']}")
        print(f"bucket_id:    {result['bucket_id']}")
        return 0

    if args.testcase_command == "replay-queue" and args.testcase_queue_command == "run":
        result = testcase_lifecycle.run_replay_queue(
            runs_dir=runs_dir,
            state_dir=state_dir,
            scenario_module=scenario_module,
            registry_path=registry_path,
            manifests_dir=args.manifests_dir,
            amaru_manifests_dir=args.amaru_manifests_dir,
            cardano_node_manifests_dir=args.cardano_node_manifests_dir,
            limit=args.limit,
            case_id=args.case_id,
        )
        print("Replay queue run complete")
        print(f"Processed:      {result['processed']}")
        for item in result["items"]:
            print(f"{item['queue_id']}: {item['case_id']} -> {item['target']} ({item['run_id']})")
        return 0

    if args.testcase_command == "compare-queue" and args.testcase_compare_queue_command == "run":
        result = testcase_lifecycle.run_compare_queue(
            runs_dir=runs_dir,
            state_dir=state_dir,
            scenario_module=scenario_module,
            registry_path=registry_path,
            amaru_manifests_dir=args.amaru_manifests_dir,
            cardano_node_manifests_dir=args.cardano_node_manifests_dir,
            limit=args.limit,
            case_id=args.case_id,
        )
        print("Compare queue run complete")
        print(f"Processed:      {result['processed']}")
        for item in result["items"]:
            status = "AGREED" if item["agreed"] else "DIVERGED"
            print(
                f"{item['queue_id']}: {item['case_id']} -> {status} "
                f"(amaru={item['amaru_run_id']}, cardano-node={item['cardano_node_run_id']})"
            )
        return 0

    if args.testcase_command == "promote" and args.testcase_promote_command == "bucket":
        result = testcase_lifecycle.promote_bucket(
            state_dir=state_dir,
            bucket_id=args.bucket_id,
            promotion_state=args.state,
            summary=args.summary,
            source=args.source,
            actor=args.actor,
        )
        print(f"Promoted bucket: {result['bucket_id']}")
        print(f"State:           {result['promotion']['state']}")
        print(f"Source:          {result['promotion']['source']}")
        print(f"Updated cases:   {result['updated_case_count']}")
        return 0

    if args.testcase_command == "buckets" and args.testcase_buckets_command == "summary":
        result = testcase_lifecycle.summarize_buckets(state_dir=state_dir)
        print("Testcase bucket summary")
        print(f"Buckets:        {result['bucket_count']}")
        print(f"Largest bucket: {result['largest_bucket_case_count']}")
        for row in result["rows"][: args.limit]:
            print(f"{row['bucket_id']}: cases={row['case_count']}")
            signature = row.get("bucket_signature") or {}
            for key in (
                "source_signature",
                "replay_outcome",
                "replay_behavior_signatures",
                "replay_resource_signatures",
                "compare_outcome",
                "compare_run_outcomes",
                "compare_behavior_signatures",
                "compare_resource_signatures",
            ):
                if key in signature:
                    print(f"  {key}={signature[key]}")
            promotion = row.get("promotion") or {}
            if promotion:
                print(f"  promotion_state={promotion.get('state')}")
                print(f"  promoted_cases={promotion.get('case_count')}")
        return 0

    if args.testcase_command == "repair-state":
        result = testcase_lifecycle.repair_state(state_dir=state_dir)
        print("Testcase state repair complete")
        print(f"Cases:          {result['case_count']}")
        print(f"Repaired:       {result['repaired_count']}")
        print(f"Compare queue:  {result['compare_queue_count']}")
        print(f"Bucket rows:    {result['bucket_count']}")
        return 0

    return 2


def cmd_replay(args):
    from profile_manager import scenario as scenario_module
    runs_dir = _forensic_runs_dir(args)
    state_dir = _forensic_state_dir(args)
    registry_path = Path(args.registry_path) if args.registry_path else None
    handle = scenario_module.replay_run(
        args.run_id,
        runs_dir=runs_dir,
        state_dir=state_dir,
        registry_path=registry_path,
    )
    print(f"Replay run_id: {handle.run_id}")
    print(f"Comparison report: {handle.run_dir / 'replay-comparison.md'}")
    cmp_text = (handle.run_dir / "replay-comparison.md").read_text()
    print(cmp_text)
    return 0


def cmd_scenario(args):
    from profile_manager import scenario as scenario_module
    if args.scenario_command == "new":
        output = Path(args.output) if args.output else Path.cwd() / f"{args.name}.yaml"
        written = scenario_templates.render_template(
            template_name=args.template,
            scenario_name=args.name,
            output_path=output,
        )
        print(f"Wrote {written}")
        return 0
    if args.scenario_command == "validate":
        try:
            if args.semantic:
                payload = scenario_module.semantic_validate_scenario(
                    args.path,
                    registry_path=Path(args.registry_path) if args.registry_path else None,
                )
                for warning in payload["warnings"]:
                    print(f"WARN: {warning}")
                if payload["errors"]:
                    for error in payload["errors"]:
                        print(f"FAIL: {error}", file=sys.stderr)
                    return 1
                s = payload["scenario"]
                if payload["warnings"]:
                    print(
                        f"OK: {s.id} (runtime={s.runtime}, target={s.target['implementation']}, warnings={len(payload['warnings'])})"
                    )
                else:
                    print(f"OK: {s.id} (runtime={s.runtime}, target={s.target['implementation']}, semantic)")
                return 0
            s = scenario_module.load_scenario(args.path)
        except scenario_module.ScenarioValidationError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
        print(f"OK: {s.id} (runtime={s.runtime}, target={s.target['implementation']})")
        return 0
    if args.scenario_command == "run":
        runs_dir = _forensic_runs_dir(args)
        state_dir = _forensic_state_dir(args)
        registry_path = Path(args.registry_path) if args.registry_path else None
        handle = scenario_module.run_scenario(
            args.path,
            runs_dir=runs_dir,
            state_dir=state_dir,
            registry_path=registry_path,
        )
        manifest_path = handle.run_dir / "manifest.json"
        import json as _json
        manifest = _json.loads(manifest_path.read_text())
        print(f"run_id: {handle.run_id}")
        print(f"exit_status: {manifest['exit_status']}")
        print(f"assertions: {manifest['assertion_summary']}")
        print(f"bundle: {handle.run_dir}")
        return 0 if manifest["exit_status"] == "pass" else 1
    return 2


def cmd_profile(args):
    if args.profile_command == "new":
        output = Path(args.output) if args.output else Path.cwd() / args.name / "profile.yaml"
        written = profile_templates.render_template(
            template_name=args.template,
            profile_name=args.name,
            output_path=output,
        )
        print(f"Wrote {written}")
        return 0
    return 2


def cmd_coverage(args):
    if args.coverage_command == "aggregate":
        from scripts import runtime_coverage_aggregate

        runs_dir = _forensic_runs_dir(args)
        state_dir = _forensic_state_dir(args)
        manifests_dir = (
            Path(args.manifests_dir)
            if getattr(args, "manifests_dir", None)
            else DWARF_ROOT / "targets" / "manifests"
        )
        report = runtime_coverage_aggregate.build_report(
            runs_root=runs_dir,
            manifests_dir=manifests_dir,
            state_dir=state_dir,
        )
        path = runtime_coverage_aggregate.write_report(report=report, state_dir=state_dir)
        print(json.dumps({"coverage_rollup": str(path), "cell_count": report["cell_count"]}, sort_keys=True))
        return 0
    return 2


def cmd_primitive(args):
    if args.primitive_command == "new":
        from scripts import primitive_scaffold

        repo_root = Path(args.repo_root) if args.repo_root else DWARF_ROOT
        result = primitive_scaffold.scaffold_primitive(repo_root=repo_root, family=args.family, name=args.name)
        print(json.dumps({key: str(value) for key, value in result.items()}, sort_keys=True))
        return 0
    return 2


def cmd_verify(args):
    runs_dir = _forensic_runs_dir(args)
    state_dir = _forensic_state_dir(args)
    result = forensic.verify(args.run_id, runs_dir=runs_dir, state_dir=state_dir)
    if result.ok:
        print(f"OK: {args.run_id} verified ({runs_dir})")
        return 0
    print(f"FAIL: {args.run_id}", file=sys.stderr)
    for err in result.errors:
        print(f"  - {err}", file=sys.stderr)
    return 1


def _read_bundle_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_iso8601(value: str | None):
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _collect_bundle_rows(runs_dir):
    rows = []
    for child in sorted(runs_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest = _read_bundle_json(child / "manifest.json") or {}
        promotion = _read_bundle_json(child / "outputs" / "promotion" / "promotion.json") or {}
        signature = _read_bundle_json(child / "outputs" / "signature" / "signature.json") or {}
        dedupe = _read_bundle_json(child / "outputs" / "dedupe" / "dedupe.json") or {}
        outputs_dir = child / "outputs"
        reason = promotion.get("reason") or {}
        timestamp = str(promotion.get("promotion_timestamp") or signature.get("signed_at_utc") or "")
        scenario = manifest.get("scenario") or {}
        target = manifest.get("target") or {}
        assertion_summary = manifest.get("assertion_summary") or {}
        rows.append(
            {
                "run_id": child.name,
                "timestamp": timestamp,
                "tag": str(reason.get("code") or ""),
                "promoted": bool(promotion),
                "signed": bool(signature) and not bool(signature.get("signing_unavailable")),
                "deduped": bool(dedupe),
                "actor": str(promotion.get("actor") or ""),
                "source_surface": str(promotion.get("source_surface") or ""),
                "scenario_id": str(scenario.get("id") or ""),
                "target": str(target.get("implementation") or ""),
                "exit_status": str(manifest.get("exit_status") or ""),
                "assertion_fail_count": int(assertion_summary.get("fail", 0) or 0),
                "evidence_keys": sorted(
                    path.name for path in outputs_dir.iterdir()
                    if outputs_dir.is_dir() and path.is_dir()
                ) if outputs_dir.is_dir() else [],
            }
        )
    return rows


_BUNDLE_REPLAY_AND_DIFF_EXCLUDED_PARTS = {
    "attestation",
    "bundle-diff",
    "chain-verify",
    "export",
    "promotion",
    "replay",
    "sarif-export",
    "signature",
}


def _infer_bundle_compare_relpaths(run_dir: Path) -> list[str]:
    preferred = []
    fallback = []
    outputs_root = run_dir / "outputs"
    if not outputs_root.is_dir():
        return []
    for artifact in sorted(outputs_root.rglob("*.json")):
        try:
            relpath = artifact.relative_to(run_dir)
        except ValueError:
            continue
        if any(part in _BUNDLE_REPLAY_AND_DIFF_EXCLUDED_PARTS for part in relpath.parts):
            continue
        relpath_text = str(relpath)
        if artifact.name == "manifest.json":
            preferred.append(relpath_text)
        elif artifact.name in {"result.json", "diff.json", "coverage.json", "summary.json"}:
            fallback.append(relpath_text)
    return preferred or fallback


def _format_bundle_replay_and_diff(payload: dict) -> str:
    lines = [
        "Bundle Replay And Diff",
        f"  original_run_id: {payload['original_run_id']}",
        f"  replay_run_id: {payload['replay_run_id']}",
        f"  diff_verdict: {payload['diff_verdict']}",
        "  comparisons:",
    ]
    for item in payload.get("comparisons", []):
        lines.append(
            "    - {relpath}: {verdict}".format(
                relpath=item.get("relpath", "<unknown>"),
                verdict=item.get("verdict", "<unknown>"),
            )
        )
    return "\n".join(lines)


def _classify_replay_drift(relpath: str) -> list[str]:
    relpath = str(relpath)
    if relpath.endswith("outputs/substrate-compose/compose-report.json"):
        return [
            "compose_project_name",
            "runtime_root_path",
            "container_identity",
            "port_allocation",
            "socket_path",
        ]
    if relpath.endswith("outputs/multi-node-observation/observation-summary.json"):
        return [
            "timestamp",
            "runtime_metadata_path",
            "resource_sample",
            "pid",
            "port_allocation",
            "socket_path",
        ]
    if relpath.endswith("outputs/multi-node-observation/correlated-timeline.json"):
        return [
            "timestamp",
            "connection_snapshot",
            "resource_sample",
        ]
    return ["unclassified_diff"]


def _run_bundle_reproduce(*, run_id: str, runs_dir: Path, state_dir: Path, registry_path: Path | None, compare_relpaths: list[str] | None) -> dict:
    payload = _run_bundle_replay_and_diff(
        run_id=run_id,
        runs_dir=runs_dir,
        state_dir=state_dir,
        registry_path=registry_path,
        compare_relpaths=compare_relpaths,
    )
    drift_records = []
    for comparison in payload.get("comparisons", []):
        if comparison.get("verdict") != "diff":
            continue
        drift_records.append(
            {
                "relpath": comparison.get("relpath"),
                "drift_classes": _classify_replay_drift(comparison.get("relpath", "")),
            }
        )
    if not drift_records:
        reproduction_verdict = "byte_identical"
    elif all("unclassified_diff" not in item["drift_classes"] for item in drift_records):
        reproduction_verdict = "semantic_drift_only"
    else:
        reproduction_verdict = "behavioral_diff"
    return {
        **payload,
        "reproduction_verdict": reproduction_verdict,
        "drift_records": drift_records,
    }


def _verify_bundle_archive(tarball_path: Path) -> dict:
    from scripts import bundle_import

    with tempfile.TemporaryDirectory(prefix="bundle-verify-") as tmpdir:
        extract_root = Path(tmpdir) / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tarball_path, "r:gz") as handle:
            handle.extractall(extract_root)
        bundle_roots = sorted(path for path in extract_root.iterdir() if path.is_dir())
        if not bundle_roots:
            return {"verdict": "fail", "reason": "archive does not contain a bundle root directory"}
        bundle_root = bundle_roots[0]
        export_manifest = _read_bundle_json(bundle_root / "EXPORT-MANIFEST.json") or {}
        for entry in export_manifest.get("files") or []:
            file_path = bundle_root / str(entry.get("path") or "")
            if not file_path.is_file():
                return {"verdict": "fail", "reason": "missing_exported_file", "offending_path": str(entry.get("path"))}
            if file_path.stat().st_size != int(entry.get("size_bytes", -1)):
                return {"verdict": "fail", "reason": "size_mismatch", "offending_path": str(entry.get("path"))}
            actual_sha256 = bundle_import.forensic._sha256_hex(file_path.read_bytes())
            if actual_sha256 != entry.get("sha256"):
                return {
                    "verdict": "fail",
                    "reason": "sha256_mismatch",
                    "offending_path": str(entry.get("path")),
                    "expected_sha256": entry.get("sha256"),
                    "actual_sha256": actual_sha256,
                }
        manifest_path = bundle_root / "manifest.json"
        chain_path = bundle_root / "chain.json"
        if not manifest_path.is_file() or not chain_path.is_file():
            return {"verdict": "fail", "reason": "bundle missing manifest.json or chain.json"}
        chain_entry = _read_bundle_json(chain_path) or {}
        recomputed = bundle_import.forensic._sha256_hex(bundle_import.forensic._canonical_json(_read_bundle_json(manifest_path) or {}))
        if recomputed != chain_entry.get("manifest_hash"):
            return {
                "verdict": "fail",
                "reason": "manifest_hash_mismatch",
                "offending_path": "chain.json",
                "expected_manifest_hash": chain_entry.get("manifest_hash"),
                "actual_manifest_hash": recomputed,
            }
        return {
            "verdict": "pass",
            "bundle_root": str(bundle_root),
            "run_id": bundle_root.name,
            "file_count": export_manifest.get("file_count"),
            "manifest_hash": recomputed,
            "chain_verdict": "bundle_local_only" if chain_entry.get("prev_hash") != "genesis" else "genesis_complete",
            "note": (
                "archive validates bundle-local hashes; prev_hash ancestry requires the surrounding runs directory"
                if chain_entry.get("prev_hash") != "genesis"
                else None
            ),
        }


def _run_bundle_replay_and_diff(*, run_id: str, runs_dir: Path, state_dir: Path, registry_path: Path | None, compare_relpaths: list[str] | None) -> dict:
    from scripts import runtime_bundle_diff
    from scripts import runtime_bundle_replay

    original_run_dir = runs_dir / run_id
    if not original_run_dir.exists():
        raise FileNotFoundError(f"missing run dir: {original_run_dir}")
    relpaths = list(compare_relpaths or _infer_bundle_compare_relpaths(original_run_dir))
    if not relpaths:
        raise ValueError(f"no comparable bundle artifacts found under {original_run_dir / 'outputs'}")
    with tempfile.TemporaryDirectory(prefix="bundle-replay-and-diff-") as tmpdir:
        scratch = Path(tmpdir)
        replay_result = runtime_bundle_replay.run_bundle_replay(
            runs_dir=runs_dir,
            state_dir=state_dir,
            registry_path=registry_path,
            output_dir=scratch / "replay",
            target_run_id=run_id,
            compare_relpaths=relpaths,
        )
        diff_result = runtime_bundle_diff.run_bundle_diff(
            runs_dir=runs_dir,
            output_dir=scratch / "diff",
            left_run_id=run_id,
            right_run_id=replay_result["replay_run_id"],
            compare_relpaths=relpaths,
        )
    return {
        "original_run_id": run_id,
        "replay_run_id": replay_result["replay_run_id"],
        "diff_verdict": diff_result["comparison_verdict"],
        "compare_relpaths": relpaths,
        "comparisons": diff_result["comparisons"],
        "replay": replay_result,
        "diff": diff_result,
    }


def cmd_bundle(args):
    runs_dir = _forensic_runs_dir(args)
    if args.bundle_command == "audit-trail":
        payload = _walk_bundle_audit_trail(runs_dir, args.run_id)
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(_format_bundle_audit_trail(payload))
        return 0 if payload["chain_verdict"] == "all-verified" else 1
    if args.bundle_command == "replay-and-diff":
        state_dir = _forensic_state_dir(args)
        registry_path = Path(args.registry_path) if args.registry_path else None
        try:
            payload = _run_bundle_replay_and_diff(
                run_id=args.run_id,
                runs_dir=runs_dir,
                state_dir=state_dir,
                registry_path=registry_path,
                compare_relpaths=args.compare_relpaths,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(_format_bundle_replay_and_diff(payload))
        return 0 if payload["diff_verdict"] == "match" else 1
    if args.bundle_command == "reproduce":
        state_dir = _forensic_state_dir(args)
        registry_path = Path(args.registry_path) if args.registry_path else None
        try:
            payload = _run_bundle_reproduce(
                run_id=args.run_id,
                runs_dir=runs_dir,
                state_dir=state_dir,
                registry_path=registry_path,
                compare_relpaths=args.compare_relpaths,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"Bundle Reproduce\n  original_run_id: {payload['original_run_id']}\n  replay_run_id: {payload['replay_run_id']}\n  reproduction_verdict: {payload['reproduction_verdict']}")
            for drift in payload.get("drift_records", []):
                print(f"  drift: {drift['relpath']} -> {', '.join(drift['drift_classes'])}")
        return 0 if payload["reproduction_verdict"] in {"byte_identical", "semantic_drift_only"} else 1

    if args.bundle_command == "search":
        since_cutoff = _parse_iso8601(args.since) if args.since else None
        until_cutoff = _parse_iso8601(args.until) if args.until else None

        rows = []
        for row in _collect_bundle_rows(runs_dir):
            if args.tag and row["tag"] != args.tag:
                continue
            if args.status == "signed" and not row["signed"]:
                continue
            if args.status == "unsigned" and row["signed"]:
                continue
            if args.status == "promoted" and not row["promoted"]:
                continue
            if args.status == "deduped" and not row["deduped"]:
                continue
            if args.by_scenario and args.by_scenario not in row["scenario_id"]:
                continue
            if args.assertion_fail and row["assertion_fail_count"] < 1:
                continue
            if args.target and row["target"] != args.target:
                continue
            if args.has_evidence_key and args.has_evidence_key not in row["evidence_keys"]:
                continue
            if since_cutoff or until_cutoff:
                row_dt = _parse_iso8601(row["timestamp"])
                if row_dt is None:
                    continue
                if since_cutoff and row_dt < since_cutoff:
                    continue
                if until_cutoff and row_dt > until_cutoff:
                    continue
            rows.append(row)

        rows.sort(key=lambda row: (row["timestamp"], row["run_id"]), reverse=True)
        if args.json:
            print(json.dumps(rows, sort_keys=True))
            return 0
        print("run_id\ttimestamp\ttag\tpromoted\tsigned\tdeduped\tactor\tsource_surface")
        for row in rows:
            print(
                "{run_id}\t{timestamp}\t{tag}\t{promoted}\t{signed}\t{deduped}\t{actor}\t{source_surface}".format(
                    run_id=row["run_id"],
                    timestamp=row["timestamp"],
                    tag=row["tag"],
                    promoted="yes" if row["promoted"] else "no",
                    signed="yes" if row["signed"] else "no",
                    deduped="yes" if row["deduped"] else "no",
                    actor=row["actor"],
                    source_surface=row["source_surface"],
                )
            )
        return 0

    if args.bundle_command == "stats":
        rows = _collect_bundle_rows(runs_dir)
        payload = {
            "total_runs": len(rows),
            "promoted_runs": sum(1 for row in rows if row["promoted"]),
            "signed_runs": sum(1 for row in rows if row["signed"]),
            "deduped_runs": sum(1 for row in rows if row["deduped"]),
            "divergence_tagged_runs": sum(1 for row in rows if row["tag"] == "divergence"),
        }
        print(json.dumps(payload, sort_keys=True))
        return 0

    if args.bundle_command == "list-promoted":
        rows = []
        for child in sorted(runs_dir.iterdir()):
            if not child.is_dir():
                continue
            artifact = child / "outputs" / "promotion" / "promotion.json"
            if not artifact.exists():
                continue
            try:
                payload = json.loads(artifact.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            reason = payload.get("reason") or {}
            rows.append(
                {
                    "run_id": str(payload.get("target_run_id") or child.name),
                    "promotion_timestamp": str(payload.get("promotion_timestamp") or ""),
                    "reason_code": str(reason.get("code") or ""),
                    "reason_text": str(reason.get("text") or ""),
                    "actor": str(payload.get("actor") or ""),
                    "source_surface": str(payload.get("source_surface") or ""),
                }
            )
        rows.sort(key=lambda row: (row["promotion_timestamp"], row["run_id"]), reverse=True)
        print("run_id\tpromotion_timestamp\treason_code\treason_text\tactor\tsource_surface")
        for row in rows:
            print(
                "{run_id}\t{promotion_timestamp}\t{reason_code}\t{reason_text}\t{actor}\t{source_surface}".format(
                    **row
                )
            )
        return 0

    if args.bundle_command == "import":
        if args.runs_dir:
            helper_script = Path(__file__).resolve().parents[1] / "scripts" / "bundle_import.py"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(helper_script),
                    str(args.tarball_path),
                    "--runs-dir",
                    str(args.runs_dir),
                ],
                text=True,
                capture_output=True,
                check=False,
                env=os.environ.copy(),
            )
            if proc.stdout:
                print(proc.stdout, end="")
            if proc.stderr:
                print(proc.stderr, file=sys.stderr, end="")
            return proc.returncode
        tarball_path = Path(args.tarball_path)
        signature_path = Path(args.signature_path) if args.signature_path else tarball_path.parent / "signature.json"
        target_run_id = tarball_path.name.removesuffix("-bundle-export.tar.gz")
        if not signature_path.exists():
            print(
                json.dumps(
                    {
                        "target_run_id": target_run_id,
                        "verdict": "unsigned",
                        "manifest_sha256_recomputed": None,
                        "manifest_sha256_signed": None,
                        "tarball_path": str(tarball_path),
                        "signature_artifact": str(signature_path),
                    },
                    sort_keys=True,
                )
            )
            return 0

        export_helper_script = Path(__file__).resolve().parents[1] / "scripts" / "runtime_bundle_export.py"
        sys.path.insert(0, str(export_helper_script.parent))
        import runtime_bundle_export as bundle_export_helper

        verification = bundle_export_helper.verify_export_signature(tarball_path.parent)
        with tempfile.TemporaryDirectory() as tmpdir:
            extract_root = Path(tmpdir) / "imported"
            extract_root.mkdir(parents=True, exist_ok=True)
            with tarfile.open(tarball_path, "r:gz") as handle:
                handle.extractall(extract_root)
            readme_path = extract_root / "README-export.txt"
            bundle_roots = sorted(path for path in extract_root.iterdir() if path.is_dir())
            bundle_root = bundle_roots[0] if bundle_roots else None
            if bundle_root is not None:
                target_run_id = bundle_root.name
            payload = {
                "target_run_id": target_run_id,
                "tarball_path": str(tarball_path),
                "signature_artifact": str(signature_path),
                "readme_present": readme_path.exists(),
                "extracted_bundle_root": str(bundle_root) if bundle_root else None,
                "has_promotion_artifact": bool(bundle_root and (bundle_root / "outputs" / "promotion" / "promotion.json").exists()),
                "has_dedupe_artifact": bool(bundle_root and (bundle_root / "outputs" / "dedupe" / "dedupe.json").exists()),
                "has_bundle_signature_artifact": bool(bundle_root and (bundle_root / "outputs" / "signature" / "signature.json").exists()),
                "bundle_verify_command": (
                    f"cardano-profile bundle verify {target_run_id} --runs-dir <extracted-parent>/runs-or-parent"
                    if target_run_id
                    else None
                ),
                **verification,
            }
        print(json.dumps(payload, sort_keys=True))
        return 0 if payload["verdict"] != "tampered" else 1

    if args.bundle_command == "promote":
        run_dir = runs_dir / args.run_id
        if not run_dir.exists():
            print(f"missing run dir: {run_dir}", file=sys.stderr)
            return 1
        output_dir = run_dir / "outputs" / "promotion"
        helper_script = Path(__file__).resolve().parents[1] / "scripts" / "runtime_bundle_promote.py"
        proc = subprocess.run(
            [
                sys.executable,
                str(helper_script),
                "--output-dir",
                str(output_dir),
                "--target-run-id",
                args.run_id,
                "--reason-code",
                args.reason_code,
                "--reason-text",
                args.reason_text,
                "--operator-notes",
                args.operator_notes,
                "--actor",
                args.actor,
                "--source-surface",
                "cli",
            ],
            text=True,
            capture_output=True,
            check=False,
            env=os.environ.copy(),
        )
    elif args.bundle_command == "dedupe":
        run_dir = runs_dir / args.run_id
        if not run_dir.exists():
            print(f"missing run dir: {run_dir}", file=sys.stderr)
            return 1
        output_dir = run_dir / "outputs" / "dedupe"
        helper_script = Path(__file__).resolve().parents[1] / "scripts" / "runtime_bundle_dedupe.py"
        command = [
            sys.executable,
            str(helper_script),
            "--output-dir",
            str(output_dir),
            "--runs-dir",
            str(runs_dir),
            "--target-run-id",
            args.run_id,
        ]
        if args.signature_primitive:
            command.extend(["--signature-primitive", args.signature_primitive])
        proc = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            env=os.environ.copy(),
        )
    elif args.bundle_command == "sign":
        run_dir = runs_dir / args.run_id
        if not run_dir.exists():
            print(f"missing run dir: {run_dir}", file=sys.stderr)
            return 1
        output_dir = run_dir / "outputs" / "signature"
        helper_script = Path(__file__).resolve().parents[1] / "scripts" / "runtime_bundle_sign.py"
        env = os.environ.copy()
        env["ADA2_DWARF_RUN_DIR"] = str(run_dir)
        proc = subprocess.run(
            [
                sys.executable,
                str(helper_script),
                "--output-dir",
                str(output_dir),
                "--target-run-id",
                args.run_id,
                "--signing-actor",
                args.signing_actor,
            ],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
    elif args.bundle_command == "export":
        run_dir = runs_dir / args.run_id
        if not run_dir.exists():
            print(f"missing run dir: {run_dir}", file=sys.stderr)
            return 1
        if args.to:
            helper_script = Path(__file__).resolve().parents[1] / "scripts" / "bundle_export.py"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(helper_script),
                    str(run_dir),
                    "--to",
                    str(args.to),
                ],
                text=True,
                capture_output=True,
                check=False,
                env=os.environ.copy(),
            )
            if proc.stdout:
                print(proc.stdout, end="")
            if proc.stderr:
                print(proc.stderr, file=sys.stderr, end="")
            return proc.returncode
        output_dir = run_dir / "outputs" / "export"
        helper_script = Path(__file__).resolve().parents[1] / "scripts" / "runtime_bundle_export.py"
        env = os.environ.copy()
        env["ADA2_DWARF_RUN_DIR"] = str(run_dir)
        proc = subprocess.run(
            [
                sys.executable,
                str(helper_script),
                "--output-dir",
                str(output_dir),
                "--target-run-id",
                args.run_id,
                "--signing-actor",
                args.signing_actor,
            ],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
    elif args.bundle_command == "verify":
        bundle_ref = Path(args.bundle_ref)
        if bundle_ref.is_file():
            payload = _verify_bundle_archive(bundle_ref)
            print(json.dumps(payload, sort_keys=True))
            return 0 if payload.get("verdict") == "pass" else 1
        run_dir = runs_dir / args.bundle_ref
        if not run_dir.exists():
            print(f"missing run dir: {run_dir}", file=sys.stderr)
            return 1
        helper_script = Path(__file__).resolve().parents[1] / "scripts" / "runtime_bundle_sign.py"
        signature_path = run_dir / "outputs" / "signature" / "signature.json"
        if not signature_path.exists():
            payload = {
                "target_run_id": args.bundle_ref,
                "verdict": "unsigned",
                "manifest_sha256_recomputed": None,
                "manifest_sha256_signed": None,
                "signature_artifact": str(signature_path),
            }
            print(json.dumps(payload, sort_keys=True))
            return 0
        sys.path.insert(0, str(helper_script.parent))
        import runtime_bundle_sign as bundle_sign_helper

        result = bundle_sign_helper.verify_signature(run_dir=run_dir, signature_path=signature_path)
        payload = {
            "target_run_id": args.bundle_ref,
            "signature_artifact": str(signature_path),
            **result,
        }
        print(json.dumps(payload, sort_keys=True))
        return 0 if payload["verdict"] != "tampered" else 1
    else:
        return 2

    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, file=sys.stderr, end="")
    if proc.returncode == 0:
        if args.bundle_command == "promote":
            print(f"promotion_artifact: {output_dir / 'promotion.json'}")
        elif args.bundle_command == "dedupe":
            print(f"dedupe_artifact: {output_dir / 'dedupe.json'}")
        elif args.bundle_command == "sign":
            print(f"signature_artifact: {output_dir / 'signature.json'}")
        elif args.bundle_command == "export":
            print(f"export_artifact: {output_dir / (args.run_id + '-bundle-export.tar.gz')}")
            print(f"export_signature_artifact: {output_dir / 'signature.json'}")
    return proc.returncode


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    print_banner()
    if args.command == "intake":
        return run_intake()
    if args.command == "list-profiles":
        print(profile_list_text(), end="")
        return 0
    if args.command == "backup":
        return cmd_backup(args)
    if args.command == "restore":
        return cmd_restore(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "config":
        return cmd_config(args)
    if args.command == "prereq-check":
        return cmd_prereq_check(args)
    if args.command == "prereq-install":
        return cmd_prereq_install(args)
    if args.command == "deploy":
        return cmd_deploy(args)
    if args.command == "remove":
        return cmd_remove(args)
    if args.command == "snapshot":
        return cmd_snapshot(args)
    if args.command == "inspect":
        return cmd_inspect(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "logs":
        return cmd_logs(args)
    if args.command == "component":
        return cmd_component(args)
    if args.command == "diff":
        print(profile_diff_text(args.left_profile_id, args.right_profile_id), end="")
        return 0
    if args.command == "test":
        return cmd_test(args)
    if args.command == "evidence":
        return cmd_evidence(args)
    if args.command == "fuzz":
        return cmd_fuzz(args)
    if args.command == "coverage":
        return cmd_coverage(args)
    if args.command == "package":
        return cmd_package(args)
    if args.command == "bundle":
        return cmd_bundle(args)
    if args.command == "dashboard":
        return cmd_dashboard(args)
    if args.command == "verify":
        return cmd_verify(args)
    if args.command == "scenario":
        return cmd_scenario(args)
    if args.command == "profile":
        return cmd_profile(args)
    if args.command == "primitive":
        return cmd_primitive(args)
    if args.command == "replay":
        return cmd_replay(args)
    if args.command == "compare":
        return cmd_compare(args)
    if args.command == "testcase":
        return cmd_testcase(args)
    parser.error(f"Unhandled command: {args.command}")
    return 2
