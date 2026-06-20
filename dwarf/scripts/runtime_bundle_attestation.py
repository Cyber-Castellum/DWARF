#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import runtime_bundle_sign  # noqa: E402
from runtime_telemetry import emit_target_event  # noqa: E402


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def infer_run_dir(explicit_run_dir: str | None = None) -> Path | None:
    return runtime_bundle_sign.infer_run_dir(explicit_run_dir)


def infer_target_run_id(explicit_target_run_id: str | None) -> str | None:
    return runtime_bundle_sign.infer_target_run_id(explicit_target_run_id)


def _default_output_dir(run_dir: Path | None) -> Path:
    if run_dir is not None:
        return run_dir / "outputs" / "attestation"
    return Path.cwd() / "outputs" / "attestation"


def _relative_artifact_path(artifact_path: Path) -> str:
    return runtime_bundle_sign._relative_artifact_path(artifact_path)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return runtime_bundle_sign._sha256_file(path)


def _canonical_json_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash_tree(paths: list[Path]) -> str:
    manifest = {}
    for root in paths:
        if not root.exists():
            continue
        if root.is_file():
            manifest[str(root.relative_to(DWARF_ROOT))] = _sha256_file(root)
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file():
                manifest[str(path.relative_to(DWARF_ROOT))] = _sha256_file(path)
    return _sha256_bytes(_canonical_json_bytes(manifest))


def _parse_profile(run_dir: Path) -> tuple[str | None, str | None]:
    path = run_dir / "resolved-profile.json"
    if not path.is_file():
        return None, None
    raw = path.read_bytes()
    try:
        body = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None, _sha256_bytes(raw)
    if body is None:
        return None, None
    profile_id = body.get("id") or body.get("profile_id")
    return profile_id, _sha256_bytes(raw)


def _dashboard_config_sha256() -> str | None:
    path = DWARF_ROOT / "profile_manager" / "data" / "config.py"
    return _sha256_file(path) if path.is_file() else None


