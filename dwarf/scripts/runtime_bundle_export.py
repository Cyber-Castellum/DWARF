#!/usr/bin/env python3

import argparse
import json
import os
import shutil
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import runtime_bundle_sign  # noqa: E402
from runtime_telemetry import emit_target_event  # noqa: E402


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def infer_target_run_id(explicit_target_run_id: str | None) -> str | None:
    return runtime_bundle_sign.infer_target_run_id(explicit_target_run_id)


def infer_run_dir() -> Path | None:
    return runtime_bundle_sign.infer_run_dir()


def _default_output_dir() -> Path:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir) / "outputs" / "export"
    return Path.cwd() / "outputs" / "export"


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


def _build_readme(target_run_id: str) -> str:
    return "\n".join(
        [
            "Dwarf Bundle Export",
            "",
            f"This archive contains the run bundle directory '{target_run_id}/'.",
            "",
            "What is included:",
            "- The exported run bundle directory with its existing artifacts",
            "- The in-bundle promotion, dedupe, and signature records when present",
            "",
            "How to verify the extracted bundle with an unmodified CLI:",
            f"1. Extract this tarball into a parent directory.",
            f"2. Run: cardano-profile bundle verify {target_run_id} --runs-dir <extracted-parent>/runs-or-parent",
            "",
            "How to verify the export tarball itself:",
            "- Use the sibling export signature artifact produced next to this tarball.",
            "- The forthcoming bundle import surface will automate that check.",
            "",
        ]
    ) + "\n"


def _tarball_filename(target_run_id: str) -> str:
    return f"{target_run_id}-bundle-export.tar.gz"


def _signature_filename() -> str:
    return "signature.json"


def _add_run_bundle_to_tar(handle: tarfile.TarFile, *, run_dir: Path, bundle_root_name: str):
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        relpath = path.relative_to(run_dir)
        if str(relpath).startswith("outputs/export/"):
            continue
        handle.add(path, arcname=str(Path(bundle_root_name) / relpath), recursive=False)


def verify_export_signature(export_dir: Path) -> dict:
    signature_path = export_dir / _signature_filename()
    tarballs = sorted(export_dir.glob("*-bundle-export.tar.gz"))
    if not tarballs:
        raise RuntimeError(f"missing export tarball under {export_dir}")
    tarball_path = tarballs[0]
    with tempfile.TemporaryDirectory() as tmp:
        verify_dir = Path(tmp) / "verify"
        verify_dir.mkdir(parents=True, exist_ok=True)
        staged_tarball = verify_dir / tarball_path.name
        shutil.copy2(tarball_path, staged_tarball)
        return runtime_bundle_sign.verify_signature(run_dir=verify_dir, signature_path=signature_path)


def run_export(
    *,
    run_dir: Path,
    output_dir: Path,
    target_run_id: str,
    signing_actor: str,
    key_path: Path | None,
) -> dict:
    bundle_root_name = target_run_id
    tarball_name = _tarball_filename(target_run_id)

    with tempfile.TemporaryDirectory() as tmp:
        temp_root = Path(tmp)
        tarball_path = temp_root / tarball_name
        readme_path = temp_root / "README-export.txt"
        readme_path.write_text(_build_readme(target_run_id), encoding="utf-8")

        with tarfile.open(tarball_path, "w:gz") as handle:
            handle.add(readme_path, arcname="README-export.txt", recursive=False)
            _add_run_bundle_to_tar(handle, run_dir=run_dir, bundle_root_name=bundle_root_name)

        tarball_sha256 = runtime_bundle_sign._sha256_file(tarball_path)
        tarball_size_bytes = tarball_path.stat().st_size

        sign_dir = temp_root / "signing-input"
        sign_dir.mkdir(parents=True, exist_ok=True)
        staged_tarball = sign_dir / tarball_name
        shutil.copy2(tarball_path, staged_tarball)
        signature = runtime_bundle_sign.run_signature(
            run_dir=sign_dir,
            output_dir=sign_dir / "outputs" / "signature",
            target_run_id=target_run_id,
            signing_actor=signing_actor,
            key_path=key_path,
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        final_tarball = output_dir / tarball_name
        final_signature = output_dir / _signature_filename()
        shutil.copy2(tarball_path, final_tarball)
        shutil.copy2(sign_dir / "outputs" / "signature" / "signature.json", final_signature)

        artifact_path = output_dir / "result.json"
        payload = {
            "schema_version": "v1",
            "target_run_id": target_run_id,
            "exported_at_utc": utc_timestamp(),
            "signing_actor": signing_actor,
            "bundle_root_name": bundle_root_name,
            "readme_entry_name": "README-export.txt",
            "tarball_filename": tarball_name,
            "tarball_sha256": tarball_sha256,
            "tarball_size_bytes": tarball_size_bytes,
            "tarball_relpath": _relative_artifact_path(final_tarball),
            "signature_relpath": _relative_artifact_path(final_signature),
            "manifest_sha256": signature.get("manifest_sha256"),
            "key_source": signature.get("key_source"),
            "operator_warning": signature.get("operator_warning"),
            "signing_unavailable": signature.get("signing_unavailable"),
        }
        artifact_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        payload["result_relpath"] = _relative_artifact_path(artifact_path)
        return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Package a run bundle into a signed export tarball")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--target-run-id", default=None)
    parser.add_argument("--key-path", default=os.environ.get("ADA2_DWARF_BUNDLE_SIGNING_KEY"))
    parser.add_argument("--signing-actor", default=os.environ.get("USER", "operator"))
    args = parser.parse_args(argv[1:])

    run_dir = infer_run_dir()
    if run_dir is None:
        print("missing run dir", file=sys.stderr)
        return 1
    target_run_id = infer_target_run_id(args.target_run_id)
    if not target_run_id:
        print("missing target run id", file=sys.stderr)
        return 1
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()
    key_path = Path(args.key_path) if args.key_path else None

    emit_target_event(
        primitive="runtime_bundle_export",
        event="bundle_export_started",
        payload={
            "target_run_id": target_run_id,
            "signing_actor": args.signing_actor,
            "key_path": str(key_path) if key_path else None,
            "output_dir": str(output_dir),
        },
    )

    result = run_export(
        run_dir=run_dir,
        output_dir=output_dir,
        target_run_id=target_run_id,
        signing_actor=args.signing_actor,
        key_path=key_path,
    )

    emit_target_event(
        primitive="runtime_bundle_export",
        event="bundle_export_completed",
        payload=result,
    )
    print(
        "target_run_id={target_run_id} signing_actor={signing_actor} manifest_sha256={manifest_sha256} "
        "tarball_relpath={tarball_relpath} signature_relpath={signature_relpath}".format(
            target_run_id=result["target_run_id"],
            signing_actor=result["signing_actor"],
            manifest_sha256=result["manifest_sha256"],
            tarball_relpath=result["tarball_relpath"],
            signature_relpath=result["signature_relpath"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
