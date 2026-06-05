#!/usr/bin/env python3

import argparse
import json
import os
import signal
import shutil
import subprocess
import time
from pathlib import Path


def materialize_seed_corpus(*, corpus_path: Path, seed_dir: Path) -> list[Path]:
    body = json.loads(corpus_path.read_text(encoding="utf-8"))
    if seed_dir.exists():
        shutil.rmtree(seed_dir)
    seed_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for sequence in body.get("sequences", []):
        lines = [str(transition["message"]["hex"]).lower() for transition in sequence.get("transitions", [])]
        path = seed_dir / f"{sequence['id']}.raw"
        path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        written.append(path)
    return written


def _build_env(base_env: dict[str, str] | None = None, *, aflnet_dir: Path) -> dict[str, str]:
    env = dict(base_env or os.environ.copy())
    cargo_bin = str(Path.home() / ".cargo" / "bin")
    path_entries = [str(aflnet_dir / "bin"), cargo_bin]
    existing = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    for entry in reversed(path_entries):
        if entry not in existing:
            existing.insert(0, entry)
    env["PATH"] = os.pathsep.join(existing)
    env.setdefault("AFL_PATH", str(aflnet_dir))
    env.setdefault("AFL_NO_UI", "1")
    env.setdefault("AFL_SKIP_CPUFREQ", "1")
    env.setdefault("AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES", "1")
    return env


def _afl_fuzz_binary(env: dict[str, str], *, aflnet_dir: Path) -> str:
    top_level = aflnet_dir / "afl-fuzz"
    if top_level.is_file():
        return str(top_level)
    afl_fuzz = shutil.which("afl-fuzz", path=env.get("PATH"))
    if afl_fuzz:
        return afl_fuzz
    fallback = aflnet_dir / "bin" / "afl-fuzz"
    if fallback.is_file():
        return str(fallback)
    raise FileNotFoundError(f"afl-fuzz not found under {aflnet_dir}")


def build_server_command(
    *,
    port: int,
    target_binary_path: Path,
    state_corpus: Path,
    state_report_path: Path,
    server_script_path: Path | None = None,
    server_binary_path: Path | None = None,
) -> list[str]:
    if server_binary_path is not None:
        return [
            str(server_binary_path),
            "--port",
            str(port),
            "--state-corpus",
            str(state_corpus),
            "--state-report",
            str(state_report_path),
        ]
    script_path = server_script_path or (Path(__file__).resolve().parent / "aflnet_state_machine_server.py")
    return [
        "python3",
        str(script_path),
        "--port",
        str(port),
        "--target-binary",
        str(target_binary_path),
        "--state-corpus",
        str(state_corpus),
        "--state-report",
        str(state_report_path),
    ]


def build_aflnet_command(
    *,
    afl_fuzz: str,
    seed_dir: Path,
    output_dir: Path,
    port: int,
    protocol: str,
    seconds: int,
    startup_wait_usec: int,
    server_command: list[str],
    use_dumb_mode: bool = True,
) -> list[str]:
    command = [
        afl_fuzz,
        "-d",
        "-i",
        str(seed_dir),
        "-o",
        str(output_dir),
        "-N",
        f"tcp://127.0.0.1/{port}",
        "-P",
        protocol,
        "-D",
        str(startup_wait_usec),
        "-q",
        "3",
        "-s",
        "3",
        "-E",
        "-K",
        "-m",
        "none",
        "--",
        *server_command,
    ]
    if use_dumb_mode:
        command.insert(2, "-n")
    return command


def _read_fuzzer_stats(path: Path) -> dict:
    stats = {}
    if not path.is_file():
        return stats
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if not key or not value:
            continue
        try:
            if value.endswith("%"):
                stats[key] = float(value[:-1])
            elif "." in value:
                stats[key] = float(value)
            else:
                stats[key] = int(value)
        except ValueError:
            stats[key] = value
    return stats


