#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_telemetry import emit_target_event  # noqa: E402


def infer_target_run_id(explicit_target_run_id: str | None) -> str | None:
    if explicit_target_run_id:
        return explicit_target_run_id
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir).name
    return None


def infer_runs_dir(explicit_runs_dir: str | None) -> Path | None:
    if explicit_runs_dir:
        return Path(explicit_runs_dir)
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir).parent
    return None


def _default_output_dir() -> Path:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir) / "outputs" / "dedupe"
    return Path.cwd() / "outputs" / "dedupe"


def _relative_artifact_path(artifact_path: Path) -> str:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        try:
            return str(artifact_path.relative_to(Path(run_dir)))
        except ValueError:
            pass
    parts = artifact_path.parts
    if "outputs" in parts:
        index = parts.index("outputs")
        return str(Path(*parts[index:]))
    return str(artifact_path)


def _stderr_tail(stderr_text: str, max_lines: int = 200) -> str:
    lines = (stderr_text or "").splitlines()
    return "\n".join(lines[-max_lines:])


def _stderr_tail_sha256(stderr_text: str, max_lines: int = 200) -> str:
    tail = _stderr_tail(stderr_text, max_lines=max_lines)
    return hashlib.sha256(tail.encode("utf-8")).hexdigest()


def _load_completed_events(run_dir: Path) -> list[dict]:
    log_path = run_dir / "log.ndjson"
    if not log_path.exists():
        raise RuntimeError(f"missing log file: {log_path}")
    events = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("event") != "completed":
            continue
        payload = entry.get("payload") or {}
        if "helper_exit_code" not in payload:
            continue
        events.append(entry)
    return events


def derive_signature(run_dir: Path, signature_primitive: str | None) -> dict:
    events = _load_completed_events(run_dir)
    if signature_primitive:
        events = [event for event in events if event.get("primitive") == signature_primitive]
    else:
        events = [event for event in events if event.get("primitive") not in {"runtime_bundle_dedupe"}]
    if not events:
        raise RuntimeError(f"no signature-bearing completed event found in {run_dir}")
    event = events[-1]
    payload = event.get("payload") or {}
    stderr_text = str(payload.get("stderr", ""))
    return {
        "primitive": str(event.get("primitive")),
        "helper_exit_code": int(payload.get("helper_exit_code", -1)),
        "stderr_tail_sha256": _stderr_tail_sha256(stderr_text),
        "stderr_tail_line_count": len(_stderr_tail(stderr_text).splitlines()) if _stderr_tail(stderr_text) else 0,
    }


def iter_promoted_run_dirs(runs_dir: Path) -> list[Path]:
    promoted = []
    for child in sorted(runs_dir.iterdir()):
        if not child.is_dir():
            continue
        if (child / "outputs" / "promotion" / "promotion.json").exists():
            promoted.append(child)
    return promoted


def compare_signature(
    *,
    runs_dir: Path,
    signature: dict,
    signature_primitive: str | None,
    target_run_id: str,
) -> dict:
    promoted_run_dirs = iter_promoted_run_dirs(runs_dir)

    matched_run_id = None
    for promoted_run_dir in promoted_run_dirs:
        promoted_signature = derive_signature(promoted_run_dir, signature_primitive)
        if (
            promoted_signature["primitive"] == signature["primitive"]
            and promoted_signature["helper_exit_code"] == signature["helper_exit_code"]
            and promoted_signature["stderr_tail_sha256"] == signature["stderr_tail_sha256"]
        ):
            matched_run_id = promoted_run_dir.name
            break
    return {
        "target_run_id": target_run_id,
        "signature_primitive": signature_primitive or signature["primitive"],
        "signature": signature,
        "verdict": "match" if matched_run_id else "novel",
        "matched_run_id": matched_run_id,
        "promoted_runs_scanned": len(promoted_run_dirs),
    }


def run_dedupe(
    *,
    runs_dir: Path,
    output_dir: Path,
    target_run_id: str,
    signature_primitive: str | None,
) -> dict:
    target_run_dir = runs_dir / target_run_id
    if not target_run_dir.exists():
        raise RuntimeError(f"missing target run dir: {target_run_dir}")
    signature = derive_signature(target_run_dir, signature_primitive)

    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / "dedupe.json"
    payload = {"schema_version": "v1"}
    payload.update(
        compare_signature(
            runs_dir=runs_dir,
            signature=signature,
            signature_primitive=signature_primitive,
            target_run_id=target_run_id,
        )
    )
    artifact_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["dedupe_relpath"] = _relative_artifact_path(artifact_path)
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Compare a target run signature against promoted bundles")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--target-run-id", default=None)
    parser.add_argument("--signature-primitive", default=None)
    args = parser.parse_args(argv[1:])

    runs_dir = infer_runs_dir(args.runs_dir)
    if runs_dir is None:
        print("missing runs dir", file=sys.stderr)
        return 1
    target_run_id = infer_target_run_id(args.target_run_id)
    if not target_run_id:
        print("missing target run id", file=sys.stderr)
        return 1
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()

    emit_target_event(
        primitive="runtime_bundle_dedupe",
        event="bundle_dedupe_started",
        payload={
            "runs_dir": str(runs_dir),
            "target_run_id": target_run_id,
            "signature_primitive": args.signature_primitive,
            "output_dir": str(output_dir),
        },
    )

    result = run_dedupe(
        runs_dir=runs_dir,
        output_dir=output_dir,
        target_run_id=target_run_id,
        signature_primitive=args.signature_primitive,
    )

    emit_target_event(
        primitive="runtime_bundle_dedupe",
        event="bundle_dedupe_completed",
        payload=result,
    )
    print(
        "target_run_id={target_run_id} signature_primitive={signature_primitive} "
        "verdict={verdict} matched_run_id={matched_run_id} promoted_runs_scanned={promoted_runs_scanned} "
        "dedupe_relpath={dedupe_relpath}".format(
            target_run_id=result["target_run_id"],
            signature_primitive=result["signature_primitive"],
            verdict=result["verdict"],
            matched_run_id=result["matched_run_id"] or "none",
            promoted_runs_scanned=result["promoted_runs_scanned"],
            dedupe_relpath=result["dedupe_relpath"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
