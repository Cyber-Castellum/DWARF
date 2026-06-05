#!/usr/bin/env python3

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_telemetry import emit_target_event  # noqa: E402


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def infer_target_run_id(explicit_target_run_id: str | None) -> str | None:
    if explicit_target_run_id:
        return explicit_target_run_id
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir).name
    return None


def _default_output_dir() -> Path:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir) / "outputs" / "promotion"
    return Path.cwd() / "outputs" / "promotion"


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


def run_promotion(
    *,
    output_dir: Path,
    target_run_id: str,
    reason_code: str,
    reason_text: str,
    operator_notes: str,
    actor: str,
    source_surface: str,
    promotion_run_id: str | None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / "promotion.json"
    payload = {
        "schema_version": "v1",
        "target_run_id": target_run_id,
        "promotion_run_id": promotion_run_id,
        "promotion_timestamp": utc_timestamp(),
        "reason": {
            "code": reason_code,
            "text": reason_text,
        },
        "operator_notes": operator_notes,
        "actor": actor,
        "source_surface": source_surface,
    }
    artifact_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["promotion_relpath"] = _relative_artifact_path(artifact_path)
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Write a structured promotion record into a run bundle")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--target-run-id", default=None)
    parser.add_argument("--reason-code", required=True)
    parser.add_argument("--reason-text", required=True)
    parser.add_argument("--operator-notes", default="")
    parser.add_argument("--actor", default=os.environ.get("USER", "operator"))
    parser.add_argument("--source-surface", default=None)
    args = parser.parse_args(argv[1:])

    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()
    target_run_id = infer_target_run_id(args.target_run_id)
    if not target_run_id:
        print("missing target run id", file=sys.stderr)
        return 1
    current_run_id = infer_target_run_id(None)
    source_surface = args.source_surface or ("scenario-primitive" if current_run_id else "cli")

    emit_target_event(
        primitive="runtime_bundle_promote",
        event="bundle_promotion_started",
        payload={
            "target_run_id": target_run_id,
            "reason_code": args.reason_code,
            "actor": args.actor,
            "source_surface": source_surface,
            "output_dir": str(output_dir),
        },
    )

    result = run_promotion(
        output_dir=output_dir,
        target_run_id=target_run_id,
        reason_code=args.reason_code,
        reason_text=args.reason_text,
        operator_notes=args.operator_notes,
        actor=args.actor,
        source_surface=source_surface,
        promotion_run_id=current_run_id,
    )

    emit_target_event(
        primitive="runtime_bundle_promote",
        event="bundle_promotion_completed",
        payload=result,
    )
    print(
        "target_run_id={target_run_id} reason_code={reason_code} actor={actor} "
        "source_surface={source_surface} promotion_timestamp={promotion_timestamp} "
        "promotion_relpath={promotion_relpath}".format(
            target_run_id=result["target_run_id"],
            reason_code=result["reason"]["code"],
            actor=result["actor"],
            source_surface=result["source_surface"],
            promotion_timestamp=result["promotion_timestamp"],
            promotion_relpath=result["promotion_relpath"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