def _run_version_command(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError:
        return None
    text = (completed.stdout or completed.stderr or "").strip()
    if not text:
        return None
    return text.splitlines()[0]


def collect_tooling_versions(*, dwarf_source_sha256: str | None = None) -> dict[str, str | None]:
    amaru_candidates = [
        os.environ.get("ADA2_DWARF_AMARU_BIN"),
        "${HOME}/amaru-verification/target/debug/amaru",
        "amaru",
    ]
    amaru_version = None
    for candidate in amaru_candidates:
        if not candidate:
            continue
        command = [candidate, "--version"]
        version = _run_version_command(command)
        if version:
            amaru_version = version
            break
    dwarf_git = _run_version_command(["git", "-C", str(DWARF_ROOT), "rev-parse", "HEAD"])
    if dwarf_git and not dwarf_git.startswith("fatal:"):
        dwarf_version = dwarf_git
    elif dwarf_source_sha256:
        dwarf_version = f"source-sha256:{dwarf_source_sha256}"
    else:
        dwarf_version = dwarf_git
    return {
        "cardano-cli": _run_version_command(["bash", "-lc", "cardano-cli --version"]),
        "cardano-node": _run_version_command(["bash", "-lc", "cardano-node --version"]),
        "amaru": amaru_version,
        "dwarf": dwarf_version,
    }


def build_attestation_statement(*, run_dir: Path, tooling_versions_override: dict[str, str | None] | None = None) -> dict:
    scenario_path = run_dir / "scenario.yaml"
    scenario_bytes = scenario_path.read_bytes()
    active_profile_id, active_profile_sha256 = _parse_profile(run_dir)
    dwarf_source_sha256 = _hash_tree(
        [
            DWARF_ROOT / "scripts",
            DWARF_ROOT / "profile_manager",
        ]
    )
    tooling_versions = tooling_versions_override or collect_tooling_versions(dwarf_source_sha256=dwarf_source_sha256)
    return {
        "scenario_path": "scenario.yaml",
        "scenario_yaml_sha256": _sha256_bytes(scenario_bytes),
        "active_profile_id": active_profile_id,
        "active_profile_sha256": active_profile_sha256,
        "dashboard_config_sha256": _dashboard_config_sha256(),
        "dwarf_source_sha256": dwarf_source_sha256,
        "tooling_versions": tooling_versions,
    }


def _signable_payload(*, target_run_id: str, attested_at_utc: str, statement: dict) -> dict:
    return {
        "schema_version": "v1",
        "target_run_id": target_run_id,
        "attested_at_utc": attested_at_utc,
        "statement": statement,
    }


def verify_attestation(path: Path) -> dict:
    body = json.loads(path.read_text(encoding="utf-8"))
    signature = body.get("signature") or {}
    if signature.get("signing_unavailable"):
        return {"verdict": "unsigned", "signing_key_fingerprint_matches": False}
    _, serialization, _, Ed25519PublicKey = runtime_bundle_sign._load_crypto()
    public_key_bytes = bytes.fromhex(signature["public_key_hex"])
    expected_fingerprint = hashlib.sha256(public_key_bytes).hexdigest()
    fingerprint_matches = expected_fingerprint == signature.get("signing_key_fingerprint")
    public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
    signable = _signable_payload(
        target_run_id=body["target_run_id"],
        attested_at_utc=body["attested_at_utc"],
        statement=body["statement"],
    )
    try:
        public_key.verify(bytes.fromhex(signature["signature_hex"]), _canonical_json_bytes(signable))
        signature_ok = True
    except runtime_bundle_sign._load_crypto()[0]:
        signature_ok = False
    verdict = "verified" if signature_ok and fingerprint_matches else "tampered"
    return {
        "verdict": verdict,
        "signing_key_fingerprint_matches": fingerprint_matches,
    }


def run_attestation(
    *,
    run_dir: Path,
    output_dir: Path,
    target_run_id: str,
    signing_actor: str,
    key_path: Path | None,
    tooling_versions_override: dict[str, str | None] | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    attested_at_utc = utc_timestamp()
    statement = build_attestation_statement(run_dir=run_dir, tooling_versions_override=tooling_versions_override)
    signable = _signable_payload(
        target_run_id=target_run_id,
        attested_at_utc=attested_at_utc,
        statement=statement,
    )

    payload = {
        "schema_version": "v1",
        "target_run_id": target_run_id,
        "attested_at_utc": attested_at_utc,
        "statement": statement,
    }

    try:
        _, serialization, _, _ = runtime_bundle_sign._load_crypto()
        private_key, key_source, operator_warning = runtime_bundle_sign._load_signing_private_key(key_path)
        public_key = private_key.public_key()
        public_key_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        signature_hex = private_key.sign(_canonical_json_bytes(signable)).hex()
        payload["signature"] = {
            "algorithm": "ed25519",
            "signing_actor": signing_actor,
            "key_source": key_source,
            "operator_warning": operator_warning,
            "signing_unavailable": False,
            "public_key_hex": public_key_bytes.hex(),
            "signing_key_fingerprint": hashlib.sha256(public_key_bytes).hexdigest(),
            "signature_hex": signature_hex,
        }
    except ImportError:
        payload["signature"] = {
            "algorithm": "ed25519",
            "signing_actor": signing_actor,
            "key_source": None,
            "operator_warning": True,
            "signing_unavailable": True,
            "public_key_hex": None,
            "signing_key_fingerprint": None,
            "signature_hex": None,
        }

    artifact_path = output_dir / "attestation.json"
    artifact_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    verification = verify_attestation(artifact_path)
    payload["verification"] = verification
    artifact_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["attestation_relpath"] = _relative_artifact_path(artifact_path)
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Write a signed provenance attestation for the current run bundle")
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
        primitive="runtime_bundle_attestation",
        event="bundle_attestation_started",
        payload={
            "target_run_id": target_run_id,
            "signing_actor": args.signing_actor,
            "key_path": str(key_path) if key_path else None,
            "output_dir": str(output_dir),
        },
    )
    result = run_attestation(
        run_dir=run_dir,
        output_dir=output_dir,
        target_run_id=target_run_id,
        signing_actor=args.signing_actor,
        key_path=key_path,
    )
    emit_target_event(
        primitive="runtime_bundle_attestation",
        event="bundle_attestation_completed",
        payload=result,
    )
    print(
        "target_run_id={target_run_id} active_profile_id={active_profile_id} verification_verdict={verification_verdict} "
        "attestation_relpath={attestation_relpath}".format(
            target_run_id=result["target_run_id"],
            active_profile_id=result["statement"]["active_profile_id"] or "none",
            verification_verdict=result["verification"]["verdict"],
            attestation_relpath=result["attestation_relpath"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
