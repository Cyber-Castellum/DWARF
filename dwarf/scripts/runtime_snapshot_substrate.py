from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import socket
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_compose_substrate import _launch_amaru_node, _launch_haskell_node
from runtime_substrate_common import run_command, wait_for_nodes_healthy, write_json


def _ssh_base_command(host: dict) -> list[str]:
    command = ["ssh", "-n", "-o", "BatchMode=yes"]
    ssh_key_path = host.get("ssh_key_path")
    if ssh_key_path:
        command.extend(["-i", str(ssh_key_path)])
    command.append(str(host["ssh_target"]))
    return command


def _run_remote_command(host: dict, script: str):
    return run_command(_ssh_base_command(host) + [f"bash -lc {json.dumps(script)}"])


def _rsync_remote_to_local(*, host: dict, remote_root: str, local_root: Path) -> None:
    local_root.parent.mkdir(parents=True, exist_ok=True)
    ssh_key_path = host.get("ssh_key_path")
    command = ["rsync", "-a"]
    if ssh_key_path:
        command.extend(["-e", f"ssh -i {ssh_key_path}"])
    command.extend([f"{host['ssh_target']}:{remote_root.rstrip('/')}/", str(local_root)])
    result = run_command(command)
    if result.returncode != 0:
        raise RuntimeError(f"failed to rsync {remote_root} from {host['id']}: {result.stderr or result.stdout}")


def _rsync_local_to_remote(*, host: dict, local_root: Path, remote_root: str) -> None:
    ssh_key_path = host.get("ssh_key_path")
    command = ["rsync", "-a", "--delete"]
    if ssh_key_path:
        command.extend(["-e", f"ssh -i {ssh_key_path}"])
    command.extend([f"{str(local_root).rstrip('/')}/", f"{host['ssh_target']}:{remote_root.rstrip('/')}/"])
    result = run_command(command)
    if result.returncode != 0:
        raise RuntimeError(f"failed to rsync {local_root} to {host['id']}:{remote_root}: {result.stderr or result.stdout}")


def _prepare_remote_directory(*, host: dict, remote_root: str) -> None:
    result = _run_remote_command(host, f"mkdir -p {json.dumps(remote_root)}")
    if result.returncode != 0:
        raise RuntimeError(f"failed to prepare remote directory {remote_root} on {host['id']}: {result.stderr or result.stdout}")


def _clear_remote_directory(*, host: dict, remote_root: str, image_ref: str) -> None:
    shell = "shopt -s dotglob nullglob; mkdir -p /target; rm -rf /target/*"
    script = (
        "docker run --rm --entrypoint bash "
        f"-v {json.dumps(remote_root)}:/target "
        f"{json.dumps(image_ref)} "
        f"-lc {json.dumps(shell)}"
    )
    result = _run_remote_command(host, script)
    if result.returncode != 0:
        raise RuntimeError(f"failed to clear remote directory {remote_root} on {host['id']}: {result.stderr or result.stdout}")


def _copy_remote_staging_into_target(*, host: dict, staging_root: str, target_root: str, image_ref: str) -> None:
    shell = "shopt -s dotglob nullglob; mkdir -p /target; cp -a /src/. /target/"
    script = (
        "docker run --rm --entrypoint bash "
        f"-v {json.dumps(staging_root)}:/src "
        f"-v {json.dumps(target_root)}:/target "
        f"{json.dumps(image_ref)} "
        f"-lc {json.dumps(shell)}"
    )
    result = _run_remote_command(host, script)
    if result.returncode != 0:
        raise RuntimeError(
            f"failed to copy remote staging {staging_root} into {target_root} on {host['id']}: {result.stderr or result.stdout}"
        )


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _wait_multihost_docker_node_healthy(node: dict, *, host: dict, timeout_seconds: float) -> bool:
    deadline = __import__("time").time() + timeout_seconds
    container_name = str(node["container_name"])
    listen_host, port_text = str(node["listen_address"]).rsplit(":", 1)
    port = int(port_text)
    while __import__("time").time() < deadline:
        inspect = _run_remote_command(host, f"docker inspect -f {json.dumps('{{.State.Running}}')} {json.dumps(container_name)}")
        running = inspect.returncode == 0 and inspect.stdout.strip().lower() == "true"
        if running and _port_open(listen_host, port):
            return True
        __import__("time").sleep(1.0)
    return False


