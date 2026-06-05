#!/usr/bin/env python3

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve_crashes_dir(bundle_dir: Path) -> Path:
    candidates = [
        bundle_dir / "default" / "crashes",
        bundle_dir / "crashes",
        bundle_dir / "outputs" / "aflpp" / "default" / "crashes",
        bundle_dir / "outputs" / "cargo-fuzz" / "artifacts",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def _sanitize_group_name(signature: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", signature)[:120] or "unknown"


def _signature_for_path(path: Path) -> str:
    for token in path.name.split(","):
        if token.startswith("sig:"):
            return f"sig-{token.split(':', 1)[1]}"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    return f"hash-{digest}"


def _parse_last_find_ts(path: Path) -> int | None:
    for token in path.name.split(","):
        if token.startswith("time:"):
            try:
                return int(token.split(":", 1)[1])
            except ValueError:
                return None
    return None


def _detect_sanitizer_kind(text: str) -> str | None:
    lowered = text.lower()
    if "addresssanitizer" in lowered:
        return "asan"
    if "undefinedbehaviorsanitizer" in lowered:
        return "ubsan"
    if "memorysanitizer" in lowered:
        return "msan"
    if "threadsanitizer" in lowered:
        return "tsan"
    if "leaksanitizer" in lowered:
        return "lsan"
    return None


_FRAME_RE = re.compile(
    r"^\s*#\d+\s+(?:0x[0-9a-fA-F]+\s+in\s+)?(?P<func>[^\s(]+).*?(?P<file>[A-Za-z0-9._/\-]+\.(?:c|cc|cpp|cxx|h|hpp|hh|rs|hs|go|zig|m|mm))?(?::\d+)?"
)


def _normalize_stack_signature(text: str, *, signature_frames: int) -> tuple[str | None, list[str]]:
    frames: list[str] = []
    for line in text.splitlines():
        match = _FRAME_RE.match(line)
        if not match:
            continue
        func = match.group("func") or "unknown"
        file_name = Path(match.group("file")).name if match.group("file") else "unknown"
        frames.append(f"{func}@{file_name}")
        if len(frames) >= signature_frames:
            break
    if not frames:
        return None, []
    return " | ".join(frames), frames


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _build_instance(path: Path, crashes_dir: Path) -> dict:
    return {
        "file_name": path.name,
        "relative_path": _relative_path(path, crashes_dir),
        "size_bytes": path.stat().st_size,
        "signature": _signature_for_path(path),
        "last_find_ts": _parse_last_find_ts(path),
    }


def _group_crashes(crashes_dir: Path) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    if not crashes_dir.is_dir():
        return groups
    for path in sorted(entry for entry in crashes_dir.iterdir() if entry.is_file()):
        signature = _signature_for_path(path)
        groups.setdefault(signature, []).append(_build_instance(path, crashes_dir))
    return groups


def _run_target_for_trace(
    *,
    crash_path: Path,
    target_binary: Path,
    target_args: list[str] | None,
    env: dict | None,
    timeout_seconds: float,
) -> dict:
    args = [str(part).replace("{input}", str(crash_path)) for part in (target_args or ["{input}"])]
    command = [str(target_binary), *args]
    proc = subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
        env=env,
        timeout=timeout_seconds,
    )
    text = "\n".join(part for part in [proc.stderr, proc.stdout] if part)
    sanitizer_kind = _detect_sanitizer_kind(text)
    signature, frames = _normalize_stack_signature(text, signature_frames=5)
    return {
        "command": command,
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-4096:],
        "stderr": proc.stderr[-4096:],
        "sanitizer_kind": sanitizer_kind,
        "normalized_signature": signature,
        "frames": frames,
    }


def _run_minimizer(*, canonical_path: Path, minimized_path: Path, minimizer: dict | None) -> dict:
    if not minimizer:
        shutil.copyfile(canonical_path, minimized_path)
        return {"status": "skipped", "tool": None, "command": None}

    command = [
        str(part).replace("{input}", str(canonical_path)).replace("{output}", str(minimized_path))
        for part in minimizer.get("command", [])
    ]
    proc = subprocess.run(command, capture_output=True, check=False, text=True)
    return {
        "status": "ok" if proc.returncode == 0 else "failed",
        "tool": minimizer.get("tool"),
        "command": command,
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-1024:],
        "stderr": proc.stderr[-1024:],
    }


