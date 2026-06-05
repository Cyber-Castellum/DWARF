#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import jsonschema


SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_telemetry import emit_target_event  # noqa: E402


SARIF_SCHEMA_URI = "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
SARIF_SCHEMA_PATH = DWARF_ROOT / "spec" / "sarif-schema-2.1.0.json"
DWARF_VERSION = "0.1.0"
DWARF_INFORMATION_URI = "https://github.com/GainSec/dwarf"


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
        return Path(run_dir) / "outputs" / "sarif-export"
    return Path.cwd() / "outputs" / "sarif-export"


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


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _tool_component() -> dict:
    return {
        "name": "Dwarf",
        "version": DWARF_VERSION,
        "informationUri": DWARF_INFORMATION_URI,
    }


def _base_run(*, tool_name: str, target_run_id: str, bundle_dir: Path, manifest: dict) -> dict:
    return {
        "tool": {"driver": _tool_component() | {"name": tool_name}},
        "automationDetails": {"id": target_run_id},
        "results": [],
        "properties": {
            "dwarf.target_run_id": target_run_id,
            "dwarf.bundle_dir": str(bundle_dir),
            "dwarf.scenario_id": manifest.get("scenario_id"),
            "dwarf.exit_status": manifest.get("exit_status"),
        },
    }


def _artifact_location(bundle_dir: Path, relpath: str) -> dict:
    return {"uri": str(bundle_dir / relpath)}


def _bundle_diff_run(*, target_run_id: str, bundle_dir: Path, manifest: dict, diff_body: dict) -> dict:
    run = _base_run(tool_name="Dwarf bundle diff", target_run_id=target_run_id, bundle_dir=bundle_dir, manifest=manifest)
    for comparison in diff_body.get("comparisons") or []:
        verdict = comparison.get("verdict")
        if verdict == "match":
            continue
        relpath = comparison.get("relpath", "<unknown>")
        message = f"Bundle comparison {verdict} for {relpath}"
        result = {
            "ruleId": f"dwarf.bundle-diff.{verdict}",
            "level": "error" if verdict == "diff" else "warning",
            "message": {"text": message},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": _artifact_location(bundle_dir, relpath),
                    }
                }
            ],
            "properties": {
                "dwarf.relpath": relpath,
                "dwarf.verdict": verdict,
                "dwarf.left_sha256": comparison.get("left_sha256"),
                "dwarf.right_sha256": comparison.get("right_sha256"),
            },
        }
        run["results"].append(result)
    return run


def _bundle_replay_run(*, target_run_id: str, bundle_dir: Path, manifest: dict, replay_body: dict) -> dict:
    run = _base_run(tool_name="Dwarf bundle replay", target_run_id=target_run_id, bundle_dir=bundle_dir, manifest=manifest)
    if replay_body.get("comparison_verdict") == "diff":
        for comparison in replay_body.get("comparisons") or []:
            if comparison.get("verdict") == "match":
                continue
            relpath = comparison.get("relpath", "<unknown>")
            result = {
                "ruleId": f"dwarf.bundle-replay.{comparison.get('verdict', 'diff')}",
                "level": "error",
                "message": {"text": f"Replay diverged for {relpath}"},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": _artifact_location(bundle_dir, relpath),
                        }
                    }
                ],
                "properties": {
                    "dwarf.relpath": relpath,
                    "dwarf.verdict": comparison.get("verdict"),
                    "dwarf.target_run_id": replay_body.get("target_run_id"),
                    "dwarf.replay_run_id": replay_body.get("replay_run_id"),
                },
            }
            run["results"].append(result)
    return run