def _read_metadata(path: Path) -> dict:
    metadata = json.loads(path.read_text(encoding="utf-8"))
    source_path = metadata.get("runtime_metadata_path")
    if source_path:
        resolved = Path(str(source_path))
        if resolved != path and resolved.exists():
            return json.loads(resolved.read_text(encoding="utf-8"))
    return metadata


def _write_metadata(path: Path, metadata: dict) -> None:
    write_json(path, metadata)


def _find_node(metadata: dict, node_id: str) -> dict:
    for node in list(metadata.get("nodes") or metadata.get("haskell_nodes") or []) + list(metadata.get("amaru_nodes") or []):
        if str(node.get("id") or "") == node_id:
            return node
    raise ValueError(f"unknown substrate node: {node_id}")


def _find_host(metadata: dict, node: dict) -> dict | None:
    host_id = str(node.get("host_id") or "")
    if not host_id:
        return None
    for host in list(metadata.get("hosts") or []):
        if str(host.get("id") or "") == host_id:
            return host
    return None


def _snapshot_sources(node: dict) -> list[dict[str, str]]:
    impl = str(node.get("impl") or "")
    if impl == "cardano-node":
        return [{"label": "db", "source_path": str(node["db_dir"])}]
    if impl == "amaru":
        return [
            {"label": "chain", "source_path": str(node["chain_dir"])},
            {"label": "ledger", "source_path": str(node["ledger_dir"])},
        ]
    raise ValueError(f"unsupported node impl for snapshot capture: {impl}")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _add_directory_to_tar(archive: tarfile.TarFile, *, source_root: Path, archive_root: str, manifest_entries: list[dict]) -> int:
    count = 0
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
            manifest_entries.append(
                {
                    "path": archive_name,
                    "kind": "dir",
                    "size": 0,
                    "sha256": None,
                }
            )
        count += 1
    return count


def _tmux_session_exists(session_name: str) -> bool:
    result = run_command(["tmux", "has-session", "-t", session_name])
    return result.returncode == 0


def _remove_stale_runtime_artifacts(node: dict) -> None:
    for key in ("socket_path", "pid_file"):
        value = node.get(key)
        if not value:
            continue
        path = Path(str(value))
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
        except FileNotFoundError:
            pass


def _stop_node(node: dict, *, metadata: dict) -> None:
    compose_mode = str(metadata.get("compose_mode") or "")
    if compose_mode == "docker":
        if metadata.get("multi_host"):
            host = _find_host(metadata, node)
            if host is None:
                raise RuntimeError(f"multi-host docker substrate missing host mapping for {node['id']}")
            result = _run_remote_command(host, f"docker stop {json.dumps(str(node['container_name']))}")
        else:
            result = run_command(["docker", "stop", str(node["container_name"])])
        if result.returncode != 0:
            raise RuntimeError(f"docker stop failed for {node['id']}: {result.stderr or result.stdout}")
        return
    result = run_command(["tmux", "kill-session", "-t", str(node["session"])])
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        if "can't find session" not in message:
            raise RuntimeError(f"failed to stop tmux session for {node['id']}: {message}")
    _remove_stale_runtime_artifacts(node)


