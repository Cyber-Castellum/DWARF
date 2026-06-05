#!/usr/bin/env python3

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tarfile
from pathlib import Path


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _export_manifest(run_dir: Path) -> dict:
    files = []
    for path in sorted(p for p in run_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(run_dir)
        files.append(
            {
                "path": str(rel),
                "size_bytes": path.stat().st_size,
                "sha256": _file_sha256(path),
            }
        )
    return {
        "schema_version": "v1",
        "run_id": run_dir.name,
        "file_count": len(files),
        "files": files,
    }


def export_bundle(*, run_dir: Path, destination: Path) -> dict:
    manifest = _export_manifest(run_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                for rel in ("manifest.json", "chain.json"):
                    path = run_dir / rel
                    if path.exists():
                        _add_file(tar, path, Path(run_dir.name) / rel)
                export_manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
                _add_bytes(tar, export_manifest_bytes, Path(run_dir.name) / "EXPORT-MANIFEST.json")
                for path in sorted(p for p in run_dir.rglob("*") if p.is_file()):
                    rel = path.relative_to(run_dir)
                    if rel in {Path("manifest.json"), Path("chain.json")}:
                        continue
                    _add_file(tar, path, Path(run_dir.name) / rel)
    return {
        "run_id": run_dir.name,
        "destination": str(destination),
        "sha256": _file_sha256(destination),
        "file_count": manifest["file_count"],
    }


def _add_file(tar: tarfile.TarFile, path: Path, arcname: Path) -> None:
    info = tar.gettarinfo(str(path), arcname=str(arcname))
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = 0
    with path.open("rb") as handle:
        tar.addfile(info, handle)


def _add_bytes(tar: tarfile.TarFile, payload: bytes, arcname: Path) -> None:
    info = tarfile.TarInfo(str(arcname))
    info.size = len(payload)
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = 0
    tar.addfile(info, fileobj=__import__("io").BytesIO(payload))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a Dwarf bundle into a deterministic tar.gz")
    parser.add_argument("run_dir")
    parser.add_argument("--to", required=True)
    args = parser.parse_args(argv)
    destination = Path(args.to)
    if str(destination).startswith("s3://"):
        raise SystemExit("s3 destinations are not implemented yet")
    result = export_bundle(run_dir=Path(args.run_dir), destination=destination)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
