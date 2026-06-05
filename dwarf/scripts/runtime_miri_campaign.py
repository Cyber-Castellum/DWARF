#!/usr/bin/env python3

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


def _build_env(base_env: dict[str, str] | None = None, *, toolchain: str, miriflags: list[str]) -> dict[str, str]:
    env = dict(base_env or os.environ.copy())
    cargo_bin = str(Path.home() / ".cargo" / "bin")
    path = env.get("PATH", "")
    parts = [part for part in path.split(os.pathsep) if part]
    if cargo_bin not in parts:
        parts.insert(0, cargo_bin)
        env["PATH"] = os.pathsep.join(parts)
    env["RUSTUP_TOOLCHAIN"] = toolchain
    if miriflags:
        env["MIRIFLAGS"] = " ".join(miriflags)
    return env


def _cargo_binary(env: dict[str, str]) -> str:
    cargo = shutil.which("cargo", path=env.get("PATH"))
    if cargo:
        return cargo
    return str(Path.home() / ".cargo" / "bin" / "cargo")


def _extract_tests_run(stdout: str) -> int:
    matches = re.findall(r"test result: .*? (\d+) passed;", stdout)
    if matches:
        return sum(int(match) for match in matches)
    running = re.findall(r"running (\d+) tests", stdout)
    return sum(int(match) for match in running)


def _extract_ub_signatures(stderr: str) -> list[str]:
    signatures: list[str] = []
    for line in stderr.splitlines():
        text = line.strip()
        if "Undefined Behavior" in text:
            signatures.append(text)
    return signatures


def _build_miri_command(*, cargo: str, package: str, test_filter: str | None) -> list[str]:
    command = [cargo, "miri", "test", "-p", package]
    if test_filter:
        command.append(test_filter)
    command.extend(["--", "--nocapture"])
    return command


def _write_summary(output_dir: Path, report: dict) -> None:
    lines = [
        "# MIRI Campaign",
        "",
        f"- tests_run: {report['tests_run']}",
        f"- test_failures: {report['test_failures']}",
        f"- ub_findings: {report['ub_findings']['count']}",
    ]
    for pkg in report.get("package_reports", []):
        lines.extend(
            [
                "",
                f"## {pkg['package']}",
                "",
                f"- tests_run: {pkg['tests_run']}",
                f"- test_failures: {pkg['test_failures']}",
                f"- ub_findings: {pkg['ub_findings']['count']}",
            ]
        )
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_miri_campaign(config: dict, *, env: dict[str, str] | None = None) -> Path:
    repo_dir = Path(config["repo_dir"])
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    packages = [str(pkg) for pkg in config["packages"]]
    toolchain = str(config.get("toolchain", "nightly-2025-11-21"))
    miriflags = [str(flag) for flag in config.get("miriflags", ["-Zmiri-disable-isolation"])]
    test_filter = config.get("test_filter")

    built_env = _build_env(env, toolchain=toolchain, miriflags=miriflags)
    cargo = _cargo_binary(built_env)

    package_reports = []
    combined_stdout: list[str] = []
    combined_stderr: list[str] = []
    total_tests_run = 0
    total_test_failures = 0
    ub_signatures: list[str] = []

    for package in packages:
        command = _build_miri_command(cargo=cargo, package=package, test_filter=test_filter)
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
        tests_run = _extract_tests_run(stdout)
        findings = _extract_ub_signatures(stderr)
        test_failures = 0 if proc.returncode == 0 else max(1, len(findings))
        package_reports.append(
            {
                "package": package,
                "command": command,
                "exit_code": proc.returncode,
                "tests_run": tests_run,
                "test_failures": test_failures,
                "ub_findings": {
                    "count": len(findings),
                    "signatures": findings,
                },
            }
        )
        total_tests_run += tests_run
        total_test_failures += test_failures
        ub_signatures.extend(findings)
        combined_stdout.append(f"## {package}\n{stdout}")
        combined_stderr.append(f"## {package}\n{stderr}")

    (output_dir / "stdout.log").write_text("\n".join(combined_stdout), encoding="utf-8")
    (output_dir / "stderr.log").write_text("\n".join(combined_stderr), encoding="utf-8")

    report = {
        "repo_dir": str(repo_dir),
        "packages": packages,
        "toolchain": toolchain,
        "miriflags": miriflags,
        "test_filter": test_filter,
        "tests_run": total_tests_run,
        "test_failures": total_test_failures,
        "ub_findings": {
            "count": len(ub_signatures),
            "signatures": ub_signatures,
        },
        "package_reports": package_reports,
    }
    report_path = output_dir / "result.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_summary(output_dir, report)
    return report_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--package", action="append", dest="packages", required=True)
    parser.add_argument("--toolchain", default="nightly-2025-11-21")
    parser.add_argument("--miri-flag", action="append", dest="miriflags", default=[])
    parser.add_argument("--test-filter")
    args = parser.parse_args(argv)

    report_path = run_miri_campaign(vars(args))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(
        " ".join(
            [
                "miri_completed=true",
                f"tests_run={report['tests_run']}",
                f"test_failures={report['test_failures']}",
                f"ub_findings={report['ub_findings']['count']}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