def _restart_node(node: dict, *, metadata: dict) -> None:
    compose_mode = str(metadata.get("compose_mode") or "")
    if compose_mode == "docker":
        if metadata.get("multi_host"):
            host = _find_host(metadata, node)
            if host is None:
                raise RuntimeError(f"multi-host docker substrate missing host mapping for {node['id']}")
            result = _run_remote_command(host, f"docker start {json.dumps(str(node['container_name']))}")
        else:
            result = run_command(["docker", "start", str(node["container_name"])])
        if result.returncode != 0:
            raise RuntimeError(f"docker start failed for {node['id']}: {result.stderr or result.stdout}")
        return
    _remove_stale_runtime_artifacts(node)
    runtime_root = Path(str(metadata["runtime_root"]))
    if str(node.get("impl") or "") == "cardano-node":
        _launch_haskell_node(
            node,
            runtime_root=runtime_root,
            binary_path=str(node["resolved_binary"]),
            public_network=bool(node.get("public_network", False)),
        )
        return
    if str(node.get("impl") or "") == "amaru":
        _launch_amaru_node(
            node,
            network_name=str(metadata["network"]),
            binary_path=str(node["resolved_binary"]),
            runtime_root=runtime_root,
        )
        return
    raise ValueError(f"unsupported node impl for restart: {node.get('impl')!r}")


def _wait_node_healthy(node: dict, *, timeout_seconds: float) -> bool:
    observed = dict(node)
    if observed.get("container_name") and observed.get("host_id"):
        host = {
            "id": observed.get("host_id"),
            "ssh_target": observed.get("host_ssh_target") or observed.get("host_id"),
        }
        return _wait_multihost_docker_node_healthy(observed, host=host, timeout_seconds=timeout_seconds)
    if observed.get("container_name"):
        observed["health_probe"] = "port-only"
    health = wait_for_nodes_healthy([observed], timeout_seconds=timeout_seconds)
    if not (health and health[0].get("healthy")):
        return False
    if node.get("session"):
        return _tmux_session_exists(str(node["session"]))
    return True


