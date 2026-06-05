#!/usr/bin/env python3

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


def _build_env(base_env: dict[str, str] | None = None, *, toolchain: str | None = None) -> dict[str, str]:
    env = dict(base_env or os.environ.copy())
    cargo_bin = str(Path.home() / ".cargo" / "bin")
    path = env.get("PATH", "")
    parts = [part for part in path.split(os.pathsep) if part]
    if cargo_bin not in parts:
        parts.insert(0, cargo_bin)
        env["PATH"] = os.pathsep.join(parts)
    if toolchain:
        env["RUSTUP_TOOLCHAIN"] = toolchain
    return env


def _cargo_binary(env: dict[str, str]) -> str:
    cargo = shutil.which("cargo", path=env.get("PATH"))
    if cargo:
        return cargo
    return str(Path.home() / ".cargo" / "bin" / "cargo")


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _build_list_command(*, cargo: str, file: str, package: str | None, baseline: str, no_config: bool) -> list[str]:
    command = [cargo, "mutants", "--file", file]
    if package:
        command.extend(["--package", package])
    command.extend(["--list", "--json", f"--baseline={baseline}"])
    if no_config:
        command.append("--no-config")
    return command


def _build_campaign_command(
    *,
    cargo: str,
    file: str,
    package: str | None,
    output_dir: Path,
    jobs: int,
    timeout: int,
    baseline: str,
    no_config: bool,
) -> list[str]:
    command = [cargo, "mutants", "--file", file]
    if package:
        command.extend(["--package", package])
    command.extend(
        [
            "--output",
            str(output_dir),
            "--jobs",
            str(jobs),
            "--timeout",
            str(timeout),
            f"--baseline={baseline}",
        ]
    )
    if no_config:
        command.append("--no-config")
    return command


def _parse_mutants_report(output_dir: Path, *, list_candidate_count: int, campaign_exit_code: int) -> dict:
    mutants_out_dir = output_dir / "mutants.out"
    mutants_json_path = mutants_out_dir / "mutants.json"
    caught_path = mutants_out_dir / "caught.txt"
    missed_path = mutants_out_dir / "missed.txt"
    timeout_path = mutants_out_dir / "timeout.txt"
    unviable_path = mutants_out_dir / "unviable.txt"

    mutants_json_count = 0
    if mutants_json_path.is_file():
        payload = json.loads(mutants_json_path.read_text(encoding="utf-8"))
        mutants_json_count = len(payload)

    candidate_count = max(list_candidate_count, mutants_json_count)
    killed_count = _line_count(caught_path)
    survived_count = _line_count(missed_path)
    timeout_count = _line_count(timeout_path)
    unviable_count = _line_count(unviable_path)
    tested_count = killed_count + survived_count + timeout_count + unviable_count
    kill_rate = (killed_count / candidate_count) if candidate_count else 0.0

    report = {
        "candidate_count": candidate_count,
        "tested_count": tested_count,
        "killed_count": killed_count,
        "survived_count": survived_count,
        "timeout_count": timeout_count,
        "unviable_count": unviable_count,
        "kill_rate": kill_rate,
        "campaign_exit_code": campaign_exit_code,
        "mutants_out_dir": str(mutants_out_dir),
        "mutants_json_path": str(mutants_json_path),
        "caught_path": str(caught_path),
        "missed_path": str(missed_path),
        "timeout_path": str(timeout_path),
        "unviable_path": str(unviable_path),
    }
    return report


def _write_summary(output_dir: Path, *, report: dict, list_command: list[str], campaign_command: list[str]) -> None:
    summary = "\n".join(
        [
            "# Cargo Mutants Campaign",
            "",
            f"- candidate_count: {report['candidate_count']}",
            f"- tested_count: {report['tested_count']}",
            f"- killed_count: {report['killed_count']}",
            f"- survived_count: {report['survived_count']}",
            f"- timeout_count: {report['timeout_count']}",
            f"- unviable_count: {report['unviable_count']}",
            f"- kill_rate: {report['kill_rate']:.4f}",
            f"- campaign_exit_code: {report['campaign_exit_code']}",
            "",
            "## Commands",
            "",
            f"- list: `{' '.join(list_command)}`",
            f"- campaign: `{' '.join(campaign_command)}`",
            "",
        ]
    )
    (output_dir / "summary.md").write_text(summary, encoding="utf-8")


def run_cargo_mutants_campaign(config: dict, *, env: dict[str, str] | None = None) -> Path:
    repo_dir = Path(config["repo_dir"])
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    built_env = _build_env(env, toolchain=config.get("toolchain"))
    cargo = _cargo_binary(built_env)
    file = str(config["file"])
    package = config.get("package")
    jobs = int(config.get("jobs", 1))
    timeout = int(config.get("timeout", 20))
    baseline = str(config.get("baseline", "skip"))
    no_config = bool(config.get("no_config", False))

    list_command = _build_list_command(
        cargo=cargo,
        file=file,
        package=package,
        baseline=baseline,
        no_config=no_config,
    )
    list_proc = subprocess.run(
        list_command,
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
        env=built_env,
    )
    if list_proc.returncode != 0:
        raise RuntimeError(f"cargo mutants list failed with exit {list_proc.returncode}: {list_proc.stdout}{list_proc.stderr}")
    listed = json.loads(list_proc.stdout or "[]")
    candidate_count = len(listed)

    campaign_command = _build_campaign_command(
        cargo=cargo,
        file=file,
        package=package,
        output_dir=output_dir,
        jobs=jobs,
        timeout=timeout,
        baseline=baseline,
        no_config=no_config,
    )
    campaign_proc = subprocess.run(
        campaign_command,
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
        env=built_env,
    )

    (output_dir / "campaign.stdout.log").write_text(campaign_proc.stdout or "", encoding="utf-8")
    (output_dir / "campaign.stderr.log").write_text(campaign_proc.stderr or "", encoding="utf-8")
    report = _parse_mutants_report(
        output_dir,
        list_candidate_count=candidate_count,
        campaign_exit_code=campaign_proc.returncode,
    )
    report.update(
        {
            "repo_dir": str(repo_dir),
            "file": file,
            "package": package,
            "jobs": jobs,
            "timeout": timeout,
            "baseline": baseline,
            "no_config": no_config,
        }
    )

    report_path = output_dir / "campaign-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_summary(output_dir, report=report, list_command=list_command, campaign_command=campaign_command)
    return report_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--package")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--baseline", default="skip")
    parser.add_argument("--toolchain")
    parser.add_argument("--no-config", action="store_true")
    args = parser.parse_args(argv)

    report_path = run_cargo_mutants_campaign(vars(args))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(
        " ".join(
            [
                "cargo_mutants_completed=true",
                f"candidate_count={report['candidate_count']}",
                f"killed_count={report['killed_count']}",
                f"survived_count={report['survived_count']}",
                f"kill_rate={report['kill_rate']:.4f}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
