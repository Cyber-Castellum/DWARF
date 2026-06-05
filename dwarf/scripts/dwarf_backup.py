#!/usr/bin/env python3

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from profile_manager import plugin_loader

DWARF_ROOT = Path(__file__).resolve().parents[1]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class BackupComponent:
    component_id: str
    kind: str
    source_root: Path
    archive_root: Path
    restore_target: Path


def discover_backup_components(*, dwarf_root: Path, include_bundles: bool, plugin_roots: Iterable[Path] | None = None) -> list[BackupComponent]:
    roots = list(plugin_roots) if plugin_roots is not None else plugin_loader.plugin_roots_from_env()
    components = [
        BackupComponent(
            component_id="state",
            kind="state",
            source_root=dwarf_root / "state",
            archive_root=Path("payload/state"),
            restore_target=dwarf_root / "state",
        )
    ]
    for index, root in enumerate(roots):
        if root.exists():
            components.append(
                BackupComponent(
                    component_id=f"plugins-{index}",
                    kind="plugins",
                    source_root=root,
                    archive_root=Path("payload/plugins") / f"root-{index}",
                    restore_target=root,
                )
            )
    if include_bundles:
        for name in ("runs", "bundles"):
            path = dwarf_root / name
            if path.exists():
                components.append(
                    BackupComponent(
                        component_id=name,
                        kind=name,
                        source_root=path,
                        archive_root=Path("payload") / name,
                        restore_target=path,
                    )
                )
    return components


def _iter_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file())


def build_backup_manifest(*, dwarf_root: Path, include_bundles: bool, plugin_roots: Iterable[Path] | None = None) -> dict:
    manifest_components = []
    file_count = 0
    for component in discover_backup_components(dwarf_root=dwarf_root, include_bundles=include_bundles, plugin_roots=plugin_roots):
        files = []
        for path in _iter_files(component.source_root):
            rel = path.relative_to(component.source_root)
            files.append(
                {
                    "path": str(rel),
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )
        file_count += len(files)
        manifest_components.append(
            {
                "component_id": component.component_id,
                "kind": component.kind,
                "source_root": str(component.source_root),
                "restore_target": str(component.restore_target),
                "archive_root": str(component.archive_root),
                "file_count": len(files),
                "files": files,
            }
        )
    return {
        "schema_version": "v1",
        "dwarf_root": str(dwarf_root),
        "include_bundles": include_bundles,
        "component_count": len(manifest_components),
        "file_count": file_count,
        "components": manifest_components,
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
    tar.addfile(info, fileobj=io.BytesIO(payload))


def export_backup(*, dwarf_root: Path, destination: Path, include_bundles: bool, plugin_roots: Iterable[Path] | None = None) -> dict:
    manifest = build_backup_manifest(dwarf_root=dwarf_root, include_bundles=include_bundles, plugin_roots=plugin_roots)
    components = {
        component.component_id: component
        for component in discover_backup_components(dwarf_root=dwarf_root, include_bundles=include_bundles, plugin_roots=plugin_roots)
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
                _add_bytes(tar, manifest_bytes, Path("dwarf-backup") / "BACKUP-MANIFEST.json")
                for component_meta in manifest["components"]:
                    component = components[component_meta["component_id"]]
                    for path in _iter_files(component.source_root):
                        rel = path.relative_to(component.source_root)
                        _add_file(tar, path, Path("dwarf-backup") / component.archive_root / rel)
    return {
        "destination": str(destination),
        "sha256": _sha256_file(destination),
        "component_count": manifest["component_count"],
        "file_count": manifest["file_count"],
        "include_bundles": include_bundles,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a deterministic backup archive for the local Dwarf installation")
    parser.add_argument("--to", required=True)
    parser.add_argument("--include-bundles", action="store_true")
    args = parser.parse_args(argv)
    result = export_backup(
        dwarf_root=DWARF_ROOT,
        destination=Path(args.to),
        include_bundles=args.include_bundles,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