def run_snapshot_capture(
    *,
    runtime_metadata_path: Path,
    output_dir: Path,
    target_node: str,
    stop_node_during_capture: bool = True,
    healthy_timeout_seconds: float = 90.0,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _read_metadata(runtime_metadata_path)
    node = _find_node(metadata, target_node)
    sources = _snapshot_sources(node)
    snapshot_path = output_dir / f"{target_node}-snapshot.tar"
    manifest_path = output_dir / f"{target_node}-snapshot-manifest.json"
    stopped = False
    restarted = False
    node_healthy_after_restart = False
    manifest_entries: list[dict] = []
    entry_count = 0
    staging_root = Path(tempfile.mkdtemp(prefix=f"{target_node}-snapshot-capture-"))
    try:
        if stop_node_during_capture:
            _stop_node(node, metadata=metadata)
            stopped = True
        with tarfile.open(snapshot_path, "w") as archive:
            for source in sources:
                source_root = Path(source["source_path"])
                host = _find_host(metadata, node)
                if str(metadata.get("compose_mode") or "") == "docker" and bool(metadata.get("multi_host")) and host is not None:
                    local_source_root = staging_root / source["label"]
                    _rsync_remote_to_local(host=host, remote_root=str(source_root), local_root=local_source_root)
                    source_root = local_source_root
                if not source_root.exists():
                    raise RuntimeError(f"snapshot source missing for {target_node}: {source_root}")
                entry_count += _add_directory_to_tar(
                    archive,
                    source_root=source_root,
                    archive_root=source["label"],
                    manifest_entries=manifest_entries,
                )
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
        if stopped:
            _restart_node(node, metadata=metadata)
            restarted = True
            node_healthy_after_restart = _wait_node_healthy(node, timeout_seconds=healthy_timeout_seconds)
    manifest = {
        "target_node": target_node,
        "runtime_metadata_path": str(runtime_metadata_path),
        "snapshot_path": str(snapshot_path),
        "sources": sources,
        "entry_count": entry_count,
        "entries": manifest_entries,
    }
    write_json(manifest_path, manifest)
    snapshot_sha256 = _file_sha256(snapshot_path)
    report = {
        "mode": "capture",
        "runtime_metadata_path": str(runtime_metadata_path),
        "target_node": target_node,
        "result": {
            "snapshot_path": str(snapshot_path),
            "snapshot_manifest_path": str(manifest_path),
            "snapshot_sha256": snapshot_sha256,
            "snapshot_size": int(snapshot_path.stat().st_size),
            "snapshot_entry_count": entry_count,
            "stop_node_during_capture": bool(stop_node_during_capture),
            "node_restarted_after_capture": restarted,
            "node_healthy_after_restart": node_healthy_after_restart,
        },
    }
    write_json(output_dir / "result.json", report)
    return report


def _corrupt_bytes(data: bytearray, *, mode: str, byte_offset: int, byte_count: int, xor_mask: int) -> bytearray:
    start = max(0, min(len(data), int(byte_offset)))
    if mode == "zero_range":
        end = max(start, min(len(data), start + int(byte_count)))
        for index in range(start, end):
            data[index] = 0
        return data
    if mode == "flip_bits":
        if start >= len(data):
            raise ValueError("byte_offset is beyond snapshot size")
        data[start] = data[start] ^ int(xor_mask)
        return data
    raise ValueError(f"unsupported corruption mode: {mode}")


def run_snapshot_corrupt(
    *,
    snapshot_path: Path,
    output_dir: Path,
    corruption_mode: str,
    byte_offset: int = 0,
    byte_count: int = 1,
    truncate_bytes: int = 0,
    xor_mask: int = 1,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    corrupted_path = output_dir / f"{snapshot_path.stem}-corrupted{snapshot_path.suffix}"
    data = bytearray(snapshot_path.read_bytes())
    original_sha256 = hashlib.sha256(data).hexdigest()
    if corruption_mode == "truncate":
        remove_bytes = max(1, int(truncate_bytes or byte_count or 1))
        if remove_bytes >= len(data):
            raise ValueError("truncate_bytes would remove the full snapshot")
        data = data[: len(data) - remove_bytes]
    else:
        data = _corrupt_bytes(
            data,
            mode=corruption_mode,
            byte_offset=byte_offset,
            byte_count=byte_count,
            xor_mask=xor_mask,
        )
    corrupted_path.write_bytes(data)
    corrupted_sha256 = _file_sha256(corrupted_path)
    report = {
        "mode": "corrupt",
        "snapshot_path": str(snapshot_path),
        "target_node": None,
        "result": {
            "corruption_mode": corruption_mode,
            "byte_offset": int(byte_offset),
            "byte_count": int(byte_count),
            "truncate_bytes": int(truncate_bytes),
            "xor_mask": int(xor_mask),
            "original_snapshot_sha256": original_sha256,
            "corrupted_snapshot_path": str(corrupted_path),
            "corrupted_snapshot_sha256": corrupted_sha256,
            "corruption_detected": original_sha256 != corrupted_sha256,
        },
    }
    write_json(output_dir / "result.json", report)
    return report


def _clear_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def run_snapshot_restore(
    *,
    runtime_metadata_path: Path,
    snapshot_path: Path,
    output_dir: Path,
    target_node: str,
    healthy_timeout_seconds: float = 120.0,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _read_metadata(runtime_metadata_path)
    node = _find_node(metadata, target_node)
    sources = _snapshot_sources(node)
    staging_dir = Path(tempfile.mkdtemp(prefix=f"{target_node}-snapshot-restore-"))
    backup_root = output_dir / "pre-restore-backup"
    backup_root.mkdir(parents=True, exist_ok=True)
    restored_paths = []
    restore_succeeded = False
    node_healthy_after_restore = False
    try:
        _stop_node(node, metadata=metadata)
        with tarfile.open(snapshot_path, "r") as archive:
            archive.extractall(staging_dir)
        for source in sources:
            source_root = Path(source["source_path"])
            extracted_root = staging_dir / source["label"]
            if not extracted_root.exists():
                raise RuntimeError(f"snapshot missing extracted root {source['label']} for {target_node}")
            backup_path = backup_root / source_root.name
            if backup_path.exists():
                shutil.rmtree(backup_path)
            host = _find_host(metadata, node)
            multihost_docker = str(metadata.get("compose_mode") or "") == "docker" and bool(metadata.get("multi_host")) and host is not None
            if multihost_docker:
                _rsync_remote_to_local(host=host, remote_root=str(source_root), local_root=backup_path)
                _clear_remote_directory(host=host, remote_root=str(source_root), image_ref=str(node.get("image_ref") or "dwarf/cardano-node:10.7.1"))
                remote_staging_root = (
                    f"{str(host.get('runtime_root') or metadata.get('runtime_root') or '/tmp')}/"
                    f"snapshot-restore-staging/{target_node}/{source['label']}"
                )
                _prepare_remote_directory(host=host, remote_root=remote_staging_root)
                _rsync_local_to_remote(host=host, local_root=extracted_root, remote_root=remote_staging_root)
                _copy_remote_staging_into_target(
                    host=host,
                    staging_root=remote_staging_root,
                    target_root=str(source_root),
                    image_ref=str(node.get("image_ref") or "dwarf/cardano-node:10.7.1"),
                )
            else:
                if source_root.exists():
                    shutil.copytree(source_root, backup_path, dirs_exist_ok=True)
                _clear_directory(source_root)
                for child in sorted(extracted_root.iterdir()):
                    destination = source_root / child.name
                    if child.is_dir():
                        shutil.copytree(child, destination, dirs_exist_ok=True)
                    else:
                        source_root.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(child, destination)
            restored_paths.append(str(source_root))
        _restart_node(node, metadata=metadata)
        node_healthy_after_restore = _wait_node_healthy(node, timeout_seconds=healthy_timeout_seconds)
        restore_succeeded = node_healthy_after_restore
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
    report = {
        "mode": "restore",
        "runtime_metadata_path": str(runtime_metadata_path),
        "target_node": target_node,
        "result": {
            "snapshot_path": str(snapshot_path),
            "restored_paths": restored_paths,
            "backup_root": str(backup_root),
            "restore_succeeded": restore_succeeded,
            "node_healthy_after_restore": node_healthy_after_restore,
        },
    }
    write_json(output_dir / "result.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", required=True, choices=["capture", "corrupt", "restore"])
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.mode == "capture":
        report = run_snapshot_capture(
            runtime_metadata_path=Path(config["runtime_metadata_path"]),
            output_dir=Path(config["output_dir"]),
            target_node=str(config["target_node"]),
            stop_node_during_capture=bool(config.get("stop_node_during_capture", True)),
            healthy_timeout_seconds=float(config.get("healthy_timeout_seconds", 90.0)),
        )
    elif args.mode == "corrupt":
        report = run_snapshot_corrupt(
            snapshot_path=Path(config["snapshot_path"]),
            output_dir=Path(config["output_dir"]),
            corruption_mode=str(config["corruption_mode"]),
            byte_offset=int(config.get("byte_offset", 0)),
            byte_count=int(config.get("byte_count", 1)),
            truncate_bytes=int(config.get("truncate_bytes", 0)),
            xor_mask=int(config.get("xor_mask", 1)),
        )
    else:
        report = run_snapshot_restore(
            runtime_metadata_path=Path(config["runtime_metadata_path"]),
            snapshot_path=Path(config["snapshot_path"]),
            output_dir=Path(config["output_dir"]),
            target_node=str(config["target_node"]),
            healthy_timeout_seconds=float(config.get("healthy_timeout_seconds", 120.0)),
        )
    print(f"mode={report['mode']} target_node={report.get('target_node') or ''}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
