#!/usr/bin/env python3

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


def collect_queue_inputs(queue_dir: Path) -> list[Path]:
    inputs = []
    for path in sorted(queue_dir.rglob("*")):
        if not path.is_file():
            continue
        if ".state" in path.relative_to(queue_dir).parts:
            continue
        inputs.append(path)
    return inputs


def prepare_input_corpus(*, queue_dir: Path, prepared_dir: Path) -> list[Path]:
    if prepared_dir.exists():
        shutil.rmtree(prepared_dir)
    prepared_dir.mkdir(parents=True, exist_ok=True)

    prepared = []
    for index, source in enumerate(collect_queue_inputs(queue_dir)):
        destination = prepared_dir / source.name
        if destination.exists():
            destination = prepared_dir / f"{index:06d}-{source.name}"
        shutil.copy2(source, destination)
        prepared.append(destination)
    return prepared


def resolve_cargo_binary(env: dict[str, str] | None = None) -> str:
    search_path = (env or os.environ).get("PATH")
    cargo = shutil.which("cargo", path=search_path)
    if cargo:
        return cargo
    fallback = Path.home() / ".cargo" / "bin" / "cargo"
    if fallback.is_file():
        return str(fallback)
    raise FileNotFoundError("cargo not found in PATH and ~/.cargo/bin/cargo missing")


def build_cmin_command(
    *,
    cargo_bin: str,
    input_dir: Path,
    output_dir: Path,
    target_binary: Path,
    target_args: list[str],
) -> list[str]:
    return [
        cargo_bin,
        "afl",
        "cmin",
        "-i",
        str(input_dir),
        "-o",
        str(output_dir),
        "--",
        str(target_binary),
        *target_args,
    ]


def build_tmin_command(
    *,
    cargo_bin: str,
    input_path: Path,
    output_path: Path,
    target_binary: Path,
    target_args: list[str],
    temporary_input_path: Path | None = None,
) -> list[str]:
    command = [
        cargo_bin,
        "afl",
        "tmin",
        "-i",
        str(input_path),
        "-o",
        str(output_path),
    ]
    if temporary_input_path is not None:
        command.extend(["-f", str(temporary_input_path)])
    command.extend(["--", str(target_binary), *target_args])
    return command


def build_minimization_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env or os.environ.copy())
    env.setdefault("AFL_NO_UI", "1")
    env.setdefault("AFL_SKIP_CPUFREQ", "1")
    env.setdefault("AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES", "1")
    return env


