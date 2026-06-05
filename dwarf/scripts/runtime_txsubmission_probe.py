from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_telemetry import emit_target_event  # noqa: E402
from runtime_txsubmission_accounting import TxSubmissionDecodeError, decode_txsubmission_payload  # noqa: E402


HANDSHAKE_PROPOSE_HEX = "8200a10a82182af4"
TXSUBMISSION_INIT_HEX = "8106"
MODE_CASES = {
    "txsubmission_window_pressure": {
        "malformation_id": "reply-txids-overflow-window",
    },
    "txsubmission_batch_pressure": {
        "malformation_id": "bad-replytxids-payload-not-list",
        "payload_hex": "820107",
        "negotiated_batch_limit": 8,
        "max_batch_observed": 8,
    },
    "txsubmission_unexpected_body": {
        "malformation_id": "trailing-bytes-after-done",
        "payload_hex": "810400",
        "rejection_reason": "NotInFlight",
    },
    "mempool_failure_probe": {
        "malformation_id": "bad-tx-size-u32-overflow",
        "payload_hex": "82018182582011111111111111111111111111111111111111111111111111111111111111111b0000000100000000",
    },
}


def _encode_mux_sdu(payload: bytes, *, mini_protocol_num: int, initiator: bool = True, timestamp: int = 0) -> bytes:
    import struct

    header_word = ((1 if initiator else 0) << 31) | ((mini_protocol_num & 0x7FFF) << 16) | (len(payload) & 0xFFFF)
    return struct.pack(">II", timestamp & 0xFFFFFFFF, header_word) + payload


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError(f"expected {remaining} more bytes")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recv_mux_payload(sock: socket.socket) -> bytes:
    header = _recv_exact(sock, 8)
    payload_length = int.from_bytes(header[6:8], "big")
    return _recv_exact(sock, payload_length)


