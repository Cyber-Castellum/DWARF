#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
import tarfile
import tempfile
from pathlib import Path


def _safe_extract_tarball(*, tarball_path: Path, destination: Path) -> Path:
    with tarfile.open(tarball_path, "r:gz") as tar:
        for member in tar.getmembers():
            member_path = destination / member.name
            resolved = member_path.resolve()
            if not str(resolved).startswith(str(destination.resolve())):
                raise ValueError(f"unsafe archive member path: {member.name}")
        tar.extractall(destination)
    root = destination / "dwarf-backup"
    if not root.is_dir():
        raise ValueError("backup archive is missing dwarf-backup root directory")
    return root


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_backup_archive(*, tarball_path: Path) -> dict:
    extract_holder = Path(tempfile.mkdtemp(prefix="dwarf-restore-"))
    root = _safe_extract_tarball(tarball_path=tarball_path, destination=extract_holder)
    manifest_path = root / "BACKUP-MANIFEST.json"
    if not manifest_path.exists():
        raise ValueError("backup archive is missing BACKUP-MANIFEST.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mismatches = []
    for component in manifest.get("components", []):
        archive_root = root / Path(component["archive_root"])
        for file_meta in component.get("files", []):
            path = archive_root / file_meta["path"]
            if not path.exists():
                mismatches.append(
                    {
                        "component_id": component["component_id"],
                        "path": file_meta["path"],
                        "reason": "missing",
                    }
                )
                continue
            actual = _sha256_file(path)
            if actual != file_meta["sha256"]:
                mismatches.append(
                    {
                        "component_id": component["component_id"],
                        "path": file_meta["path"],
                        "reason": "sha256-mismatch",
                        "expected": file_meta["sha256"],
                        "actual": actual,
                    }
                )
    return {"manifest": manifest, "extract_root": root, "mismatches": mismatches}


def restore_backup(*, tarball_path: Path, dry_run: bool = False) -> dict:
    validated = validate_backup_archive(tarball_path=tarball_path)
    manifest = validated["manifest"]
    root: Path = validated["extract_root"]
    if validated["mismatches"]:
        return {"verdict": "invalid", "dry_run": dry_run, "mismatches": validated["mismatches"]}
    actions = []
    for component in manifest.get("components", []):
        archive_root = root / Path(component["archive_root"])
        target_root = Path(component["restore_target"]).expanduser()
        actions.append(
            {
                "component_id": component["component_id"],
                "target_root": str(target_root),
                "file_count": component["file_count"],
            }
        )
        if dry_run:
            continue
        target_root.mkdir(parents=True, exist_ok=True)
        for file_meta in component.get("files", []):
            source = archive_root / file_meta["path"]
            target = target_root / file_meta["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return {
        "verdict": "dry-run" if dry_run else "restored",
        "dry_run": dry_run,
        "component_count": manifest.get("component_count", 0),
        "file_count": manifest.get("file_count", 0),
        "actions": actions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore a local Dwarf installation from a backup archive")
    parser.add_argument("archive_path")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = restore_backup(tarball_path=Path(args.archive_path), dry_run=args.dry_run)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["verdict"] in {"restored", "dry-run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
