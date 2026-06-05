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

import runtime_bundle_dedupe  # noqa: E402
import runtime_bundle_promote  # noqa: E402
import runtime_bundle_sign  # noqa: E402
from runtime_telemetry import emit_target_event  # noqa: E402


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def infer_target_run_id(explicit_target_run_id: str | None) -> str | None:
    return runtime_bundle_promote.infer_target_run_id(explicit_target_run_id)


def infer_runs_dir(explicit_runs_dir: str | None) -> Path | None:
    return runtime_bundle_dedupe.infer_runs_dir(explicit_runs_dir)


def infer_run_dir() -> Path | None:
    return runtime_bundle_sign.infer_run_dir()


def _default_output_dir() -> Path:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir) / "outputs" / "chain"
    return Path.cwd() / "outputs" / "chain"


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


def run_chain(
    *,
    runs_dir: Path,
    run_dir: Path,
    output_dir: Path,
    target_run_id: str,
    reason_code: str,
    reason_text: str,
    operator_notes: str,
    actor: str,
    source_surface: str,
    signature_primitive: str | None,
    chain_run_id: str | None,
    key_path: Path | None,
    signing_actor: str | None = None,
) -> dict:
    promotion = runtime_bundle_promote.run_promotion(
        output_dir=run_dir / "outputs" / "promotion",
        target_run_id=target_run_id,
        reason_code=reason_code,
        reason_text=reason_text,
        operator_notes=operator_notes,
        actor=actor,
        source_surface=source_surface,
        promotion_run_id=chain_run_id,
    )
    dedupe_signature = {
        "primitive": "runtime_bundle_promote",
        "helper_exit_code": 0,
        "stderr_tail_sha256": runtime_bundle_dedupe._stderr_tail_sha256(""),
        "stderr_tail_line_count": 0,
    }
    dedupe = runtime_bundle_dedupe.compare_signature(
        runs_dir=runs_dir,
        signature=dedupe_signature,
        signature_primitive=signature_primitive,
        target_run_id=target_run_id,
    )
    dedupe_output_dir = run_dir / "outputs" / "dedupe"
    dedupe_output_dir.mkdir(parents=True, exist_ok=True)
    (dedupe_output_dir / "dedupe.json").write_text(
        json.dumps({"schema_version": "v1", **dedupe}, indent=2) + "\n",
        encoding="utf-8",
    )
    dedupe["dedupe_relpath"] = runtime_bundle_promote._relative_artifact_path(dedupe_output_dir / "dedupe.json")

    signature = runtime_bundle_sign.run_signature(
        run_dir=run_dir,
        output_dir=run_dir / "outputs" / "signature",
        target_run_id=target_run_id,
        signing_actor=signing_actor or actor,
        key_path=key_path,
        extra_excluded_relpaths={"outputs/chain/chain.json"},
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / "chain.json"
    payload = {
        "schema_version": "v1",
        "target_run_id": target_run_id,
        "chain_run_id": chain_run_id,
        "chain_timestamp": utc_timestamp(),
        "reason": {
            "code": reason_code,
            "text": reason_text,
        },
        "operator_notes": operator_notes,
        "actor": actor,
        "source_surface": source_surface,
        "signature": {
            "signed_at_utc": signature.get("signed_at_utc"),
            "signing_actor": signature.get("signing_actor"),
            "manifest_sha256": signature.get("manifest_sha256"),
            "signature_relpath": signature.get("signature_relpath"),
            "operator_warning": signature.get("operator_warning"),
            "key_source": signature.get("key_source"),
            "signing_unavailable": signature.get("signing_unavailable"),
        },
        "promotion": {
            "promotion_run_id": promotion.get("promotion_run_id"),
            "promotion_timestamp": promotion.get("promotion_timestamp"),
            "promotion_relpath": promotion.get("promotion_relpath"),
        },
        "dedupe": {
            "signature_primitive": dedupe.get("signature_primitive"),
            "verdict": dedupe.get("verdict"),
            "matched_run_id": dedupe.get("matched_run_id"),
            "promoted_runs_scanned": dedupe.get("promoted_runs_scanned"),
            "dedupe_relpath": dedupe.get("dedupe_relpath"),
        },
    }
    artifact_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["chain_relpath"] = _relative_artifact_path(artifact_path)
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Sign, promote, and dedupe a target run in one composite chain step")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--target-run-id", default=None)
    parser.add_argument("--reason-code", required=True)
    parser.add_argument("--reason-text", required=True)
    parser.add_argument("--operator-notes", default="")
    parser.add_argument("--actor", default=os.environ.get("USER", "operator"))
    parser.add_argument("--signing-actor", default=None)
    parser.add_argument("--source-surface", default=None)
    parser.add_argument("--signature-primitive", default=None)
    parser.add_argument("--key-path", default=os.environ.get("ADA2_DWARF_BUNDLE_SIGNING_KEY"))
    args = parser.parse_args(argv[1:])

    runs_dir = infer_runs_dir(args.runs_dir)
    if runs_dir is None:
        print("missing runs dir", file=sys.stderr)
        return 1
    run_dir = infer_run_dir()
    if run_dir is None:
        print("missing run dir", file=sys.stderr)
        return 1
    target_run_id = infer_target_run_id(args.target_run_id)
    if not target_run_id:
        print("missing target run id", file=sys.stderr)
        return 1
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()
    current_run_id = infer_target_run_id(None)
    source_surface = args.source_surface or ("scenario-primitive" if current_run_id else "cli")
    key_path = Path(args.key_path) if args.key_path else None

    emit_target_event(
        primitive="runtime_bundle_chain",
        event="bundle_chain_started",
        payload={
            "runs_dir": str(runs_dir),
            "run_dir": str(run_dir),
            "target_run_id": target_run_id,
            "reason_code": args.reason_code,
            "actor": args.actor,
            "signing_actor": args.signing_actor or args.actor,
            "source_surface": source_surface,
            "signature_primitive": args.signature_primitive,
            "output_dir": str(output_dir),
        },
    )

    result = run_chain(
        runs_dir=runs_dir,
        run_dir=run_dir,
        output_dir=output_dir,
        target_run_id=target_run_id,
        reason_code=args.reason_code,
        reason_text=args.reason_text,
        operator_notes=args.operator_notes,
        actor=args.actor,
        source_surface=source_surface,
        signature_primitive=args.signature_primitive,
        chain_run_id=current_run_id,
        key_path=key_path,
        signing_actor=args.signing_actor or args.actor,
    )

    emit_target_event(
        primitive="runtime_bundle_chain",
        event="bundle_chain_completed",
        payload=result,
    )
    print(
        "target_run_id={target_run_id} reason_code={reason_code} signature_primitive={signature_primitive} "
        "verdict={verdict} matched_run_id={matched_run_id} promoted_runs_scanned={promoted_runs_scanned} "
        "chain_relpath={chain_relpath}".format(
            target_run_id=result["target_run_id"],
            reason_code=result["reason"]["code"],
            signature_primitive=result["dedupe"]["signature_primitive"],
            verdict=result["dedupe"]["verdict"],
            matched_run_id=result["dedupe"]["matched_run_id"] or "none",
            promoted_runs_scanned=result["dedupe"]["promoted_runs_scanned"],
            chain_relpath=result["chain_relpath"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
