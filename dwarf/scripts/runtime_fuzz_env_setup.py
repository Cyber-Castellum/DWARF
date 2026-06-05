#!/usr/bin/env python3

import argparse
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_env() -> dict[str, str]:
    env = os.environ.copy()
    cargo_bin = str(Path.home() / ".cargo" / "bin")
    path = env.get("PATH", "")
    parts = [part for part in path.split(os.pathsep) if part]
    if cargo_bin not in parts:
        parts.insert(0, cargo_bin)
        env["PATH"] = os.pathsep.join(parts)
    return env


def _run_command(command: list[str]) -> CommandResult:
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        env=_build_env(),
    )
    return CommandResult(command=command, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def _format_command(command: list[str]) -> str:
    return " ".join(command)


def _extract_version(stdout: str, stderr: str) -> str | None:
    for stream in (stdout, stderr):
        for line in stream.splitlines():
            text = line.strip()
            if text:
                return text
    return None


def _path_for_binary(binary_name: str, *, which) -> str | None:
    return which(binary_name)


def _check_cargo_subcommand(
    *,
    label: str,
    subcommand: str,
    install_crate: str,
    which,
    runner,
    install_allowed: bool = True,
) -> dict:
    check_command = ["cargo", subcommand, "--version"]
    check_result = runner(check_command)
    if check_result.returncode == 0:
        return {
            "status": "present",
            "satisfied": True,
            "version": _extract_version(check_result.stdout, check_result.stderr),
            "path": _path_for_binary(f"cargo-{subcommand}", which=which),
            "check_command": check_command,
            "install_command": ["cargo", "install", install_crate],
        }

    install_command = ["cargo", "install", install_crate]
    if not install_allowed:
        return {
            "status": "not_attempted",
            "satisfied": False,
            "version": None,
            "path": _path_for_binary(f"cargo-{subcommand}", which=which),
            "check_command": check_command,
            "install_command": install_command,
            "error": _extract_version(check_result.stdout, check_result.stderr),
        }

    install_result = runner(install_command)
    if install_result.returncode != 0:
        return {
            "status": "not_attempted",
            "satisfied": False,
            "version": None,
            "path": _path_for_binary(f"cargo-{subcommand}", which=which),
            "check_command": check_command,
            "install_command": install_command,
            "error": _extract_version(install_result.stdout, install_result.stderr),
        }

    final_check = runner(check_command)
    return {
        "status": "newly_installed" if final_check.returncode == 0 else "not_attempted",
        "satisfied": final_check.returncode == 0,
        "version": _extract_version(final_check.stdout, final_check.stderr),
        "path": _path_for_binary(f"cargo-{subcommand}", which=which),
        "check_command": check_command,
        "install_command": install_command,
    }


def _resolve_llvm_profdata_path(*, runner, stable_toolchain: str = "stable") -> tuple[str | None, str | None]:
    sysroot_result = runner(["rustc", f"+{stable_toolchain}", "--print", "sysroot"])
    if sysroot_result.returncode != 0:
        return None, _extract_version(sysroot_result.stdout, sysroot_result.stderr)
    host_result = runner(["rustc", f"+{stable_toolchain}", "-vV"])
    if host_result.returncode != 0:
        return None, _extract_version(host_result.stdout, host_result.stderr)
    host = None
    for line in host_result.stdout.splitlines():
        if line.startswith("host: "):
            host = line.split(":", 1)[1].strip()
            break
    if not host:
        return None, "unable to determine stable host triple"
    candidate = Path(sysroot_result.stdout.strip()) / "lib" / "rustlib" / host / "bin" / "llvm-profdata"
    return str(candidate), None


def _check_llvm_tools_preview(*, which, runner, install_allowed: bool = True) -> dict:
    check_command = ["rustup", "component", "list", "--installed"]
    check_result = runner(check_command)
    install_command = ["rustup", "component", "add", "llvm-tools-preview", "--toolchain", "stable"]

    installed_components = {
        line.strip()
        for line in check_result.stdout.splitlines()
        if line.strip()
    }
    present = check_result.returncode == 0 and any(
        component == "llvm-tools-preview" or component.startswith("llvm-tools-")
        for component in installed_components
    )
    status = "present"
    if not present:
        if not install_allowed:
            status = "not_attempted"
        else:
            install_result = runner(install_command)
            if install_result.returncode == 0:
                check_result = runner(check_command)
                installed_components = {
                    line.strip()
                    for line in check_result.stdout.splitlines()
                    if line.strip()
                }
                present = check_result.returncode == 0 and any(
                    component == "llvm-tools-preview" or component.startswith("llvm-tools-")
                    for component in installed_components
                )
                status = "newly_installed" if present else "not_attempted"
            else:
                status = "not_attempted"

    path, path_error = _resolve_llvm_profdata_path(runner=runner)
    version = None
    if path:
        version_result = runner([path, "--version"])
        if version_result.returncode == 0:
            version = _extract_version(version_result.stdout, version_result.stderr)

    record = {
        "status": status if present or status != "present" else "present",
        "satisfied": present and path is not None,
        "version": version,
        "path": path,
        "check_command": check_command,
        "install_command": install_command,
    }
    if not record["satisfied"]:
        record["error"] = path_error or _extract_version(check_result.stdout, check_result.stderr)
    return record


def _check_stable_rust(*, which, runner, install_allowed: bool = True) -> dict:
    check_command = ["rustc", "--version"]
    check_result = runner(check_command)
    install_command = ["rustup", "toolchain", "install", "stable"]
    if check_result.returncode == 0:
        path_result = runner(["rustup", "which", "rustc"])
        return {
            "status": "present",
            "satisfied": True,
            "version": _extract_version(check_result.stdout, check_result.stderr),
            "path": _extract_version(path_result.stdout, path_result.stderr) or _path_for_binary("rustc", which=which),
            "check_command": check_command,
            "install_command": install_command,
        }
    if not install_allowed:
        return {
            "status": "not_attempted",
            "satisfied": False,
            "version": None,
            "path": _path_for_binary("rustc", which=which),
            "check_command": check_command,
            "install_command": install_command,
            "error": _extract_version(check_result.stdout, check_result.stderr),
        }
    install_result = runner(install_command)
    if install_result.returncode != 0:
        return {
            "status": "not_attempted",
            "satisfied": False,
            "version": None,
            "path": _path_for_binary("rustc", which=which),
            "check_command": check_command,
            "install_command": install_command,
            "error": _extract_version(install_result.stdout, install_result.stderr),
        }
    final_check = runner(check_command)
    path_result = runner(["rustup", "which", "rustc"])
    return {
        "status": "newly_installed" if final_check.returncode == 0 else "not_attempted",
        "satisfied": final_check.returncode == 0,
        "version": _extract_version(final_check.stdout, final_check.stderr),
        "path": _extract_version(path_result.stdout, path_result.stderr) or _path_for_binary("rustc", which=which),
        "check_command": check_command,
        "install_command": install_command,
    }


def _check_nightly_toolchain(*, nightly_toolchain: str, which, runner) -> dict:
    check_command = ["rustc", f"+{nightly_toolchain}", "--version"]
    check_result = runner(check_command)
    path_command = ["rustup", "which", "--toolchain", nightly_toolchain, "rustc"]
    install_command = ["rustup", "toolchain", "install", nightly_toolchain]
    if check_result.returncode == 0:
        path_result = runner(path_command)
        return {
            "status": "present",
            "satisfied": True,
            "version": _extract_version(check_result.stdout, check_result.stderr),
            "path": _extract_version(path_result.stdout, path_result.stderr),
            "check_command": check_command,
            "install_command": install_command,
        }
    return {
        "status": "not_attempted",
        "satisfied": False,
        "version": None,
        "path": None,
        "check_command": check_command,
        "install_command": install_command,
        "error": _extract_version(check_result.stdout, check_result.stderr),
        "policy": "nightly toolchains are verify-only",
    }


def _component_checks(*, nightly_toolchain: str) -> list[tuple[str, callable]]:
    return [
        ("cargo-afl", lambda **kwargs: _check_cargo_subcommand(label="cargo-afl", subcommand="afl", install_crate="cargo-afl", **kwargs)),
        ("cargo-audit", lambda **kwargs: _check_cargo_subcommand(label="cargo-audit", subcommand="audit", install_crate="cargo-audit", **kwargs)),
        ("cargo-deny", lambda **kwargs: _check_cargo_subcommand(label="cargo-deny", subcommand="deny", install_crate="cargo-deny", **kwargs)),
        ("cargo-fuzz", lambda **kwargs: _check_cargo_subcommand(label="cargo-fuzz", subcommand="fuzz", install_crate="cargo-fuzz", **kwargs)),
        ("llvm-tools-preview", lambda **kwargs: _check_llvm_tools_preview(**kwargs)),
        ("rust-stable", lambda **kwargs: _check_stable_rust(**kwargs)),
        (nightly_toolchain, lambda **kwargs: _check_nightly_toolchain(nightly_toolchain=nightly_toolchain, **kwargs)),
    ]


def provision_fuzz_env(
    *,
    output_dir: Path,
    nightly_toolchain: str = "nightly-2025-11-21",
    runner=_run_command,
    which=shutil.which,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    install_log_path = output_dir / "install-log.txt"
    started_at = time.monotonic()
    log_lines = [f"started_at={_utc_now_iso()}"]
    components = {}

    for component_name, checker in _component_checks(nightly_toolchain=nightly_toolchain):
        record = checker(runner=runner, which=which)
        components[component_name] = record
        log_lines.append(
            " | ".join(
                [
                    component_name,
                    f"status={record['status']}",
                    f"satisfied={record['satisfied']}",
                    f"version={record.get('version')}",
                    f"path={record.get('path')}",
                    f"check={' '.join(record['check_command'])}",
                    f"install={' '.join(record['install_command'])}",
                ]
            )
        )
        if record.get("error"):
            log_lines.append(f"{component_name} error={record['error']}")

    duration = time.monotonic() - started_at
    report = {
        "generated_at": _utc_now_iso(),
        "nightly_toolchain": nightly_toolchain,
        "satisfied": all(record.get("satisfied", False) for record in components.values()),
        "component_count": len(components),
        "status_counts": {
            "present": sum(1 for record in components.values() if record["status"] == "present"),
            "newly_installed": sum(1 for record in components.values() if record["status"] == "newly_installed"),
            "not_attempted": sum(1 for record in components.values() if record["status"] == "not_attempted"),
        },
        "total_install_duration_seconds": round(duration, 3),
        "components": components,
    }
    report_path = output_dir / "provisioning-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    install_log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    return report_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--nightly-toolchain", default="nightly-2025-11-21")
    args = parser.parse_args(argv)

    report_path = provision_fuzz_env(
        output_dir=Path(args.output_dir),
        nightly_toolchain=args.nightly_toolchain,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(
        " ".join(
            [
                f"fuzz_env_satisfied={'true' if report['satisfied'] else 'false'}",
                f"present={report['status_counts']['present']}",
                f"newly_installed={report['status_counts']['newly_installed']}",
                f"not_attempted={report['status_counts']['not_attempted']}",
            ]
        )
    )
    return 0 if report["satisfied"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