def _run(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def _reduction_ratio(*, before: int, after: int) -> float:
    if before <= 0:
        return 0.0
    return max(0.0, float(before - after) / float(before))


def _write_markdown_summary(*, output_dir: Path, cmin_stats: dict, tmin_stats: dict) -> None:
    body = "\n".join(
        [
            "# AFL Corpus Minimization Summary",
            "",
            f"- input corpus files: `{cmin_stats['input_count']}`",
            f"- retained files after cmin: `{cmin_stats['output_count']}`",
            f"- cmin reduction ratio: `{cmin_stats['reduction_ratio']:.4f}`",
            f"- representative fallback used: `{cmin_stats['used_representative_fallback']}`",
            f"- tmin outputs: `{tmin_stats['output_count']}`",
            f"- aggregate testcase size reduction: `{tmin_stats['aggregate_reduction_ratio']:.4f}`",
        ]
    )
    (output_dir / "summary.md").write_text(body + "\n", encoding="utf-8")


def run_afl_corpus_min(
    *,
    queue_dir: Path,
    output_dir: Path,
    target_binary: Path,
    target_args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> dict:
    target_args = list(target_args or [])
    env = build_minimization_env(env)
    output_dir.mkdir(parents=True, exist_ok=True)
    prepared_dir = output_dir / "prepared-input"
    cmin_dir = output_dir / "cmin"
    tmin_dir = output_dir / "tmin"
    temp_dir = output_dir / ".tmp"
    if cmin_dir.exists():
        shutil.rmtree(cmin_dir)
    if tmin_dir.exists():
        shutil.rmtree(tmin_dir)
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    cmin_dir.mkdir(parents=True, exist_ok=True)
    tmin_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    prepared_inputs = prepare_input_corpus(queue_dir=queue_dir, prepared_dir=prepared_dir)
    cargo_bin = resolve_cargo_binary(env)
    cmin_proc = _run(
        build_cmin_command(
            cargo_bin=cargo_bin,
            input_dir=prepared_dir,
            output_dir=cmin_dir,
            target_binary=target_binary,
            target_args=target_args,
        ),
        env=env,
    )
    if cmin_proc.returncode != 0:
        raise RuntimeError(f"cargo afl cmin failed with exit {cmin_proc.returncode}: {cmin_proc.stdout}{cmin_proc.stderr}")

    retained = sorted(path for path in cmin_dir.iterdir() if path.is_file())
    used_representative_fallback = False
    raw_output_count = len(retained)
    if not retained and prepared_inputs:
        used_representative_fallback = True
        representative = prepared_inputs[0]
        shutil.copy2(representative, cmin_dir / representative.name)
        retained = [cmin_dir / representative.name]

    cmin_stats = {
        "input_count": len(prepared_inputs),
        "raw_output_count": raw_output_count,
        "output_count": len(retained),
        "reduction_ratio": _reduction_ratio(before=len(prepared_inputs), after=len(retained)),
        "used_representative_fallback": used_representative_fallback,
        "target_binary": str(target_binary),
        "target_args": target_args,
        "queue_dir": str(queue_dir),
        "prepared_dir": str(prepared_dir),
        "stdout": cmin_proc.stdout[-4096:],
        "stderr": cmin_proc.stderr[-4096:],
    }

    tmin_entries = []
    uses_placeholder = "@@" in target_args
    for retained_input in retained:
        minimized_path = tmin_dir / retained_input.name
        temporary_input_path = None
        if uses_placeholder:
            temporary_input_path = temp_dir / f"{retained_input.name}.input"
            shutil.copy2(retained_input, temporary_input_path)
        tmin_proc = _run(
            build_tmin_command(
                cargo_bin=cargo_bin,
                input_path=retained_input,
                output_path=minimized_path,
                target_binary=target_binary,
                target_args=target_args,
                temporary_input_path=temporary_input_path,
            ),
            env=env,
        )
        if tmin_proc.returncode != 0:
            raise RuntimeError(
                f"cargo afl tmin failed with exit {tmin_proc.returncode} for {retained_input.name}: "
                f"{tmin_proc.stdout}{tmin_proc.stderr}"
            )
        if not minimized_path.is_file():
            raise RuntimeError(f"cargo afl tmin did not emit {minimized_path}")
        original_size = retained_input.stat().st_size
        minimized_size = minimized_path.stat().st_size
        tmin_entries.append(
            {
                "input_name": retained_input.name,
                "input_path": str(retained_input),
                "output_path": str(minimized_path),
                "original_size": original_size,
                "minimized_size": minimized_size,
                "reduction_ratio": _reduction_ratio(before=original_size, after=minimized_size),
            }
        )

    total_original_size = sum(entry["original_size"] for entry in tmin_entries)
    total_minimized_size = sum(entry["minimized_size"] for entry in tmin_entries)
    tmin_stats = {
        "input_count": len(retained),
        "output_count": len(tmin_entries),
        "total_original_size": total_original_size,
        "total_minimized_size": total_minimized_size,
        "aggregate_reduction_ratio": _reduction_ratio(before=total_original_size, after=total_minimized_size),
        "entries": tmin_entries,
    }

    (output_dir / "cmin-stats.json").write_text(json.dumps(cmin_stats, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "tmin-stats.json").write_text(json.dumps(tmin_stats, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_markdown_summary(output_dir=output_dir, cmin_stats=cmin_stats, tmin_stats=tmin_stats)
    return {"cmin": cmin_stats, "tmin": tmin_stats}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AFL++ corpus minimization and testcase minimization")
    parser.add_argument("--queue-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--target-binary", required=True)
    parser.add_argument("--target-arg", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = run_afl_corpus_min(
        queue_dir=Path(args.queue_dir),
        output_dir=Path(args.output_dir),
        target_binary=Path(args.target_binary),
        target_args=list(args.target_arg),
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
