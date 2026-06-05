#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
if str(DWARF_ROOT) not in sys.path:
    sys.path.insert(0, str(DWARF_ROOT))

from profile_manager import forensic  # noqa: E402


def _recompute_manifest_hash(manifest_path: Path) -> str:
    manifest_obj = json.loads(manifest_path.read_text(encoding="utf-8"))
    return forensic._sha256_hex(forensic._canonical_json(manifest_obj))


def _collision_safe_run_id(runs_dir: Path, requested: str) -> str:
    if not (runs_dir / requested).exists():
        return requested
    suffix = 1
    while True:
        candidate = f"{requested}-imported-{suffix}"
        if not (runs_dir / candidate).exists():
            return candidate
        suffix += 1


def import_bundle(*, tarball_path: Path, runs_dir: Path) -> dict:
    runs_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        extract_root = Path(tmpdir) / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tarball_path, "r:gz") as handle:
            handle.extractall(extract_root)
        bundle_roots = sorted(path for path in extract_root.iterdir() if path.is_dir())
        if not bundle_roots:
            raise ValueError("archive does not contain a bundle root directory")
        bundle_root = bundle_roots[0]
        manifest_path = bundle_root / "manifest.json"
        chain_path = bundle_root / "chain.json"
        if not manifest_path.exists() or not chain_path.exists():
            raise ValueError("bundle is missing manifest.json or chain.json")
        chain_entry = json.loads(chain_path.read_text(encoding="utf-8"))
        recomputed = _recompute_manifest_hash(manifest_path)
        if chain_entry.get("manifest_hash") != recomputed:
            return {
                "verdict": "tampered",
                "requested_run_id": bundle_root.name,
                "imported_run_id": None,
                "manifest_sha256_recomputed": recomputed,
                "manifest_sha256_signed": chain_entry.get("manifest_hash"),
                "tarball_path": str(tarball_path),
            }
        imported_run_id = _collision_safe_run_id(runs_dir, bundle_root.name)
        destination = runs_dir / imported_run_id
        shutil.copytree(bundle_root, destination)
        if imported_run_id != bundle_root.name:
            manifest_obj = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
            manifest_obj["run_id"] = imported_run_id
            manifest_bytes = forensic._canonical_json(manifest_obj)
            (destination / "manifest.json").write_bytes(manifest_bytes)
            chain_entry["run_id"] = imported_run_id
            chain_entry["manifest_hash"] = forensic._sha256_hex(manifest_bytes)
            (destination / "chain.json").write_text(
                json.dumps(chain_entry, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return {
            "verdict": "imported",
            "requested_run_id": bundle_root.name,
            "imported_run_id": imported_run_id,
            "manifest_sha256_recomputed": recomputed,
            "manifest_sha256_signed": chain_entry.get("manifest_hash"),
            "tarball_path": str(tarball_path),
            "destination_dir": str(destination),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a Dwarf bundle archive into runs/")
    parser.add_argument("tarball_path")
    parser.add_argument("--runs-dir", required=True)
    args = parser.parse_args(argv)
    result = import_bundle(tarball_path=Path(args.tarball_path), runs_dir=Path(args.runs_dir))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["verdict"] == "imported" else 1


if __name__ == "__main__":
    raise SystemExit(main())