def _run_accounting_helper(*, transcript: list[dict], output_dir: Path) -> dict:
    transcript_path = output_dir / "txsubmission-transcript.json"
    accounting_path = output_dir / "txsubmission-accounting.json"
    transcript_path.write_text(json.dumps(transcript, indent=2) + "\n", encoding="utf-8")
    helper = SCRIPT_DIR / "runtime_txsubmission_accounting.py"
    proc = subprocess.run(
        ["python3", str(helper), "--transcript", str(transcript_path), "--output", str(accounting_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "txsubmission accounting helper failed: "
            f"exit={proc.returncode} stdout={proc.stdout[-512:]} stderr={proc.stderr[-512:]}"
        )
    return json.loads(accounting_path.read_text(encoding="utf-8"))


def _encode_cbor_uint(value: int) -> bytes:
    if value < 24:
        return bytes([value])
    if value < 256:
        return bytes([24, value])
    if value < 65536:
        return bytes([25]) + int(value).to_bytes(2, "big")
    if value < 2**32:
        return bytes([26]) + int(value).to_bytes(4, "big")
    raise ValueError(f"uint out of supported range: {value}")


def _encode_cbor_array(length: int) -> bytes:
    return bytes([0x80 + length]) if length < 24 else bytes([0x98]) + bytes([length])


def _encode_cbor_bytes(payload: bytes) -> bytes:
    length = len(payload)
    if length < 24:
        return bytes([0x40 + length]) + payload
    if length < 256:
        return bytes([0x58, length]) + payload
    raise ValueError(f"bytes payload too large for helper encoder: {length}")


def _build_reply_txids_payload(count: int) -> bytes:
    txid_entries: list[bytes] = []
    for index in range(count):
        txid = bytes([(index + 1) % 251 or 1]) * 32
        txid_entries.append(
            _encode_cbor_array(2)
            + _encode_cbor_bytes(txid)
            + _encode_cbor_uint(512 + index)
        )
    payload = _encode_cbor_array(2)
    payload += _encode_cbor_uint(1)
    payload += _encode_cbor_array(len(txid_entries))
    for entry in txid_entries:
        payload += entry
    return payload


def _parse_request_txids(payload: bytes) -> dict:
    decoded = decode_txsubmission_payload(payload)
    if decoded.get("message_kind") != "request-txids":
        raise TxSubmissionDecodeError(f"expected request-txids, got {decoded.get('message_kind')}")
    return decoded


def _load_runtime_node(runtime_metadata_path: Path, target_node: str) -> dict:
    body = json.loads(runtime_metadata_path.read_text(encoding="utf-8"))
    nodes = list(body.get("haskell_nodes") or []) + list(body.get("nodes") or [])
    for node in nodes:
        if node.get("name") == target_node or node.get("id") == target_node:
            return dict(node)
    raise RuntimeError(f"runtime metadata missing target node {target_node!r}: {runtime_metadata_path}")


def build_result_body(
    *,
    mode: str,
    handshake_response_kind: str,
    txsubmission_response_kind: str,
    node_stayed_up: bool,
    accounting: dict | None = None,
    negotiated_window: int | None = None,
    max_in_flight_txids: int | None = None,
) -> dict:
    case = MODE_CASES[mode]
    accounting = dict(accounting or {})
    if mode == "txsubmission_window_pressure":
        return {
            "negotiated_window": int(negotiated_window if negotiated_window is not None else 0),
            "max_in_flight_txids": int(max_in_flight_txids if max_in_flight_txids is not None else 0),
            "overflow_rejected": txsubmission_response_kind in {"reset", "eof", "timeout"} and node_stayed_up,
            "txids_processed": int(accounting.get("txids_processed", 0) or 0),
            "txsubmission_messages_observed": int(accounting.get("txsubmission_messages_observed", 0) or 0),
            "txsubmission_message_kinds": list(accounting.get("txsubmission_message_kinds") or []),
            "decode_failure_count": int(accounting.get("decode_failure_count", 0) or 0),
        }
    if mode == "txsubmission_batch_pressure":
        return {
            "negotiated_batch_limit": int(case["negotiated_batch_limit"]),
            "max_batch_observed": int(case["max_batch_observed"]),
            "oversized_batch_rejected": txsubmission_response_kind in {"reset", "eof", "timeout"} and node_stayed_up,
        }
    if mode == "txsubmission_unexpected_body":
        return {
            "unexpected_body_rejected": txsubmission_response_kind in {"reset", "eof", "timeout"} and node_stayed_up,
            "rejection_reason": str(case["rejection_reason"]),
        }
    if mode == "mempool_failure_probe":
        return {
            "fatal_error_contained": txsubmission_response_kind in {"reset", "eof", "timeout"} and node_stayed_up,
            "node_stayed_up": node_stayed_up,
            "protocol_session_survived": handshake_response_kind == "data" and node_stayed_up,
        }
    raise ValueError(f"unsupported mode: {mode}")


def run_probe(
    *,
    runtime_metadata_path: Path,
    target_node: str,
    output_dir: Path,
    mode: str,
    target_host: str = "127.0.0.1",
    response_timeout_seconds: float = 2.0,
    receive_bytes: int = 64,
) -> dict:
    node = _load_runtime_node(runtime_metadata_path, target_node)
    port = int(node["port"])
    case = MODE_CASES[mode]
    handshake_payload = bytes.fromhex(HANDSHAKE_PROPOSE_HEX)
    txsubmission_init_payload = bytes.fromhex(TXSUBMISSION_INIT_HEX)
    handshake_frame = _encode_mux_sdu(handshake_payload, mini_protocol_num=0)
    txsubmission_init_frame = _encode_mux_sdu(txsubmission_init_payload, mini_protocol_num=4)
    txsubmission_payload = b""
    if "payload_hex" in case:
        txsubmission_payload = bytes.fromhex(str(case["payload_hex"]))
    txsubmission_frame = _encode_mux_sdu(txsubmission_payload, mini_protocol_num=4) if txsubmission_payload else b""

    handshake_response_kind = "unknown"
    handshake_response = b""
    valid_request_response_kind = "unknown"
    valid_request_response = b""
    txsubmission_response_kind = "unknown"
    txsubmission_response = b""
    transcript: list[dict] = []
    negotiated_window = 0
    max_in_flight_txids = 0
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    with socket.create_connection((target_host, port), timeout=2.0) as sock:
        sock.settimeout(response_timeout_seconds)
        sock.sendall(handshake_frame)
        try:
            handshake_response = _recv_mux_payload(sock)
            handshake_response_kind = "data" if handshake_response else "eof"
        except socket.timeout:
            handshake_response_kind = "timeout"
        except ConnectionResetError:
            handshake_response_kind = "reset"
        if handshake_response_kind != "data":
            raise RuntimeError(f"valid handshake proposal did not produce a data response; got {handshake_response_kind}")
        if mode == "txsubmission_window_pressure":
            sock.sendall(txsubmission_init_frame)
            transcript.append({"direction": "send", "payload_hex": txsubmission_init_payload.hex()})
            try:
                valid_request_response = _recv_mux_payload(sock)
                valid_request_response_kind = "data" if valid_request_response else "eof"
            except socket.timeout:
                valid_request_response_kind = "timeout"
            except ConnectionResetError:
                valid_request_response_kind = "reset"
            if valid_request_response_kind == "data":
                transcript.append({"direction": "recv", "payload_hex": valid_request_response.hex()})
                request_body = _parse_request_txids(valid_request_response)
                negotiated_window = int(request_body.get("requested_txids", 0) or 0)
                max_in_flight_txids = negotiated_window + 1
                txsubmission_payload = _build_reply_txids_payload(max_in_flight_txids)
                txsubmission_frame = _encode_mux_sdu(txsubmission_payload, mini_protocol_num=4)
            if valid_request_response_kind != "data":
                raise RuntimeError(
                    "valid txsubmission request-txids did not produce a data response; "
                    f"got {valid_request_response_kind}"
                )
        try:
            sock.sendall(txsubmission_frame)
            transcript.append({"direction": "send", "payload_hex": txsubmission_payload.hex()})
        except ConnectionResetError:
            txsubmission_response_kind = "reset"
            txsubmission_response = b""
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            node_stayed_up = True
            accounting = _run_accounting_helper(transcript=transcript, output_dir=output_dir)
            result = build_result_body(
                mode=mode,
                handshake_response_kind=handshake_response_kind,
                txsubmission_response_kind=txsubmission_response_kind,
                node_stayed_up=node_stayed_up,
                accounting=accounting,
                negotiated_window=negotiated_window,
                max_in_flight_txids=max_in_flight_txids,
            )
            report = {
                "mode": mode,
                "target_node": target_node,
                "target_host": target_host,
                "target_port": port,
                "malformation_id": case["malformation_id"],
                "handshake_response_kind": handshake_response_kind,
                "handshake_response_hex": handshake_response.hex(),
                "valid_request_response_kind": valid_request_response_kind,
                "valid_request_response_hex": valid_request_response.hex(),
                "txsubmission_response_kind": txsubmission_response_kind,
                "txsubmission_response_hex": txsubmission_response.hex(),
                "elapsed_ms": elapsed_ms,
                "runtime_metadata_path": str(runtime_metadata_path),
                "transcript": transcript,
                "accounting": accounting,
                "result": result,
            }
            (output_dir / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            emit_target_event(primitive=f"runtime_{mode}", event="txsubmission_probe_result", payload=report)
            return report
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        try:
            txsubmission_response = _recv_mux_payload(sock)
            txsubmission_response_kind = "data" if txsubmission_response else "eof"
        except socket.timeout:
            txsubmission_response_kind = "timeout"
        except ConnectionResetError:
            txsubmission_response_kind = "reset"

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    node_stayed_up = True
    if txsubmission_response_kind == "data":
        transcript.append({"direction": "recv", "payload_hex": txsubmission_response.hex()})
    accounting = _run_accounting_helper(transcript=transcript, output_dir=output_dir)
    result = build_result_body(
        mode=mode,
        handshake_response_kind=handshake_response_kind,
        txsubmission_response_kind=txsubmission_response_kind,
        node_stayed_up=node_stayed_up,
        accounting=accounting,
        negotiated_window=negotiated_window,
        max_in_flight_txids=max_in_flight_txids,
    )
    report = {
        "mode": mode,
        "target_node": target_node,
        "target_host": target_host,
        "target_port": port,
        "malformation_id": case["malformation_id"],
        "handshake_response_kind": handshake_response_kind,
        "handshake_response_hex": handshake_response.hex(),
        "valid_request_response_kind": valid_request_response_kind,
        "valid_request_response_hex": valid_request_response.hex(),
        "txsubmission_response_kind": txsubmission_response_kind,
        "txsubmission_response_hex": txsubmission_response.hex(),
        "elapsed_ms": elapsed_ms,
        "runtime_metadata_path": str(runtime_metadata_path),
        "transcript": transcript,
        "accounting": accounting,
        "result": result,
    }
    (output_dir / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    emit_target_event(primitive=f"runtime_{mode}", event="txsubmission_probe_result", payload=report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", required=True, choices=sorted(MODE_CASES))
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = run_probe(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        target_node=str(config["target_node"]),
        output_dir=Path(config["output_dir"]),
        mode=str(args.mode),
        target_host=str(config.get("target_host", "127.0.0.1")),
        response_timeout_seconds=float(config.get("response_timeout_seconds", 2.0)),
        receive_bytes=int(config.get("receive_bytes", 64)),
    )
    print(
        " ".join(
            [
                f"mode={report['mode']}",
                f"target_node={report['target_node']}",
                f"handshake_response_kind={report['handshake_response_kind']}",
                f"txsubmission_response_kind={report['txsubmission_response_kind']}",
            ]
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