def _find_fuzzer_stats_path(output_dir: Path) -> Path | None:
    candidates = [
        output_dir / "fuzzer_stats",
        output_dir / "default" / "fuzzer_stats",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _find_plot_data_path(output_dir: Path) -> Path | None:
    candidates = [
        output_dir / "plot_data",
        output_dir / "default" / "plot_data",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _count_plot_data_rows(path: Path | None) -> int:
    if path is None or not path.is_file():
        return 0
    rows = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            rows += 1
    return rows


def _telemetry_validation_errors(
    *,
    report: dict,
    min_execs_done: int,
    min_sessions: int,
    min_plot_data_rows: int,
) -> list[str]:
    errors = []
    if int(report.get("execs_done", 0)) < min_execs_done:
        errors.append(f"execs_done<{min_execs_done}")
    if int(report.get("sessions", 0)) < min_sessions:
        errors.append(f"sessions<{min_sessions}")
    if int(report.get("plot_data_rows", 0)) < min_plot_data_rows:
        errors.append(f"plot_data_rows<{min_plot_data_rows}")
    return errors


def _write_summary(output_dir: Path, report: dict) -> None:
    lines = [
        "# AFLNet Campaign",
        "",
        f"- states_visited: {report['states_visited']}",
        f"- novel_state_count: {report['novel_state_count']}",
        f"- transitions_executed: {report['transitions_executed']}",
        f"- execs_done: {report['execs_done']}",
        f"- execs_per_sec: {report['execs_per_sec']}",
        f"- crashes: {report['crashes']}",
        "",
    ]
    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def _terminate_process_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return


def _process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _cleanup_process_group(pgid: int, *, grace_seconds: float) -> None:
    if not _process_group_exists(pgid):
        return
    _terminate_process_group(pgid, signal.SIGTERM)
    deadline = time.time() + max(0.0, grace_seconds)
    while time.time() < deadline:
        if not _process_group_exists(pgid):
            return
        time.sleep(0.05)
    _terminate_process_group(pgid, signal.SIGKILL)


def _run_aflnet_process(
    *,
    command: list[str],
    env: dict[str, str],
    output_dir: Path,
    seconds: int,
    shutdown_grace_seconds: float,
) -> int:
    stdout_path = output_dir / "stdout.log"
    stderr_path = output_dir / "stderr.log"
    with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open("w", encoding="utf-8") as stderr_handle:
        proc = subprocess.Popen(
            command,
            text=True,
            env=env,
            start_new_session=True,
            stdout=stdout_handle,
            stderr=stderr_handle,
        )
        pgid = proc.pid
        try:
            time.sleep(max(0, seconds))
            try:
                os.kill(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                return proc.wait(timeout=5)
            try:
                return proc.wait(timeout=shutdown_grace_seconds)
            except subprocess.TimeoutExpired:
                os.kill(proc.pid, signal.SIGTERM)
                try:
                    return proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _terminate_process_group(pgid, signal.SIGTERM)
                    try:
                        return proc.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        _terminate_process_group(pgid, signal.SIGKILL)
                        return proc.wait(timeout=5)
        finally:
            _cleanup_process_group(pgid, grace_seconds=1.0)


def run_aflnet_campaign(config: dict, *, env: dict[str, str] | None = None) -> Path:
    aflnet_dir = Path(config["aflnet_dir"])
    target_binary_path = Path(config["target_binary_path"])
    state_corpus = Path(config["state_corpus"])
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    seconds = int(config.get("seconds", 30))
    port = int(config.get("port", 8554))
    protocol = str(config.get("protocol", "SMTP"))
    startup_wait_usec = int(config.get("startup_wait_usec", 100000))
    server_script_path = Path(config["server_script_path"]) if config.get("server_script_path") else None
    server_binary_path = Path(config["server_binary_path"]) if config.get("server_binary_path") else None
    use_dumb_mode = bool(config.get("use_dumb_mode", True))
    timeout_raw = config.get("timeout_seconds")
    timeout_seconds = float(timeout_raw) if timeout_raw is not None else float(max(120, seconds + 60))
    shutdown_grace_seconds = float(config.get("shutdown_grace_seconds", max(5, min(30, timeout_seconds - seconds))))
    min_execs_done = int(config.get("min_execs_done", 2))
    min_sessions = int(config.get("min_sessions", 2))
    min_plot_data_rows = int(config.get("min_plot_data_rows", 1))

    seed_dir = output_dir / "seeds"
    materialize_seed_corpus(corpus_path=state_corpus, seed_dir=seed_dir)
    state_report_path = output_dir / "server-state-report.json"
    server_command = build_server_command(
        port=port,
        target_binary_path=target_binary_path,
        state_corpus=state_corpus,
        state_report_path=state_report_path,
        server_script_path=server_script_path,
        server_binary_path=server_binary_path,
    )
    built_env = _build_env(env, aflnet_dir=aflnet_dir)
    afl_fuzz = _afl_fuzz_binary(built_env, aflnet_dir=aflnet_dir)
    command = build_aflnet_command(
        afl_fuzz=afl_fuzz,
        seed_dir=seed_dir,
        output_dir=output_dir,
        port=port,
        protocol=protocol,
        seconds=seconds,
        startup_wait_usec=startup_wait_usec,
        server_command=server_command,
        use_dumb_mode=use_dumb_mode,
    )

    exit_code = _run_aflnet_process(
        command=command,
        env=built_env,
        output_dir=output_dir,
        seconds=seconds,
        shutdown_grace_seconds=shutdown_grace_seconds,
    )

    stats_path = _find_fuzzer_stats_path(output_dir)
    plot_data_path = _find_plot_data_path(output_dir)
    stats = _read_fuzzer_stats(stats_path) if stats_path is not None else {}
    plot_data_rows = _count_plot_data_rows(plot_data_path)
    if state_report_path.is_file():
        state_report = json.loads(state_report_path.read_text(encoding="utf-8"))
    else:
        state_report = {
            "states_visited": [],
            "states_declared": [],
            "state_count": 0,
            "novel_state_count": 0,
            "transitions_declared": 0,
            "transitions_executed": 0,
            "transition_coverage_pct": 0.0,
            "sessions": 0,
            "invalid_messages": 0,
            "response_codes": {},
        }

    report = {
        "aflnet_dir": str(aflnet_dir),
        "target_binary_path": str(target_binary_path),
        "server_binary_path": str(server_binary_path) if server_binary_path is not None else None,
        "state_corpus": str(state_corpus),
        "protocol": protocol,
        "use_dumb_mode": use_dumb_mode,
        "seconds": seconds,
        "port": port,
        "exit_code": exit_code,
        "states_declared": list(state_report.get("states_declared", [])),
        "states_visited": int(state_report.get("state_count", len(state_report.get("states_visited", [])))),
        "reachable_state_count": int(state_report.get("state_count", len(state_report.get("states_visited", [])))),
        "novel_state_count": int(state_report.get("novel_state_count", 0)),
        "states_visited_labels": list(state_report.get("states_visited", [])),
        "transitions_declared": int(state_report.get("transitions_declared", 0)),
        "transitions_executed": int(state_report.get("transitions_executed", state_report.get("transitions_covered", 0))),
        "transitions_covered": int(state_report.get("transitions_covered", state_report.get("transitions_executed", 0))),
        "transition_coverage_pct": float(state_report.get("transition_coverage_pct", 0.0)),
        "sessions": int(state_report.get("sessions", 0)),
        "response_codes": dict(state_report.get("response_codes", {})),
        "execs_done": int(stats.get("execs_done", 0)),
        "cycles_done": int(stats.get("cycles_done", 0)),
        "execs_per_sec": float(stats.get("execs_per_sec", 0.0)),
        "bitmap_cvg": float(stats.get("bitmap_cvg", 0.0)),
        "crashes": int(stats.get("saved_crashes", 0)),
        "hangs": int(stats.get("saved_hangs", 0)),
        "seed_count": len(list(seed_dir.glob("*.raw"))),
        "novel_state_labels": list(state_report.get("states_visited", [])),
        "covered_transition_labels": list(state_report.get("covered_transition_labels", [])),
        "fuzzer_stats_path": str(stats_path) if stats_path is not None else None,
        "plot_data_path": str(plot_data_path) if plot_data_path is not None else None,
        "plot_data_rows": plot_data_rows,
    }
    validation_errors = _telemetry_validation_errors(
        report=report,
        min_execs_done=min_execs_done,
        min_sessions=min_sessions,
        min_plot_data_rows=min_plot_data_rows,
    )
    report["telemetry_validation_passed"] = not validation_errors
    report["telemetry_validation_errors"] = validation_errors
    report_path = output_dir / "result.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_summary(output_dir, report)
    return report_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aflnet-dir", required=True)
    parser.add_argument("--target-binary-path", required=True)
    parser.add_argument("--state-corpus", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--port", type=int, default=8554)
    parser.add_argument("--protocol", default="SMTP")
    parser.add_argument("--startup-wait-usec", type=int, default=100000)
    parser.add_argument("--server-script-path")
    parser.add_argument("--server-binary-path")
    parser.add_argument("--no-dumb-mode", action="store_true")
    parser.add_argument("--timeout-seconds", type=float)
    parser.add_argument("--min-execs-done", type=int, default=2)
    parser.add_argument("--min-sessions", type=int, default=2)
    parser.add_argument("--min-plot-data-rows", type=int, default=1)
    args = parser.parse_args(argv)
    if args.no_dumb_mode:
        args.use_dumb_mode = False
    delattr(args, "no_dumb_mode")

    report_path = run_aflnet_campaign(vars(args))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(
        " ".join(
            [
                "aflnet_completed=true",
                f"states_visited={report['states_visited']}",
                f"transitions_executed={report['transitions_executed']}",
                f"crashes={report['crashes']}",
            ]
        )
    )
    return 0 if report["states_visited"] >= 1 and report.get("telemetry_validation_passed", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
