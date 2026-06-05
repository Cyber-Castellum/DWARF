#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
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

from bundle_compare_helpers import compare_relpath as generic_compare_relpath  # noqa: E402
from bundle_compare_helpers import normalize_for_compare as generic_normalize_for_compare  # noqa: E402
from profile_manager import scenario as scenario_module  # noqa: E402
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


def infer_state_dir(explicit_state_dir: str | None) -> Path | None:
    if explicit_state_dir:
        return Path(explicit_state_dir)
    env_state_dir = os.environ.get("ADA2_DWARF_STATE_DIR")
    if env_state_dir:
        return Path(env_state_dir)
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir).parent.parent / "state"
    return None


def infer_target_run_id(explicit_target_run_id: str | None) -> str | None:
    if explicit_target_run_id:
        return explicit_target_run_id
    return None


def _default_output_dir() -> Path:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir) / "outputs" / "replay"
    return Path.cwd() / "outputs" / "replay"


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


def normalize_for_compare(value, *, original_run_dir: Path, replay_run_dir: Path):
    return generic_normalize_for_compare(
        value,
        left_run_dir=original_run_dir,
        right_run_dir=replay_run_dir,
    )


def compare_relpath(*, original_run_dir: Path, replay_run_dir: Path, relpath: str) -> dict:
    result = generic_compare_relpath(
        left_run_dir=original_run_dir,
        right_run_dir=replay_run_dir,
        relpath=relpath,
    )
    if "left_exists" in result:
        result["original_exists"] = result.pop("left_exists")
    if "right_exists" in result:
        result["replay_exists"] = result.pop("right_exists")
    if "left_sha256" in result:
        result["original_sha256"] = result.pop("left_sha256")
    if "right_sha256" in result:
        result["replay_sha256"] = result.pop("right_sha256")
    if "left_normalized" in result:
        result["original_normalized"] = result.pop("left_normalized")
    if "right_normalized" in result:
        result["replay_normalized"] = result.pop("right_normalized")
    if result.get("verdict") == "missing_in_left":
        result["reason"] = "missing_original_artifact"
    elif result.get("verdict") == "missing_in_right":
        result["reason"] = "missing_replay_artifact"
    elif result.get("verdict") == "missing_in_both":
        result["reason"] = "missing_artifact"
    return result


def run_bundle_replay(
    *,
    runs_dir: Path,
    state_dir: Path,
    registry_path: Path | None,
    output_dir: Path,
    target_run_id: str,
    compare_relpaths: list[str],
) -> dict:
    original_run_dir = runs_dir / target_run_id
    replay_handle = scenario_module.replay_run(
        target_run_id,
        runs_dir=runs_dir,
        state_dir=state_dir,
        registry_path=registry_path,
    )
    replay_run_dir = replay_handle.run_dir
    comparisons = [
        compare_relpath(
            original_run_dir=original_run_dir,
            replay_run_dir=replay_run_dir,
            relpath=relpath,
        )
        for relpath in compare_relpaths
    ]
    comparison_verdict = "match" if all(item["verdict"] == "match" for item in comparisons) else "diff"
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "result.json"
    payload = {
        "schema_version": "v1",
        "target_run_id": target_run_id,
        "replay_run_id": replay_handle.run_id,
        "original_run_dir": str(original_run_dir),
        "replay_run_dir": str(replay_run_dir),
        "compared_relpaths": compare_relpaths,
        "comparison_verdict": comparison_verdict,
        "comparisons": comparisons,
        "replayed_at_utc": utc_timestamp(),
        "comparison_relpath": _relative_artifact_path(replay_run_dir / "replay-comparison.md"),
    }
    result_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["result_relpath"] = _relative_artifact_path(result_path)
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Replay a captured bundle and compare selected artifacts")
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--registry-path", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--target-run-id", required=True)
    parser.add_argument("--compare-relpath", action="append", dest="compare_relpaths", required=True)
    args = parser.parse_args(argv[1:])

    runs_dir = infer_runs_dir(args.runs_dir)
    state_dir = infer_state_dir(args.state_dir)
    registry_path = Path(args.registry_path) if args.registry_path else None
    if runs_dir is None:
        print("missing runs dir", file=sys.stderr)
        return 1
    if state_dir is None:
        print("missing state dir", file=sys.stderr)
        return 1
    target_run_id = infer_target_run_id(args.target_run_id)
    if not target_run_id:
        print("missing target run id", file=sys.stderr)
        return 1
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()

    emit_target_event(
        primitive="runtime_bundle_replay",
        event="bundle_replay_started",
        payload={
            "runs_dir": str(runs_dir),
            "state_dir": str(state_dir),
            "target_run_id": target_run_id,
            "compare_relpaths": args.compare_relpaths,
            "output_dir": str(output_dir),
        },
    )
    result = run_bundle_replay(
        runs_dir=runs_dir,
        state_dir=state_dir,
        registry_path=registry_path,
        output_dir=output_dir,
        target_run_id=target_run_id,
        compare_relpaths=list(args.compare_relpaths),
    )
    emit_target_event(
        primitive="runtime_bundle_replay",
        event="bundle_replay_completed",
        payload=result,
    )
    print(
        "target_run_id={target_run_id} replay_run_id={replay_run_id} comparison_verdict={comparison_verdict} result_relpath={result_relpath}".format(
            target_run_id=result["target_run_id"],
            replay_run_id=result["replay_run_id"],
            comparison_verdict=result["comparison_verdict"],
            result_relpath=result["result_relpath"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