def _write_markdown_report(output_dir: Path, report: dict) -> Path:
    lines = [
        "# Crash Triage Summary",
        "",
        f"- Crash count: {report['crashes_total']}",
        f"- Group count: {report['unique_signatures']}",
        "",
        "| Signature | Count | Minimization |",
        "| --- | ---: | --- |",
    ]
    for group in report["signatures"]:
        lines.append(
            f"| `{group['signature']}` | {group['count']} | {group['minimization']['status']} |"
        )
    path = output_dir / "triage-report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_crash_triage(
    *,
    bundle_dir: Path,
    output_dir: Path,
    minimizer: dict | None = None,
    target_binary: Path | None = None,
    target_args: list[str] | None = None,
    triage_env: dict | None = None,
    signature_frames: int = 5,
    timeout_seconds: float = 30.0,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    groups_dir = output_dir / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)

    crashes_dir = _resolve_crashes_dir(bundle_dir)
    instances = []
    if crashes_dir.is_dir():
        for path in sorted(entry for entry in crashes_dir.iterdir() if entry.is_file()):
            instances.append(_build_instance(path, crashes_dir))

    grouped: dict[str, list[dict]] = {}
    traces_by_signature: dict[str, dict] = {}
    for instance in instances:
        crash_path = crashes_dir / instance["relative_path"]
        if target_binary is not None:
            trace = _run_target_for_trace(
                crash_path=crash_path,
                target_binary=target_binary,
                target_args=target_args,
                env=triage_env,
                timeout_seconds=timeout_seconds,
            )
            signature, frames = _normalize_stack_signature(
                "\n".join(part for part in [trace["stderr"], trace["stdout"]] if part),
                signature_frames=signature_frames,
            )
            signature = signature or instance["signature"]
            instance["triage"] = {
                "sanitizer_kind": trace["sanitizer_kind"],
                "frames": frames,
                "exit_code": trace["exit_code"],
            }
            traces_by_signature.setdefault(signature, trace)
        else:
            signature = instance["signature"]
        grouped.setdefault(signature, []).append(instance)

    report_groups = []
    crash_count = 0

    for signature in sorted(grouped, key=lambda key: (-len(grouped[key]), key)):
        instances = grouped[signature]
        crash_count += len(instances)
        canonical_instance = instances[0]
        canonical_source = crashes_dir / canonical_instance["relative_path"]
        group_dir = groups_dir / _sanitize_group_name(signature)
        group_dir.mkdir(parents=True, exist_ok=True)
        canonical_output = group_dir / "canonical-input.bin"
        minimized_output = group_dir / "minimized-input.bin"
        shutil.copyfile(canonical_source, canonical_output)
        minimization = _run_minimizer(
            canonical_path=canonical_output,
            minimized_path=minimized_output,
            minimizer=minimizer,
        )
        (group_dir / "sig.txt").write_text(signature + "\n", encoding="utf-8")
        instances_path = group_dir / "instances.json"
        instances_path.write_text(json.dumps(instances, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        trace = traces_by_signature.get(signature)
        if trace:
            (group_dir / "trace.txt").write_text(trace["stderr"] or trace["stdout"], encoding="utf-8")
            trace_summary = {
                "command": trace["command"],
                "exit_code": trace["exit_code"],
                "sanitizer_kind": trace["sanitizer_kind"],
                "frames": trace["frames"],
            }
            (group_dir / "trace.json").write_text(
                json.dumps(trace_summary, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        last_find_ts_values = [item.get("last_find_ts") for item in instances if item.get("last_find_ts") is not None]
        report_groups.append(
            {
                "signature": signature,
                "count": len(instances),
                "canonical_input": str(canonical_output),
                "minimized_input": str(minimized_output),
                "instances_path": str(instances_path),
                "minimization": minimization,
                "exemplar_input_path": canonical_instance["relative_path"],
                "sanitizer_kind": trace["sanitizer_kind"] if trace else None,
                "last_find_ts": max(last_find_ts_values) if last_find_ts_values else None,
            }
        )

    report = {
        "generated_at": _utc_now_iso(),
        "bundle_dir": str(bundle_dir),
        "crashes_dir": str(crashes_dir),
        "crash_count": crash_count,
        "group_count": len(report_groups),
        "groups": report_groups,
        "crashes_total": crash_count,
        "unique_signatures": len(report_groups),
        "signatures": report_groups,
    }
    report_path = output_dir / "triage-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_markdown_report(output_dir, report)
    return report_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--minimizer-json")
    parser.add_argument("--target-binary")
    parser.add_argument("--target-arg", action="append", default=[])
    parser.add_argument("--triage-env-json")
    parser.add_argument("--signature-frames", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)

    minimizer = json.loads(args.minimizer_json) if args.minimizer_json else None
    triage_env = json.loads(args.triage_env_json) if args.triage_env_json else None
    report_path = run_crash_triage(
        bundle_dir=Path(args.bundle_dir),
        output_dir=Path(args.output_dir),
        minimizer=minimizer,
        target_binary=Path(args.target_binary) if args.target_binary else None,
        target_args=args.target_arg or None,
        triage_env=triage_env,
        signature_frames=max(args.signature_frames, 1),
        timeout_seconds=args.timeout_seconds,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(
        " ".join(
            [
                f"crashes_total={report['crashes_total']}",
                f"unique_signatures={report['unique_signatures']}",
                f"triage_relpath={report_path.name}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