def build_sarif_log(*, bundle_dir: Path, target_run_id: str) -> dict:
    manifest_path = bundle_dir / "manifest.json"
    manifest = _load_json(manifest_path) if manifest_path.is_file() else {}
    runs = []

    diff_path = bundle_dir / "outputs" / "bundle-diff" / "diff.json"
    if diff_path.is_file():
        runs.append(_bundle_diff_run(
            target_run_id=target_run_id,
            bundle_dir=bundle_dir,
            manifest=manifest,
            diff_body=_load_json(diff_path),
        ))

    replay_path = bundle_dir / "outputs" / "replay" / "result.json"
    if replay_path.is_file():
        runs.append(_bundle_replay_run(
            target_run_id=target_run_id,
            bundle_dir=bundle_dir,
            manifest=manifest,
            replay_body=_load_json(replay_path),
        ))

    if not runs:
        runs.append(_base_run(tool_name="Dwarf bundle export", target_run_id=target_run_id, bundle_dir=bundle_dir, manifest=manifest))

    return {
        "$schema": SARIF_SCHEMA_URI,
        "version": "2.1.0",
        "runs": runs,
    }


def validate_sarif(sarif_log: dict, *, schema_path: Path = SARIF_SCHEMA_PATH) -> tuple[bool, str | None]:
    schema = _load_json(schema_path)
    try:
        jsonschema.validate(instance=sarif_log, schema=schema)
    except jsonschema.ValidationError as exc:
        return False, exc.message
    return True, None


def _count_results(sarif_log: dict) -> int:
    return sum(len(run.get("results") or []) for run in sarif_log.get("runs") or [])


def run_sarif_export(*, runs_dir: Path, output_dir: Path, target_run_id: str, schema_path: Path = SARIF_SCHEMA_PATH) -> dict:
    bundle_dir = runs_dir / target_run_id
    sarif_log = build_sarif_log(bundle_dir=bundle_dir, target_run_id=target_run_id)
    schema_valid, validation_error = validate_sarif(sarif_log, schema_path=schema_path)
    result_count = _count_results(sarif_log)

    output_dir.mkdir(parents=True, exist_ok=True)
    sarif_path = output_dir / "dwarf-export.sarif"
    sarif_path.write_text(json.dumps(sarif_log, indent=2) + "\n", encoding="utf-8")
    result_path = output_dir / "result.json"
    payload = {
        "schema_version": "v1",
        "target_run_id": target_run_id,
        "exported_at_utc": utc_timestamp(),
        "schema_path": str(schema_path),
        "schema_valid": schema_valid,
        "validation_error": validation_error,
        "sarif_result_count": result_count,
        "sarif_run_count": len(sarif_log.get("runs") or []),
        "mapped_evidence_types": [
            name for name, path in (
                ("bundle-diff", bundle_dir / "outputs" / "bundle-diff" / "diff.json"),
                ("bundle-replay", bundle_dir / "outputs" / "replay" / "result.json"),
            ) if path.is_file()
        ],
        "sarif_relpath": _relative_artifact_path(sarif_path),
    }
    result_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["result_relpath"] = _relative_artifact_path(result_path)
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Export a captured bundle into SARIF v2.1.0")
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--target-run-id", required=True)
    parser.add_argument("--schema-path", default=str(SARIF_SCHEMA_PATH))
    args = parser.parse_args(argv[1:])

    runs_dir = infer_runs_dir(args.runs_dir)
    if runs_dir is None:
        print("missing runs dir", file=sys.stderr)
        return 1
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()
    schema_path = Path(args.schema_path)

    emit_target_event(
        primitive="runtime_bundle_export_sarif",
        event="bundle_export_sarif_started",
        payload={
            "runs_dir": str(runs_dir),
            "target_run_id": args.target_run_id,
            "schema_path": str(schema_path),
            "output_dir": str(output_dir),
        },
    )
    result = run_sarif_export(
        runs_dir=runs_dir,
        output_dir=output_dir,
        target_run_id=args.target_run_id,
        schema_path=schema_path,
    )
    emit_target_event(
        primitive="runtime_bundle_export_sarif",
        event="bundle_export_sarif_completed",
        payload=result,
    )
    print(
        "target_run_id={target_run_id} schema_valid={schema_valid} sarif_result_count={sarif_result_count} sarif_relpath={sarif_relpath}".format(
            target_run_id=result["target_run_id"],
            schema_valid=str(result["schema_valid"]).lower(),
            sarif_result_count=result["sarif_result_count"],
            sarif_relpath=result["sarif_relpath"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
