from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_snapshot_substrate import (  # noqa: E402
    _file_sha256,
    _read_metadata,
    _restart_node,
    _stop_node,
    _wait_node_healthy,
    _write_metadata,
)
from runtime_substrate_common import write_json  # noqa: E402


def _add_file_to_tar(
    archive: tarfile.TarFile,
    *,
    source_path: Path,
    archive_name: str,
    manifest_entries: list[dict],
) -> int:
    info = archive.gettarinfo(str(source_path), arcname=archive_name)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    with source_path.open("rb") as handle:
        archive.addfile(info, handle)
    manifest_entries.append(
        {
            "path": archive_name,
            "kind": "file",
            "size": int(source_path.stat().st_size),
            "sha256": _file_sha256(source_path),
        }
    )
    return 1


def _add_directory_to_tar(
    archive: tarfile.TarFile,
    *,
    source_root: Path,
    archive_root: str,
    manifest_entries: list[dict],
) -> int:
    count = 0
    if not source_root.exists():
        return count
    for path in sorted(source_root.rglob("*")):
        relative = path.relative_to(source_root)
        archive_name = str(PurePosixPath(archive_root) / PurePosixPath(relative.as_posix()))
        info = archive.gettarinfo(str(path), arcname=archive_name)
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mtime = 0
        if path.is_file():
            with path.open("rb") as handle:
                archive.addfile(info, handle)
            manifest_entries.append(
                {
                    "path": archive_name,
                    "kind": "file",
                    "size": int(path.stat().st_size),
                    "sha256": _file_sha256(path),
                }
            )
        else:
            archive.addfile(info)
            manifest_entries.append({"path": archive_name, "kind": "dir", "size": 0, "sha256": None})
        count += 1
    return count


def _runtime_paths(runtime_root: Path) -> list[tuple[str, Path]]:
    candidates = [
        ("runtime.json", runtime_root / "runtime.json"),
        ("env", runtime_root / "env"),
        ("cardano-topology", runtime_root / "cardano-topology"),
        ("public-network", runtime_root / "public-network"),
        ("docker-compose.yml", runtime_root / "docker-compose.yml"),
        ("hosts", runtime_root / "hosts"),
    ]
    return [(label, path) for label, path in candidates if path.exists()]


def _stop_all_nodes(metadata: dict) -> None:
    nodes = list(metadata.get("nodes") or metadata.get("haskell_nodes") or []) + list(metadata.get("amaru_nodes") or [])
    for node in nodes:
        _stop_node(node, metadata=metadata)


def _restart_all_nodes(metadata: dict) -> None:
    nodes = list(metadata.get("nodes") or metadata.get("haskell_nodes") or []) + list(metadata.get("amaru_nodes") or [])
    for node in nodes:
        _restart_node(node, metadata=metadata)


def _all_nodes_healthy(metadata: dict, *, timeout_seconds: float) -> bool:
    nodes = list(metadata.get("nodes") or metadata.get("haskell_nodes") or []) + list(metadata.get("amaru_nodes") or [])
    return all(_wait_node_healthy(node, timeout_seconds=timeout_seconds) for node in nodes)


