#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_telemetry import emit_target_event  # noqa: E402


DEV_PRIVATE_KEY_HEX = "1f8b7d4c2a3e9b5d6c7a8f1029384756abcdef1234567890fedcba0987654321"
DEFAULT_EXCLUDED_RELPATHS = {
    "assertions.json",
    "chain.json",
    "events/observer.ndjson",
    "events/target.ndjson",
    "log.ndjson",
    "manifest.json",
    "metrics/host/load.ndjson",
    "metrics/process/self.ndjson",
    "metrics/summary.json",
    "events/target-hooks.ndjson",
    "outputs/signature/signature.json",
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def infer_target_run_id(explicit_target_run_id: str | None) -> str | None:
    if explicit_target_run_id:
        return explicit_target_run_id
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir).name
    return None


def infer_run_dir(explicit_run_dir: str | None = None) -> Path | None:
    if explicit_run_dir:
        return Path(explicit_run_dir)
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    return Path(run_dir) if run_dir else None


def _default_output_dir(run_dir: Path | None) -> Path:
    if run_dir is not None:
        return run_dir / "outputs" / "signature"
    return Path.cwd() / "outputs" / "signature"


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_crypto():
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

    return InvalidSignature, serialization, Ed25519PrivateKey, Ed25519PublicKey


def _load_signing_private_key(key_path: Path | None):
    _, serialization, Ed25519PrivateKey, _ = _load_crypto()
    if key_path is None:
        return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(DEV_PRIVATE_KEY_HEX)), "development-embedded-key", True
    private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    return private_key, str(key_path), False


def build_manifest(run_dir: Path, extra_excluded_relpaths: set[str] | None = None) -> dict[str, str]:
    excluded = set(DEFAULT_EXCLUDED_RELPATHS)
    if extra_excluded_relpaths:
        excluded.update(extra_excluded_relpaths)
    manifest = {}
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        relpath = str(path.relative_to(run_dir))
        if relpath in excluded:
            continue
        manifest[relpath] = _sha256_file(path)
    return manifest


def canonical_manifest_bytes(manifest: dict[str, str]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_signature(*, run_dir: Path, signature_path: Path) -> dict:
    payload = json.loads(signature_path.read_text(encoding="utf-8"))
    if payload.get("signing_unavailable"):
        return {
            "verdict": "unsigned",
            "manifest_sha256_recomputed": None,
            "manifest_sha256_signed": payload.get("manifest_sha256"),
            "reason": payload.get("reason"),
        }
    InvalidSignature, serialization, _, Ed25519PublicKey = _load_crypto()
    extra_excluded = set(payload.get("excluded_relpaths") or [])
    manifest = build_manifest(run_dir, extra_excluded_relpaths=extra_excluded)
    manifest_bytes = canonical_manifest_bytes(manifest)
    manifest_sha256_recomputed = hashlib.sha256(manifest_bytes).hexdigest()
    public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(payload["public_key_hex"]))
    try:
        public_key.verify(bytes.fromhex(payload["signature_hex"]), manifest_bytes)
        signature_ok = True
    except InvalidSignature:
        signature_ok = False
    verdict = "verified" if signature_ok and manifest_sha256_recomputed == payload["manifest_sha256"] else "tampered"
    return {
        "verdict": verdict,
        "manifest_sha256_recomputed": manifest_sha256_recomputed,
        "manifest_sha256_signed": payload["manifest_sha256"],
    }


def run_signature(
    *,
    run_dir: Path,
    output_dir: Path,
    target_run_id: str,
    signing_actor: str,
    key_path: Path | None,
    extra_excluded_relpaths: set[str] | None = None,
) -> dict:
    artifact_path = output_dir / "signature.json"
    extra_excluded = set()
    if key_path is not None:
        try:
            rel_key = str(key_path.resolve().relative_to(run_dir.resolve()))
            extra_excluded.add(rel_key)
        except ValueError:
            pass
    if extra_excluded_relpaths:
        extra_excluded.update(extra_excluded_relpaths)
    manifest = build_manifest(run_dir, extra_excluded_relpaths=extra_excluded)
    manifest_bytes = canonical_manifest_bytes(manifest)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema_version": "v1",
        "target_run_id": target_run_id,
        "signed_at_utc": utc_timestamp(),
        "signing_actor": signing_actor,
        "excluded_relpaths": sorted(DEFAULT_EXCLUDED_RELPATHS | extra_excluded),
        "manifest_entry_count": len(manifest),
    }

    try:
        _, serialization, _, _ = _load_crypto()
        private_key, key_source, operator_warning = _load_signing_private_key(key_path)
        public_key = private_key.public_key()
        public_key_hex = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()
        signature_hex = private_key.sign(manifest_bytes).hex()
        payload.update(
            {
                "public_key_hex": public_key_hex,
                "manifest_sha256": manifest_sha256,
                "signature_hex": signature_hex,
                "key_source": key_source,
                "operator_warning": operator_warning,
                "signing_unavailable": False,
            }
        )
    except ImportError:
        payload.update(
            {
                "public_key_hex": None,
                "manifest_sha256": manifest_sha256,
                "signature_hex": None,
                "key_source": None,
                "operator_warning": True,
                "signing_unavailable": True,
                "reason": "missing_dependency",
            }
        )

    artifact_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["signature_relpath"] = _relative_artifact_path(artifact_path)
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Write an Ed25519 signature record for the current run bundle")
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
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir(run_dir)
    key_path = Path(args.key_path) if args.key_path else None

    emit_target_event(
        primitive="runtime_bundle_sign",
        event="bundle_sign_started",
        payload={
            "target_run_id": target_run_id,
            "signing_actor": args.signing_actor,
            "key_path": str(key_path) if key_path else None,
            "output_dir": str(output_dir),
        },
    )

    result = run_signature(
        run_dir=run_dir,
        output_dir=output_dir,
        target_run_id=target_run_id,
        signing_actor=args.signing_actor,
        key_path=key_path,
    )

    emit_target_event(
        primitive="runtime_bundle_sign",
        event="bundle_sign_completed",
        payload=result,
    )
    print(
        "target_run_id={target_run_id} signing_actor={signing_actor} manifest_sha256={manifest_sha256} "
        "signing_unavailable={signing_unavailable} operator_warning={operator_warning} key_source={key_source} "
        "signature_relpath={signature_relpath}".format(
            target_run_id=result["target_run_id"],
            signing_actor=result["signing_actor"],
            manifest_sha256=result["manifest_sha256"],
            signing_unavailable=str(result["signing_unavailable"]).lower(),
            operator_warning=str(result["operator_warning"]).lower(),
            key_source=result["key_source"] or "none",
            signature_relpath=result["signature_relpath"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
