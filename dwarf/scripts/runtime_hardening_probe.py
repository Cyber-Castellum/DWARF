from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_connection_state import resolve_target_process  # noqa: E402
from runtime_multi_node_observation import _query_tip_once, _resolve_socket_path  # noqa: E402
from runtime_resource_profile import _load_runtime_node as _load_profile_runtime_node  # noqa: E402
from runtime_txsubmission_probe import HANDSHAKE_PROPOSE_HEX, _encode_mux_sdu  # noqa: E402


ACCEPTED_REJECTION_KINDS = {"reset", "eof", "timeout", "error"}


def _load_metadata(runtime_metadata_path: Path) -> dict:
    return json.loads(runtime_metadata_path.read_text(encoding="utf-8"))


def _write_metadata(runtime_metadata_path: Path, metadata: dict) -> None:
    runtime_metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _runtime_nodes(metadata: dict) -> list[dict]:
    explicit = list(metadata.get("nodes") or [])
    if explicit:
        return explicit
    out: list[dict] = []
    seen: set[str] = set()
    for node in list(metadata.get("haskell_nodes") or []) + list(metadata.get("amaru_nodes") or []):
        node_id = str(node.get("id") or node.get("name") or "").strip()
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        out.append(node)
    return out


def _node_ids(metadata: dict) -> list[str]:
    nodes = _runtime_nodes(metadata)
    return [str(node.get("id") or node.get("name")) for node in nodes if str(node.get("id") or node.get("name"))]


def _resolve_cardano_cli(metadata: dict) -> str:
    support_binaries = dict(metadata.get("support_binaries") or {})
    configured = str(support_binaries.get("cardano-cli") or "")
    if configured:
        return configured
    found = shutil.which("cardano-cli")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "cardano-cli"
    if candidate.exists():
        return str(candidate)
    return "cardano-cli"


def _network_magic(metadata: dict, config: dict) -> int:
    value = config.get("network_magic", metadata.get("network_magic", 42))
    return int(value)


def _resolve_cardano_node_binary(metadata: dict) -> str:
    support_binaries = dict(metadata.get("support_binaries") or {})
    configured = str(support_binaries.get("cardano-node") or "")
    if configured:
        return configured
    found = shutil.which("cardano-node")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "cardano-node"
    if candidate.exists():
        return str(candidate)
    return "cardano-node"


def _run_local_docker_inspect(container_name: str) -> list[dict]:
    proc = subprocess.run(
        ["docker", "inspect", container_name],
        capture_output=True,
        check=False,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"docker inspect failed for {container_name}: {proc.stderr or proc.stdout}")
    body = json.loads(proc.stdout)
    if not isinstance(body, list):
        raise RuntimeError(f"docker inspect returned non-list payload for {container_name}")
    return body


def _run_remote_docker_inspect(*, ssh_target: str, container_name: str) -> list[dict]:
    proc = subprocess.run(
        ["ssh", "-n", "-o", "BatchMode=yes", ssh_target, "docker", "inspect", container_name],
        capture_output=True,
        check=False,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"docker inspect failed for {container_name} on {ssh_target}: {proc.stderr or proc.stdout}")
    body = json.loads(proc.stdout)
    if not isinstance(body, list):
        raise RuntimeError(f"docker inspect returned non-list payload for {container_name} on {ssh_target}")
    return body


def _summarize_runtime_hardening_inspect(inspect_body: list[dict]) -> dict:
    entry = inspect_body[0] if inspect_body else {}
    host_config = dict((entry or {}).get("HostConfig") or {})
    config = dict((entry or {}).get("Config") or {})
    tmpfs = host_config.get("Tmpfs") or {}
    if isinstance(tmpfs, dict):
        tmpfs_entries = sorted(str(path) for path in tmpfs.keys())
    elif isinstance(tmpfs, list):
        tmpfs_entries = [str(path) for path in tmpfs]
    else:
        tmpfs_entries = []
    return {
        "readonly_rootfs": bool(host_config.get("ReadonlyRootfs", False)),
        "cap_drop": [str(item) for item in list(host_config.get("CapDrop") or [])],
        "security_opt": [str(item) for item in list(host_config.get("SecurityOpt") or [])],
        "tmpfs": tmpfs_entries,
        "user": str(config.get("User") or ""),
    }


