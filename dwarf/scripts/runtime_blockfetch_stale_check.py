#!/usr/bin/env python3

import pathlib
import socket
import subprocess
import sys
import tempfile
import time

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_common import PROFILE_A_CONFIG, derive_chainsync_point, point_slot  # noqa: E402
from runtime_telemetry import emit_runtime_metric, emit_target_event  # noqa: E402


SERVER_BIN = "${HOME}/dwarf-fw/targets/amaru/target/release/dwarf-amaru-runtime-blockfetch-stale-server"
CLIENT_BIN = "${HOME}/dwarf-fw/targets/amaru/target/release/dwarf-amaru-runtime-blockfetch"


def allocate_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _emit_client_metrics(*, point: str, server_port: int, client: subprocess.CompletedProcess[str], elapsed_ms: float) -> None:
    point_slot_value = point_slot(point)
    payload = {
        "point": point,
        "server_port": server_port,
        "client_exit": client.returncode,
        "elapsed_ms": elapsed_ms,
        "stdout_bytes": len(client.stdout.encode("utf-8")) if client.stdout else 0,
        "stderr_bytes": len(client.stderr.encode("utf-8")) if client.stderr else 0,
    }
    if point_slot_value is not None:
        payload["point_slot"] = point_slot_value
        emit_runtime_metric("stale_check_point_slot", value=point_slot_value, meta={"kind": "point_slot"})
    emit_target_event(
        primitive="runtime_blockfetch_stale_check",
        event="client_result",
        payload=payload,
        level="info" if client.returncode == 1 else "warn",
    )
    emit_runtime_metric("stale_client_exit_code", value=client.returncode, meta={"expected": 1})
    emit_runtime_metric("stale_client_elapsed_ms", value=elapsed_ms, meta={"expected": 1})
    emit_runtime_metric("stale_client_stdout_bytes", value=payload["stdout_bytes"], meta={"kind": "stdout"})
    emit_runtime_metric("stale_client_stderr_bytes", value=payload["stderr_bytes"], meta={"kind": "stderr"})


def _emit_server_log_metrics(server_log_path: pathlib.Path) -> str:
    text = server_log_path.read_text(encoding="utf-8", errors="replace")
    line_count = len(text.splitlines())
    byte_count = server_log_path.stat().st_size
    emit_target_event(
        primitive="runtime_blockfetch_stale_check",
        event="server_log_observed",
        payload={"path": str(server_log_path), "bytes": byte_count, "lines": line_count},
    )
    emit_runtime_metric("stale_server_log_bytes", value=byte_count, meta={"path": str(server_log_path)})
    emit_runtime_metric("stale_server_log_lines", value=line_count, meta={"path": str(server_log_path)})
    return text


def main() -> int:
    point = derive_chainsync_point(PROFILE_A_CONFIG)
    point_slot_value = point_slot(point)
    emit_target_event(
        primitive="runtime_blockfetch_stale_check",
        event="stale_check_started",
        payload={"point": point, "point_slot": point_slot_value},
    )
    emit_runtime_metric("stale_check_point_length", value=len(point), meta={"kind": "point"})
    if point_slot_value is not None:
        emit_runtime_metric("stale_check_point_slot", value=point_slot_value, meta={"kind": "point_slot"})
    server_port = allocate_port()
    emit_runtime_metric("stale_server_port", value=server_port, meta={"host": "127.0.0.1"})
    server_log_handle = tempfile.NamedTemporaryFile(prefix="dwarf-stale-server-", delete=False)
    server_log_path = pathlib.Path(server_log_handle.name)
    server_log_handle.close()
    server = None
    try:
        with server_log_path.open("wb") as server_log:
            server = subprocess.Popen(
                [SERVER_BIN, f"127.0.0.1:{server_port}", PROFILE_A_CONFIG.network_magic],
                stdout=server_log,
                stderr=subprocess.STDOUT,
            )
        time.sleep(1)
        started_at = time.monotonic()
        client = subprocess.run(
            [
                CLIENT_BIN,
                f"127.0.0.1:{server_port}",
                PROFILE_A_CONFIG.network_magic,
                point,
                point,
                "1",
                point,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        _emit_client_metrics(point=point, server_port=server_port, client=client, elapsed_ms=elapsed_ms)
        print(f"point={point} server_port={server_port} client_exit={client.returncode} expected_mismatch=1")
        if client.stdout:
            print(client.stdout, end="")
        if client.stderr:
            print(client.stderr, end="")
        if server_log_path.exists():
            print(_emit_server_log_metrics(server_log_path), end="")
        if client.returncode == 1:
            emit_target_event(
                primitive="runtime_blockfetch_stale_check",
                event="stale_check_completed",
                payload={"result": "expected_mismatch_observed", "server_port": server_port},
            )
            return 0
        if client.returncode == 0:
            emit_target_event(
                primitive="runtime_blockfetch_stale_check",
                event="stale_check_completed",
                payload={"result": "unexpected_success", "server_port": server_port},
                level="error",
            )
            print(f"unexpected_success point={point} server_port={server_port}")
            return 9
        emit_target_event(
            primitive="runtime_blockfetch_stale_check",
            event="stale_check_completed",
            payload={"result": "unexpected_exit", "server_port": server_port, "client_exit": client.returncode},
            level="error",
        )
        return 1
    except subprocess.TimeoutExpired:
        emit_target_event(
            primitive="runtime_blockfetch_stale_check",
            event="client_timeout",
            payload={"point": point, "server_port": server_port},
            level="error",
        )
        print(f"point={point} server_port={server_port} client_exit=timeout expected_mismatch=1")
        if server_log_path.exists():
            print(_emit_server_log_metrics(server_log_path), end="")
        return 1
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=3)
        server_log_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
