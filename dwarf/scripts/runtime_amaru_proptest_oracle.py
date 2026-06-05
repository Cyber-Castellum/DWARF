#!/usr/bin/env python3
"""Run selected Amaru proptest fixtures and capture any fixture-emitted corpus."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


def _cargo_binary(env: dict[str, str]) -> str:
    path_value = env.get("PATH", "")
    for root in path_value.split(os.pathsep):
        candidate = Path(root) / "cargo"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return "cargo"


def _build_env(base_env: dict[str, str], *, corpus_dir: Path, corpus_size: int) -> dict[str, str]:
    env = dict(base_env)
    cargo_bin = str(Path.home() / ".cargo" / "bin")
    env["PATH"] = cargo_bin if not env.get("PATH") else f"{env['PATH']}:{cargo_bin}"
    env["PROPTEST_CASES"] = str(corpus_size)
    env["DWARF_PROPTEST_ORACLE_OUTPUT_DIR"] = str(corpus_dir)
    return env


def _base_command(*, cargo: str, toolchain: str | None, target_subcrate: str, fixture_filter: str | None) -> list[str]:
    command = [cargo]
    if toolchain:
        command.append(f"+{toolchain}")
    command.extend(["test", "-p", target_subcrate])
    if fixture_filter:
        command.append(fixture_filter)
    return command


def _parse_fixture_list(stdout: str) -> list[str]:
    fixtures = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.endswith(": test"):
            fixtures.append(stripped[:-6])
    return fixtures


def _limit_corpus(corpus_dir: Path, limit: int) -> list[Path]:
    files = sorted(path for path in corpus_dir.rglob("*") if path.is_file())
    if len(files) <= limit:
        return files
    for extra in files[limit:]:
        extra.unlink()
    return files[:limit]


def _write_summary(output_dir: Path, report: dict) -> None:
    lines = [
        "# Amaru Proptest Oracle",
        "",
        f"- Fixtures run: {len(report['fixtures_run'])}",
        f"- Corpus inputs captured: {report['corpus_inputs_captured']}",
        f"- Corpus path: `{report['corpus_path']}`",
    ]
    if report["corpus_inputs_captured"] == 0:
        lines.append("- Note: selected fixtures did not emit corpus files into the oracle output directory.")
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_amaru_proptest_oracle(config: dict, *, env: dict[str, str] | None = None) -> Path:
    repo_dir = Path(config["repo_dir"]).resolve()
    output_dir = Path(config["output_dir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    corpus_dir = output_dir / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    target_subcrate = str(config["target_subcrate"])
    fixture_filter = config.get("fixture_filter")
    corpus_size = int(config.get("corpus_size", 32))
    toolchain = config.get("toolchain")
    oracle_env = _build_env(dict(os.environ if env is None else env), corpus_dir=corpus_dir, corpus_size=corpus_size)
    cargo = _cargo_binary(oracle_env)

    list_command = _base_command(
        cargo=cargo,
        toolchain=toolchain,
        target_subcrate=target_subcrate,
        fixture_filter=fixture_filter,
    ) + ["--", "--list"]
    listed = subprocess.run(
        list_command,
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
        env=oracle_env,
    )
    fixtures_run = _parse_fixture_list(listed.stdout)
    if listed.returncode != 0:
        (output_dir / "stdout.log").write_text(listed.stdout or "", encoding="utf-8")
        (output_dir / "stderr.log").write_text(listed.stderr or "", encoding="utf-8")
        raise RuntimeError(listed.stderr or listed.stdout or f"cargo test --list exited {listed.returncode}")

    run_command = _base_command(
        cargo=cargo,
        toolchain=toolchain,
        target_subcrate=target_subcrate,
        fixture_filter=fixture_filter,
    ) + ["--quiet", "--", "--nocapture"]
    executed = subprocess.run(
        run_command,
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
        env=oracle_env,
    )
    (output_dir / "stdout.log").write_text(executed.stdout or "", encoding="utf-8")
    (output_dir / "stderr.log").write_text(executed.stderr or "", encoding="utf-8")
    if executed.returncode != 0:
        raise RuntimeError(executed.stderr or executed.stdout or f"cargo test exited {executed.returncode}")

    captured = _limit_corpus(corpus_dir, corpus_size)
    report = {
        "fixtures_run": fixtures_run,
        "corpus_inputs_captured": len(captured),
        "corpus_path": str(corpus_dir),
        "target_subcrate": target_subcrate,
        "fixture_filter": fixture_filter,
        "corpus_size": corpus_size,
        "toolchain": toolchain,
        "commands": {
            "list": list_command,
            "run": run_command,
        },
    }
    report_path = output_dir / "result.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_summary(output_dir, report)
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--target-subcrate", required=True)
    parser.add_argument("--fixture-filter")
    parser.add_argument("--corpus-size", type=int, default=32)
    parser.add_argument("--toolchain")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    report_path = run_amaru_proptest_oracle(
        {
            "repo_dir": args.repo_dir,
            "target_subcrate": args.target_subcrate,
            "fixture_filter": args.fixture_filter,
            "corpus_size": args.corpus_size,
            "toolchain": args.toolchain,
            "output_dir": args.output_dir,
        }
    )
    print(f"amaru_proptest_oracle_completed=true report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