def _clear_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def run_substrate_checkpoint(
    *,
    runtime_metadata_path: Path,
    output_dir: Path,
    stop_nodes_during_capture: bool = True,
    healthy_timeout_seconds: float = 120.0,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _read_metadata(runtime_metadata_path)
    runtime_root = Path(str(metadata["runtime_root"]))
    checkpoint_path = output_dir / "substrate-checkpoint.tar"
    manifest_path = output_dir / "substrate-checkpoint-manifest.json"
    manifest_entries: list[dict] = []
    entry_count = 0
    stopped = False
    resumed = False
    healthy_after_checkpoint = False
    try:
        if stop_nodes_during_capture:
            _stop_all_nodes(metadata)
            stopped = True
        with tarfile.open(checkpoint_path, "w") as archive:
            for label, path in _runtime_paths(runtime_root):
                if path.is_dir():
                    entry_count += _add_directory_to_tar(
                        archive,
                        source_root=path,
                        archive_root=label,
                        manifest_entries=manifest_entries,
                    )
                else:
                    entry_count += _add_file_to_tar(
                        archive,
                        source_path=path,
                        archive_name=label,
                        manifest_entries=manifest_entries,
                    )
    finally:
        if stopped:
            _restart_all_nodes(metadata)
            resumed = True
            healthy_after_checkpoint = _all_nodes_healthy(metadata, timeout_seconds=healthy_timeout_seconds)
    manifest = {
        "runtime_metadata_path": str(runtime_metadata_path),
        "runtime_root": str(runtime_root),
        "entry_count": entry_count,
        "entries": manifest_entries,
        "paths": [label for label, _ in _runtime_paths(runtime_root)],
    }
    write_json(manifest_path, manifest)
    report = {
        "mode": "checkpoint",
        "runtime_metadata_path": str(runtime_metadata_path),
        "result": {
            "checkpoint_path": str(checkpoint_path),
            "checkpoint_manifest_path": str(manifest_path),
            "checkpoint_sha256": _file_sha256(checkpoint_path),
            "checkpoint_size": int(checkpoint_path.stat().st_size),
            "checkpoint_entry_count": entry_count,
            "stop_nodes_during_capture": bool(stop_nodes_during_capture),
            "nodes_restarted_after_checkpoint": resumed,
            "nodes_healthy_after_checkpoint": healthy_after_checkpoint,
        },
    }
    write_json(output_dir / "result.json", report)
    return report


def run_substrate_resume(
    *,
    runtime_metadata_path: Path,
    checkpoint_path: Path,
    output_dir: Path,
    healthy_timeout_seconds: float = 120.0,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _read_metadata(runtime_metadata_path)
    runtime_root = Path(str(metadata["runtime_root"]))
    staging_dir = Path(tempfile.mkdtemp(prefix="substrate-checkpoint-resume-"))
    restored_paths: list[str] = []
    try:
        _stop_all_nodes(metadata)
        with tarfile.open(checkpoint_path, "r") as archive:
            archive.extractall(staging_dir)
        for label, current_path in _runtime_paths(runtime_root):
            _clear_path(current_path)
            extracted = staging_dir / label
            if extracted.is_dir():
                shutil.copytree(extracted, current_path, dirs_exist_ok=True)
            elif extracted.exists():
                current_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(extracted, current_path)
            restored_paths.append(str(current_path))
        restored_metadata_path = runtime_root / "runtime.json"
        restored_metadata = _read_metadata(restored_metadata_path)
        _restart_all_nodes(restored_metadata)
        healthy = _all_nodes_healthy(restored_metadata, timeout_seconds=healthy_timeout_seconds)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
    report = {
        "mode": "resume",
        "runtime_metadata_path": str(runtime_metadata_path),
        "result": {
            "checkpoint_path": str(checkpoint_path),
            "restored_runtime_metadata_path": str(runtime_root / "runtime.json"),
            "restored_paths": restored_paths,
            "resume_succeeded": healthy,
            "nodes_healthy_after_resume": healthy,
        },
    }
    write_json(output_dir / "result.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", required=True, choices=["checkpoint", "resume"])
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.mode == "checkpoint":
        report = run_substrate_checkpoint(
            runtime_metadata_path=Path(config["runtime_metadata_path"]),
            output_dir=Path(config["output_dir"]),
            stop_nodes_during_capture=bool(config.get("stop_nodes_during_capture", True)),
            healthy_timeout_seconds=float(config.get("healthy_timeout_seconds", 120.0)),
        )
    else:
        report = run_substrate_resume(
            runtime_metadata_path=Path(config["runtime_metadata_path"]),
            checkpoint_path=Path(config["checkpoint_path"]),
            output_dir=Path(config["output_dir"]),
            healthy_timeout_seconds=float(config.get("healthy_timeout_seconds", 120.0)),
        )
    print(f"mode={report['mode']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
