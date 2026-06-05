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
if str(DWARF_ROOT) not in sys.path:
    sys.path.insert(0, str(DWARF_ROOT))

from bundle_compare_helpers import compare_relpath  # noqa: E402
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
        return Path(run_dir) / "outputs" / "bundle-diff"
    return Path.cwd() / "outputs" / "bundle-diff"


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


def run_bundle_diff(*, runs_dir: Path, output_dir: Path, left_run_id: str, right_run_id: str, compare_relpaths: list[str]) -> dict:
    left_run_dir = runs_dir / left_run_id
    right_run_dir = runs_dir / right_run_id
    comparisons = [
        compare_relpath(
            left_run_dir=left_run_dir,
            right_run_dir=right_run_dir,
            relpath=relpath,
        )
        for relpath in compare_relpaths
    ]
    comparison_verdict = "match" if all(item["verdict"] == "match" for item in comparisons) else "diff"
    output_dir.mkdir(parents=True, exist_ok=True)
    diff_path = output_dir / "diff.json"
    payload = {
        "schema_version": "v1",
        "left_run_id": left_run_id,
        "right_run_id": right_run_id,
        "left_run_dir": str(left_run_dir),
        "right_run_dir": str(right_run_dir),
        "compared_relpaths": compare_relpaths,
        "comparison_verdict": comparison_verdict,
        "comparisons": comparisons,
        "diffed_at_utc": utc_timestamp(),
    }
    diff_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["diff_relpath"] = _relative_artifact_path(diff_path)
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Compare selected artifacts across any two captured bundles")
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--left-run-id", required=True)
    parser.add_argument("--right-run-id", required=True)
    parser.add_argument("--compare-relpath", action="append", dest="compare_relpaths", required=True)
    args = parser.parse_args(argv[1:])

    runs_dir = infer_runs_dir(args.runs_dir)
    if runs_dir is None:
        print("missing runs dir", file=sys.stderr)
        return 1
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()

    emit_target_event(
        primitive="runtime_bundle_diff",
        event="bundle_diff_started",
        payload={
            "runs_dir": str(runs_dir),
            "left_run_id": args.left_run_id,
            "right_run_id": args.right_run_id,
            "compare_relpaths": args.compare_relpaths,
            "output_dir": str(output_dir),
        },
    )
    result = run_bundle_diff(
        runs_dir=runs_dir,
        output_dir=output_dir,
        left_run_id=args.left_run_id,
        right_run_id=args.right_run_id,
        compare_relpaths=list(args.compare_relpaths),
    )
    emit_target_event(
        primitive="runtime_bundle_diff",
        event="bundle_diff_completed",
        payload=result,
    )
    print(
        "left_run_id={left_run_id} right_run_id={right_run_id} comparison_verdict={comparison_verdict} diff_relpath={diff_relpath}".format(
            left_run_id=result["left_run_id"],
            right_run_id=result["right_run_id"],
            comparison_verdict=result["comparison_verdict"],
            diff_relpath=result["diff_relpath"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
