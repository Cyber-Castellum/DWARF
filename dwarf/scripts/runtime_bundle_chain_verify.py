#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from bundle_chain_helpers import walk_bundle_audit_trail  # noqa: E402
from runtime_telemetry import emit_target_event  # noqa: E402


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def infer_runs_dir(explicit_runs_dir: str | None) -> Path | None:
    if explicit_runs_dir:
        return Path(explicit_runs_dir)
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir).parent
    env_runs_dir = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env_runs_dir:
        return Path(env_runs_dir)
    return None


def _default_output_dir() -> Path:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir) / "outputs" / "chain-verify"
    return Path.cwd() / "outputs" / "chain-verify"


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


def run_chain_verify(*, runs_dir: Path, target_run_id: str, output_dir: Path) -> dict:
    payload = walk_bundle_audit_trail(runs_dir, target_run_id)
    payload = {
        "schema_version": "v1",
        "verified_at_utc": utc_timestamp(),
        "target_run_id": target_run_id,
        **payload,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "chain-verify-report.json"
    report_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["report_relpath"] = _relative_artifact_path(report_path)
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Verify an attestation chain for a captured bundle")
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--target-run-id", required=True)
    args = parser.parse_args(argv[1:])

    runs_dir = infer_runs_dir(args.runs_dir)
    if runs_dir is None:
        print("missing runs dir", file=sys.stderr)
        return 1
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()

    emit_target_event(
        primitive="runtime_bundle_chain_verify",
        event="bundle_chain_verify_started",
        payload={
            "runs_dir": str(runs_dir),
            "target_run_id": args.target_run_id,
            "output_dir": str(output_dir),
        },
    )
    result = run_chain_verify(
        runs_dir=runs_dir,
        target_run_id=args.target_run_id,
        output_dir=output_dir,
    )
    emit_target_event(
        primitive="runtime_bundle_chain_verify",
        event="bundle_chain_verify_completed",
        payload=result,
    )
    print(
        "target_run_id={target_run_id} chain_length={chain_length} chain_verdict={chain_verdict} report_relpath={report_relpath}".format(
            target_run_id=result["target_run_id"],
            chain_length=result["chain_length"],
            chain_verdict=result["chain_verdict"],
            report_relpath=result["report_relpath"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