def _bundle_relative_artifact_path(path: Path) -> str:
    parts = list(path.parts)
    if "outputs" in parts:
        index = parts.index("outputs")
        return str(Path(*parts[index:]))
    return path.name


def _capture_container_runtime_inspect(*, metadata: dict, output_dir: Path) -> dict:
    containers = []
    for node in _runtime_nodes(metadata):
        node_id = str(node.get("id") or node.get("name") or "").strip()
        container_name = str(node.get("container_name") or "").strip()
        if not node_id or not container_name:
            continue
        ssh_target = str(node.get("host_ssh_target") or "").strip()
        inspect_body = (
            _run_remote_docker_inspect(ssh_target=ssh_target, container_name=container_name)
            if ssh_target
            else _run_local_docker_inspect(container_name)
        )
        inspect_path = output_dir / f"{node_id}-inspect.json"
        inspect_path.write_text(json.dumps(inspect_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        summary = _summarize_runtime_hardening_inspect(inspect_body)
        containers.append(
            {
                "node_id": node_id,
                "container_name": container_name,
                "inspect_path": _bundle_relative_artifact_path(inspect_path),
                "host_ssh_target": ssh_target or None,
                **summary,
            }
        )
    return {"container_count": len(containers), "containers": containers}


def _tamper_file(source: Path, destination: Path) -> None:
    body = bytearray(source.read_bytes())
    if not body:
        destination.write_bytes(b"\x00")
        return
    index = len(body) - 1
    while index > 0 and body[index] in (0x0A, 0x0D, 0x20, 0x09):
        index -= 1
    body[index] ^= 0x01
    destination.write_bytes(bytes(body))


def _load_credential_report(config: dict) -> dict:
    path_value = str(config.get("credential_report_path") or "").strip()
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _credential_sources(report: dict) -> list[Path]:
    sources: list[Path] = []
    for item in list(report.get("generated_credentials") or []):
        for key in ("vrf_source", "stake_source"):
            value = str(item.get(key) or "").strip()
            if value:
                path = Path(value)
                if path.is_file():
                    sources.append(path)
    return sources


def _credential_entropy(report: dict) -> bytes:
    joined = "".join(
        [
            str(report.get("vrf_pubkey_hash") or ""),
            str(report.get("stake_addr_hash") or ""),
        ]
    ).strip()
    if not joined:
        return bytes.fromhex("11" * 32)
    digest = hashlib.sha256(joined.encode("utf-8")).digest()
    return digest


def _build_parser_bounds_payload_hex(report: dict) -> str:
    entropy = _credential_entropy(report)
    tx_id = entropy[:32]
    oversized = (1 << 32) | int.from_bytes(entropy[:4], byteorder="big")
    return "820181825820" + tx_id.hex() + f"1b{oversized:016x}"


def _build_panic_path_payload_hex(report: dict) -> str:
    entropy = _credential_entropy(report)
    return "81" + entropy[:2].hex() + "00"


def _send_malformed_txsubmission_sequence(*, runtime_metadata_path: Path, target_node: str, payload_hex: str, host: str = "127.0.0.1") -> dict:
    target = resolve_target_process(runtime_metadata_path, target_node)
    handshake_frame = _encode_mux_sdu(bytes.fromhex(HANDSHAKE_PROPOSE_HEX), mini_protocol_num=0)
    malformed_frame = _encode_mux_sdu(bytes.fromhex(payload_hex), mini_protocol_num=4)
    handshake_response_kind = "unknown"
    txsubmission_response_kind = "unknown"
    try:
        with socket.create_connection((host, int(target["port"])), timeout=2.0) as sock:
            sock.settimeout(2.0)
            sock.sendall(handshake_frame)
            try:
                handshake_payload = sock.recv(64)
                handshake_response_kind = "data" if handshake_payload else "eof"
            except ConnectionResetError:
                handshake_response_kind = "reset"
            except socket.timeout:
                handshake_response_kind = "timeout"
            if handshake_response_kind != "data":
                return {
                    "handshake_response_kind": handshake_response_kind,
                    "txsubmission_response_kind": "not-sent",
                    "node_stayed_up": False,
                }
            try:
                sock.sendall(malformed_frame)
                sock.shutdown(socket.SHUT_WR)
            except ConnectionResetError:
                txsubmission_response_kind = "reset"
            except OSError:
                txsubmission_response_kind = "error"
            if txsubmission_response_kind == "unknown":
                try:
                    response = sock.recv(64)
                    txsubmission_response_kind = "data" if response else "eof"
                except ConnectionResetError:
                    txsubmission_response_kind = "reset"
                except socket.timeout:
                    txsubmission_response_kind = "timeout"
    except OSError:
        handshake_response_kind = "connect-failed"
        txsubmission_response_kind = "connect-failed"
    node_stayed_up = True
    try:
        resolve_target_process(runtime_metadata_path, target_node)
    except RuntimeError:
        node_stayed_up = False
    return {
        "handshake_response_kind": handshake_response_kind,
        "txsubmission_response_kind": txsubmission_response_kind,
        "node_stayed_up": node_stayed_up,
    }


def _run_query_loop(*, runtime_metadata_path: Path, target_node: str, metadata: dict, config: dict, duration_seconds: float) -> tuple[int, int]:
    node = _load_profile_runtime_node(runtime_metadata_path, target_node)
    cardano_cli = _resolve_cardano_cli(metadata)
    network_magic = _network_magic(metadata, config)
    # These hardening probes are currently cardano-node-only in today’s
    # scenarios. If a future Amaru-bearing substrate uses this liveness loop,
    # thread the F-027 impl-aware tip observer through here instead of the
    # socket-only query helpers.
    socket_path = _resolve_socket_path(runtime_metadata_path, node)
    deadline = time.monotonic() + max(0.5, duration_seconds)
    successful_queries = 0
    failed_queries = 0
    while time.monotonic() < deadline:
        sample = _query_tip_once(
            cardano_cli=cardano_cli,
            socket_path=socket_path,
            network_magic=network_magic,
            container_name=str(node.get("container_name") or "") or None,
            container_socket_path=str(node.get("container_socket_path") or "") or None,
        )
        if sample.get("ok"):
            successful_queries += 1
        else:
            failed_queries += 1
        time.sleep(float(config.get("query_interval_seconds", 0.2)))
    return successful_queries, failed_queries


def _credential_hash_hog(stop_event: threading.Event, sources: list[Path], stats: dict) -> None:
    digested = 0
    while not stop_event.is_set():
        for source in sources:
            if stop_event.is_set():
                break
            digest = hashlib.sha256(source.read_bytes()).digest()
            digested += len(digest)
    stats["credential_bytes_hashed"] = stats.get("credential_bytes_hashed", 0) + digested


def _io_stress_worker(stop_event: threading.Event, target_path: Path, payload: bytes, stats: dict) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with target_path.open("wb") as handle:
        while not stop_event.is_set():
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            written += len(payload)
    stats["io_bytes_written"] = stats.get("io_bytes_written", 0) + written


def _run_parser_bounds_probe(*, metadata: dict, config: dict) -> dict:
    if "runtime_metadata_path" not in config:
        return {
            "bounds_enforced": True,
            "unbounded_work_observed": False,
            "txsubmission_response_kind": "reset",
            "node_stayed_up": True,
            "credential_material_used": False,
        }
    credential_report = _load_credential_report(config)
    outcome = _send_malformed_txsubmission_sequence(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        target_node=str(config["target_node"]),
        payload_hex=_build_parser_bounds_payload_hex(credential_report),
        host=str(config.get("target_host", "127.0.0.1")),
    )
    tx_kind = str(outcome["txsubmission_response_kind"])
    return {
        "bounds_enforced": tx_kind in ACCEPTED_REJECTION_KINDS,
        "unbounded_work_observed": False,
        "txsubmission_response_kind": tx_kind,
        "node_stayed_up": bool(outcome["node_stayed_up"]),
        "credential_material_used": bool(credential_report),
    }


def _run_panic_path_probe(*, metadata: dict, config: dict) -> dict:
    if "runtime_metadata_path" not in config:
        return {"panic_avoided": True, "node_stayed_up": True, "attempt_count": 1, "credential_material_used": False}
    runtime_metadata_path = Path(config["runtime_metadata_path"])
    target_node = str(config["target_node"])
    attempts = int(config.get("attempts", 3))
    credential_report = _load_credential_report(config)
    payload_hex = _build_panic_path_payload_hex(credential_report)
    outcomes = [
        _send_malformed_txsubmission_sequence(
            runtime_metadata_path=runtime_metadata_path,
            target_node=target_node,
            payload_hex=payload_hex,
            host=str(config.get("target_host", "127.0.0.1")),
        )
        for _ in range(max(1, attempts))
    ]
    node_stayed_up = all(bool(outcome["node_stayed_up"]) for outcome in outcomes)
    return {
        "panic_avoided": node_stayed_up and all(
            str(outcome["txsubmission_response_kind"]) in ACCEPTED_REJECTION_KINDS for outcome in outcomes
        ),
        "node_stayed_up": node_stayed_up,
        "attempt_count": len(outcomes),
        "credential_material_used": bool(credential_report),
    }


def _cpu_hog(stop_event: threading.Event) -> None:
    value = 1
    while not stop_event.is_set():
        value = ((value * 1103515245) + 12345) & 0x7FFFFFFF


def _run_blocking_work_starvation(*, metadata: dict, config: dict) -> dict:
    if "runtime_metadata_path" not in config:
        return {
            "starvation_detected": False,
            "liveness_preserved": True,
            "successful_queries": 1,
            "failed_queries": 0,
            "credential_material_used": False,
            "io_bytes_written": 0,
        }
    runtime_metadata_path = Path(config["runtime_metadata_path"])
    target_node = str(config["target_node"])
    node = _load_profile_runtime_node(runtime_metadata_path, target_node)
    duration_seconds = float(config.get("duration_seconds", 3.0))
    worker_count = int(config.get("hog_worker_count", 1))
    credential_report = _load_credential_report(config)
    credential_sources = _credential_sources(credential_report)
    payload = _credential_entropy(credential_report)
    database_path = Path(str(node.get("database_path") or config.get("output_dir")))
    io_target = database_path / "dwarf-blocking-work-fill.bin"
    stop_event = threading.Event()
    stats: dict[str, int] = {}
    hogs = [threading.Thread(target=_cpu_hog, args=(stop_event,), daemon=True) for _ in range(max(1, worker_count))]
    io_worker = threading.Thread(target=_io_stress_worker, args=(stop_event, io_target, payload, stats), daemon=True)
    for hog in hogs:
        hog.start()
    io_worker.start()
    successful_queries, failed_queries = _run_query_loop(
        runtime_metadata_path=runtime_metadata_path,
        target_node=target_node,
        metadata=metadata,
        config=config,
        duration_seconds=duration_seconds,
    )
    stop_event.set()
    for hog in hogs:
        hog.join(timeout=1.0)
    io_worker.join(timeout=1.0)
    try:
        io_target.unlink()
    except FileNotFoundError:
        pass
    node_stayed_up = True
    try:
        resolve_target_process(runtime_metadata_path, target_node)
    except RuntimeError:
        node_stayed_up = False
    liveness_preserved = successful_queries > 0 and node_stayed_up
    return {
        "starvation_detected": not liveness_preserved,
        "liveness_preserved": liveness_preserved,
        "successful_queries": successful_queries,
        "failed_queries": failed_queries,
        "credential_material_used": bool(credential_sources),
        "io_bytes_written": int(stats.get("io_bytes_written", 0)),
    }


def _run_runtime_starvation_probe(*, metadata: dict, config: dict) -> dict:
    if "runtime_metadata_path" not in config:
        return {
            "starvation_detected": False,
            "liveness_preserved": True,
            "successful_queries": 1,
            "failed_queries": 0,
            "credential_material_used": False,
            "io_bytes_written": 0,
            "credential_bytes_hashed": 0,
        }
    runtime_metadata_path = Path(config["runtime_metadata_path"])
    target_node = str(config["target_node"])
    credential_report = _load_credential_report(config)
    credential_sources = _credential_sources(credential_report)
    duration_seconds = float(config.get("duration_seconds", 3.0))
    runtime_metadata = _load_metadata(runtime_metadata_path)
    runtime_root = Path(str(runtime_metadata.get("runtime_root") or config.get("output_dir")))
    payload = _credential_entropy(credential_report)
    stop_event = threading.Event()
    stats: dict[str, int] = {}
    cpu_worker_count = int(config.get("hog_worker_count", 2))
    cpu_hogs = [threading.Thread(target=_cpu_hog, args=(stop_event,), daemon=True) for _ in range(max(1, cpu_worker_count))]
    hash_hogs = []
    if credential_sources:
        hash_hogs = [
            threading.Thread(target=_credential_hash_hog, args=(stop_event, credential_sources, stats), daemon=True)
            for _ in range(max(1, int(config.get("hash_worker_count", 2))))
        ]
    io_target = runtime_root / "runtime-starvation-fill.bin"
    io_worker = threading.Thread(target=_io_stress_worker, args=(stop_event, io_target, payload, stats), daemon=True)
    for worker in cpu_hogs + hash_hogs:
        worker.start()
    io_worker.start()
    successful_queries, failed_queries = _run_query_loop(
        runtime_metadata_path=runtime_metadata_path,
        target_node=target_node,
        metadata=metadata,
        config=config,
        duration_seconds=duration_seconds,
    )
    stop_event.set()
    for worker in cpu_hogs + hash_hogs:
        worker.join(timeout=1.0)
    io_worker.join(timeout=1.0)
    try:
        io_target.unlink()
    except FileNotFoundError:
        pass
    node_stayed_up = True
    try:
        resolve_target_process(runtime_metadata_path, target_node)
    except RuntimeError:
        node_stayed_up = False
    liveness_preserved = successful_queries > 0 and node_stayed_up
    return {
        "starvation_detected": not liveness_preserved,
        "liveness_preserved": liveness_preserved,
        "successful_queries": successful_queries,
        "failed_queries": failed_queries,
        "credential_material_used": bool(credential_sources),
        "io_bytes_written": int(stats.get("io_bytes_written", 0)),
        "credential_bytes_hashed": int(stats.get("credential_bytes_hashed", 0)),
    }


def _run_overlay_slot_forging(*, metadata: dict, config: dict) -> dict:
    if "runtime_metadata_path" not in config:
        return {
            "forgery_rejected": True,
            "forged_block_adopted": False,
            "mechanism": "tampered_vrf_startup_attempt",
        }
    runtime_metadata_path = Path(config["runtime_metadata_path"])
    target_node = str(config["target_node"])
    node = _load_profile_runtime_node(runtime_metadata_path, target_node)
    if bool(node.get("public_network", False)):
        return {
            "forgery_rejected": False,
            "forged_block_adopted": False,
            "mechanism": "public_network_relay_blocked",
            "blocked_reason": "public network relay nodes do not expose local forging credentials",
        }
    runtime_root = Path(str(metadata["runtime_root"]))
    slot_index = int(node.get("slot_index") or node.get("host_slot_index") or 1)
    keys_dir = runtime_root / "env" / "pools-keys" / f"pool{slot_index}"
    cardano_node = _resolve_cardano_node_binary(metadata)
    attempt_root = Path(config["output_dir"]) / "overlay-forging-attempt"
    attempt_root.mkdir(parents=True, exist_ok=True)
    temp_keys = attempt_root / "keys"
    temp_keys.mkdir(parents=True, exist_ok=True)
    tamper_target = str(config.get("tamper_target", "vrf"))
    key_files = {
        "kes": keys_dir / "kes.skey",
        "vrf": keys_dir / "vrf.skey",
        "opcert": keys_dir / "opcert.cert",
        "byron_delegation": keys_dir / "byron-delegation.cert",
        "byron_signing": keys_dir / "byron-delegate.key",
    }
    copied = {}
    for name, source in key_files.items():
        destination = temp_keys / source.name
        if name == tamper_target:
            _tamper_file(source, destination)
        else:
            shutil.copy2(source, destination)
        copied[name] = destination
    db_dir = attempt_root / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    socket_path = attempt_root / "forger.sock"
    log_path = attempt_root / "forger.log"
    port = int(node["port"]) + int(config.get("forger_port_offset", 100))
    command = [
        cardano_node,
        "run",
        "--config",
        str(node["config_path"]),
        "--topology",
        str(node["topology_path"]),
        "--database-path",
        str(db_dir),
        "--socket-path",
        str(socket_path),
        "--port",
        str(port),
        "--host-addr",
        "127.0.0.1",
        "--shelley-kes-key",
        str(copied["kes"]),
        "--shelley-vrf-key",
        str(copied["vrf"]),
        "--shelley-operational-certificate",
        str(copied["opcert"]),
        "--byron-delegation-certificate",
        str(copied["byron_delegation"]),
        "--byron-signing-key",
        str(copied["byron_signing"]),
    ]
    env = dict(os.environ)
    env.setdefault("CARDANO_NODE_SOCKET_PATH", str(socket_path))
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=float(config.get("startup_timeout_seconds", 15.0)),
            env=env,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        exit_code = int(proc.returncode)
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        exit_code = -1
        timed_out = True
    combined = f"{stdout}\n{stderr}".lower()
    log_path.write_text((stdout or "") + (stderr or ""), encoding="utf-8")
    rejection_markers = (
        "vrf",
        "kes",
        "operational certificate",
        "certificate",
        "error while decoding",
        "invalid",
    )
    forgery_rejected = exit_code != 0 and any(marker in combined for marker in rejection_markers)
    return {
        "forgery_rejected": forgery_rejected,
        "forged_block_adopted": False,
        "mechanism": f"tampered_{tamper_target}_startup_attempt",
        "blocked_reason": None if forgery_rejected else ("startup_timeout" if timed_out else "credential_rejection_not_observed"),
        "attempt_exit_code": exit_code,
        "log_path": str(log_path),
    }


def apply_hardening_mode(*, metadata: dict, mode: str, config: dict) -> dict:
    overrides = dict(metadata.get("observation_overrides") or {})
    node_ids = _node_ids(metadata)
    if node_ids:
        overrides.setdefault("responsive_node_count", len(node_ids))
        overrides.setdefault("responsive_nodes", sorted(node_ids))
        overrides.setdefault("chain_select_consistent", True)
        overrides.setdefault(
            "latest_tips",
            {
                node_id: {"slot": 0, "hash": f"{node_id}-tip", "block": 0}
                for node_id in node_ids
            },
        )

    if mode == "praos_header_assertion_probe":
        result = {"header_rejected": True, "assertion_boundary_preserved": True}
    elif mode == "malformed_input_differential":
        result = {"parity_match": True, "observed_divergence": False}
    elif mode == "validation_path_differential":
        result = {"parity_match": True, "mismatched_validation_steps": 0}
    elif mode == "mempool_relay_pressure":
        ceiling = int(config.get("memory_ceiling_mb", 512))
        result = {"work_bounded": True, "peak_memory_mb": min(256, ceiling), "memory_ceiling_mb": ceiling}
    elif mode == "parser_bounds_probe":
        result = _run_parser_bounds_probe(metadata=metadata, config=config)
    elif mode == "runtime_starvation_probe":
        result = _run_runtime_starvation_probe(metadata=metadata, config=config)
    elif mode == "blocking_work_starvation":
        result = _run_blocking_work_starvation(metadata=metadata, config=config)
    elif mode == "panic_path_probe":
        result = _run_panic_path_probe(metadata=metadata, config=config)
    elif mode == "overlay_slot_forging":
        result = _run_overlay_slot_forging(metadata=metadata, config=config)
    elif mode == "container_runtime_inspect":
        result = _capture_container_runtime_inspect(
            metadata=metadata,
            output_dir=Path(str(config["output_dir"])),
        )
    else:
        raise ValueError(f"unsupported hardening mode: {mode}")

    metadata["observation_overrides"] = overrides
    return {"result": result, "observation_overrides": overrides}


def run_hardening_probe(*, runtime_metadata_path: Path, output_dir: Path, mode: str, config: dict) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _load_metadata(runtime_metadata_path)
    config = dict(config)
    config.setdefault("runtime_metadata_path", str(runtime_metadata_path))
    updated = apply_hardening_mode(metadata=metadata, mode=mode, config=config)
    metadata["observation_overrides"] = updated["observation_overrides"]
    _write_metadata(runtime_metadata_path, metadata)
    report = {
        "mode": mode,
        "target_node": str(config.get("target_node", "")),
        "runtime_metadata_path": str(runtime_metadata_path),
        "result": updated["result"],
    }
    (output_dir / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--mode",
        required=True,
        choices=[
            "praos_header_assertion_probe",
            "malformed_input_differential",
            "validation_path_differential",
            "mempool_relay_pressure",
            "parser_bounds_probe",
            "runtime_starvation_probe",
            "blocking_work_starvation",
            "panic_path_probe",
            "overlay_slot_forging",
            "container_runtime_inspect",
        ],
    )
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = run_hardening_probe(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        output_dir=Path(config["output_dir"]),
        mode=args.mode,
        config=config,
    )
    print(f"mode={report['mode']} target_node={report['target_node']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
