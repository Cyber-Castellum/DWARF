#!/usr/bin/env python3

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


def _build_env(base_env: dict[str, str] | None = None, *, toolchain: str | None, cases: int) -> dict[str, str]:
    env = dict(base_env or os.environ.copy())
    cargo_bin = str(Path.home() / ".cargo" / "bin")
    path = env.get("PATH", "")
    parts = [part for part in path.split(os.pathsep) if part]
    if cargo_bin not in parts:
        parts.insert(0, cargo_bin)
        env["PATH"] = os.pathsep.join(parts)
    env["PROPTEST_CASES"] = str(cases)
    if toolchain:
        env["RUSTUP_TOOLCHAIN"] = toolchain
    return env


def _cargo_binary(env: dict[str, str]) -> str:
    cargo = shutil.which("cargo", path=env.get("PATH"))
    if cargo:
        return cargo
    return str(Path.home() / ".cargo" / "bin" / "cargo")


def _build_command(*, cargo: str, package: str, test_filter: str, features: list[str] | None) -> list[str]:
    command = [cargo, "test", "-p", package]
    if features:
        command.extend(["--features", ",".join(features)])
    command.extend([test_filter, "--", "--nocapture"])
    return command


def _extract_properties_run(stdout: str) -> int:
    match = re.search(r"test result: .*? (\d+) passed; (\d+) failed;", stdout)
    if match:
        return int(match.group(1)) + int(match.group(2))
    running = re.findall(r"running (\d+) test", stdout)
    return sum(int(item) for item in running)


def _extract_properties_failed(stdout: str) -> int:
    match = re.search(r"test result: .*? (\d+) passed; (\d+) failed;", stdout)
    if match:
        return int(match.group(2))
    return 0


def _extract_minimal_repro(stderr: str) -> str | None:
    for line in stderr.splitlines():
        if "minimal failing input" in line:
            return line.strip()
    return None


def _write_summary(output_dir: Path, report: dict) -> None:
    lines = [
        "# Proptest Campaign",
        "",
        f"- properties_run: {report['properties_run']}",
        f"- properties_failed: {report['properties_failed']}",
        f"- shrunk_minimal_repros: {len(report['shrunk_minimal_repros'])}",
        "",
    ]
    for check in report.get("check_reports", []):
        lines.extend(
            [
                f"## {check['package']} :: {check['filter']}",
                "",
                f"- properties_run: {check['properties_run']}",
                f"- properties_failed: {check['properties_failed']}",
                "",
            ]
        )
    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def run_proptest_campaign(config: dict, *, env: dict[str, str] | None = None) -> Path:
    repo_dir = Path(config["repo_dir"])
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    checks = list(config["checks"])
    cases = int(config.get("cases", 8))
    toolchain = config.get("toolchain")

    built_env = _build_env(env, toolchain=toolchain, cases=cases)
    cargo = _cargo_binary(built_env)

    check_reports = []
    stdout_chunks = []
    stderr_chunks = []
    properties_run = 0
    properties_failed = 0
    minimal_repros = []

    for check in checks:
        package = str(check["package"])
        test_filter = str(check["filter"])
        features = [str(item) for item in check.get("features", [])]
        command = _build_command(cargo=cargo, package=package, test_filter=test_filter, features=features)
        proc = subprocess.run(
            command,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
            env=built_env,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        ran = _extract_properties_run(stdout)
        failed = _extract_properties_failed(stdout)
        repro = _extract_minimal_repro(stderr)
        if proc.returncode != 0 and failed == 0:
            failed = 1
        report = {
            "package": package,
            "filter": test_filter,
            "features": features,
            "command": command,
            "exit_code": proc.returncode,
            "properties_run": ran,
            "properties_failed": failed,
        }
        if repro:
            report["minimal_repro"] = repro
            minimal_repros.append(
                {
                    "package": package,
                    "filter": test_filter,
                    "minimal_repro": repro,
                }
            )
        check_reports.append(report)
        properties_run += ran
        properties_failed += failed
        stdout_chunks.append(f"## {package} :: {test_filter}\n{stdout}")
        stderr_chunks.append(f"## {package} :: {test_filter}\n{stderr}")

    (output_dir / "stdout.log").write_text("\n".join(stdout_chunks), encoding="utf-8")
    (output_dir / "stderr.log").write_text("\n".join(stderr_chunks), encoding="utf-8")

    report = {
        "repo_dir": str(repo_dir),
        "cases": cases,
        "toolchain": toolchain,
        "properties_run": properties_run,
        "properties_failed": properties_failed,
        "shrunk_minimal_repros": minimal_repros,
        "check_reports": check_reports,
    }
    report_path = output_dir / "result.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_summary(output_dir, report)
    return report_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cases", type=int, default=8)
    parser.add_argument("--toolchain")
    parser.add_argument("--check-json", action="append", dest="checks", required=True)
    args = parser.parse_args(argv)
    checks = [json.loads(item) for item in args.checks]
    config = {
        "repo_dir": args.repo_dir,
        "output_dir": args.output_dir,
        "cases": args.cases,
        "toolchain": args.toolchain,
        "checks": checks,
    }
    report_path = run_proptest_campaign(config)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(
        " ".join(
            [
                "proptest_completed=true",
                f"properties_run={report['properties_run']}",
                f"properties_failed={report['properties_failed']}",
                f"shrunk_minimal_repros={len(report['shrunk_minimal_repros'])}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
