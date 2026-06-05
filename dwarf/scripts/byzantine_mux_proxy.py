from __future__ import annotations

import argparse
import json
import selectors
import signal
import socket
import socketserver
import threading
import time
from pathlib import Path


HEADER_LENGTH = 8
RESPONDER_PROTOCOL_BIT = 0x8000
CHAINSYNC_PROTOCOL_ID = 2
BLOCKFETCH_PROTOCOL_ID = 3


def parse_mux_segment_header(header: bytes) -> dict[str, int]:
    if len(header) != HEADER_LENGTH:
        raise ValueError(f"expected {HEADER_LENGTH} header bytes, got {len(header)}")
    timestamp = int.from_bytes(header[0:4], "big")
    protocol_word = int.from_bytes(header[4:6], "big")
    payload_length = int.from_bytes(header[6:8], "big")
    return {
        "timestamp": timestamp,
        "protocol": _normalize_protocol_id(protocol_word),
        "mini_protocol_num": _normalize_protocol_id(protocol_word),
        "initiator": 0 if (protocol_word & RESPONDER_PROTOCOL_BIT) else 1,
        "raw_protocol": protocol_word,
        "payload_length": payload_length,
        "segment_length": HEADER_LENGTH + payload_length,
    }


def _normalize_protocol_id(protocol: int) -> int:
    return int(protocol) & ~RESPONDER_PROTOCOL_BIT


def _read_cbor_head(data: bytes, offset: int = 0) -> tuple[int, int, int] | None:
    if offset >= len(data):
        return None
    initial = data[offset]
    major = initial >> 5
    additional = initial & 0x1F
    if additional < 24:
        return major, additional, offset + 1
    if additional == 24:
        if offset + 1 >= len(data):
            return None
        return major, data[offset + 1], offset + 2
    if additional == 25:
        if offset + 2 >= len(data):
            return None
        return major, int.from_bytes(data[offset + 1 : offset + 3], "big"), offset + 3
    return None


def classify_segment_message(*, protocol: int, payload: bytes) -> str | None:
    if _normalize_protocol_id(protocol) == CHAINSYNC_PROTOCOL_ID:
        head = _read_cbor_head(payload, 0)
        if head is None:
            return None
        major, length, cursor = head
        if major != 4:
            return None
        head = _read_cbor_head(payload, cursor)
        if head is None:
            return None
        key_major, key, _cursor = head
        if key_major != 0:
            return None
        mapping = {
            (1, 0): "request_next",
            (1, 1): "await_reply",
            (3, 2): "roll_forward",
            (3, 3): "roll_backward",
            (2, 4): "find_intersect",
            (3, 5): "intersect_found",
            (2, 6): "intersect_not_found",
            (1, 7): "done",
        }
        return mapping.get((length, key))
    if _normalize_protocol_id(protocol) != BLOCKFETCH_PROTOCOL_ID:
        return None
    head = _read_cbor_head(payload, 0)
    if head is None:
        return None
    major, length, cursor = head
    if major != 4:
        return None
    head = _read_cbor_head(payload, cursor)
    if head is None:
        return None
    key_major, key, _cursor = head
    if key_major != 0:
        return None
    mapping = {
        (3, 0): "request_range",
        (1, 1): "client_done",
        (1, 2): "start_batch",
        (1, 3): "no_blocks",
        (2, 4): "block",
        (1, 5): "batch_done",
    }
    return mapping.get((length, key))


class ProxyState:
    def __init__(
        self,
        *,
        upstream_host: str,
        upstream_port: int,
        output_dir: Path,
        mutation_mode: str,
        mutation_direction: str,
        mutation_protocol: str,
        mutate_after_segments: int,
    ) -> None:
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.output_dir = output_dir
        self.mutation_mode = mutation_mode
        self.mutation_direction = mutation_direction
        self.mutation_protocol = mutation_protocol
        self.mutate_after_segments = mutate_after_segments
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.started_at = time.time()
        self.stats = {
            "intercepted_segments": 0,
            "mutated_segments": 0,
            "client_to_server_segments": 0,
            "server_to_client_segments": 0,
            "connections_seen": 0,
            "chainsync_messages_observed": 0,
            "chainsync_roll_backward_count": 0,
            "chainsync_roll_forward_count": 0,
            "rollback_then_forward_count": 0,
            "blockfetch_messages_observed": 0,
            "block_range_requests_observed": 0,
            "blocks_fetched": 0,
            "mutation_mode": mutation_mode,
            "mutation_direction": mutation_direction,
            "mutation_protocol": mutation_protocol,
            "mutate_after_segments": mutate_after_segments,
        }
        self.events_path = output_dir / "proxy-events.ndjson"
        self.stats_path = output_dir / "proxy-stats.json"
        self._pending_inbound_chainsync_rollbacks = 0
        self._chainsync_fork_switch_saw_find_intersect = False
        self._chainsync_fork_switch_saw_intersect_found = False
        self._chainsync_fork_switch_mutate_next_inbound = False
        self._chainsync_fork_switch_triggered = False

    def note_connection(self) -> None:
        with self.lock:
            self.stats["connections_seen"] += 1
            self._write_stats_locked()

    def note_segment(self, *, direction: str, protocol: int, payload_length: int, mutated: bool) -> None:
        with self.lock:
            self.stats["intercepted_segments"] += 1
            key = "client_to_server_segments" if direction == "outbound" else "server_to_client_segments"
            self.stats[key] += 1
            if mutated:
                self.stats["mutated_segments"] += 1
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "ts": time.time(),
                            "direction": direction,
                            "protocol": protocol,
                            "payload_length": payload_length,
                            "mutated": mutated,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            self._write_stats_locked()

    def note_segment_message(
        self,
        *,
        direction: str,
        protocol: int,
        payload_length: int,
        mutated: bool,
        message_kind: str | None,
        original_message_kind: str | None = None,
    ) -> None:
        with self.lock:
            self.stats["intercepted_segments"] += 1
            key = "client_to_server_segments" if direction == "outbound" else "server_to_client_segments"
            self.stats[key] += 1
            if mutated:
                self.stats["mutated_segments"] += 1
            if message_kind is not None:
                normalized_protocol = _normalize_protocol_id(protocol)
                if normalized_protocol == CHAINSYNC_PROTOCOL_ID:
                    self.stats["chainsync_messages_observed"] += 1
                    if message_kind == "roll_backward":
                        self.stats["chainsync_roll_backward_count"] += 1
                        if direction == "inbound":
                            self._pending_inbound_chainsync_rollbacks += 1
                    elif message_kind == "roll_forward":
                        self.stats["chainsync_roll_forward_count"] += 1
                        if direction == "inbound" and self._pending_inbound_chainsync_rollbacks > 0:
                            self.stats["rollback_then_forward_count"] += 1
                            self._pending_inbound_chainsync_rollbacks -= 1
                elif normalized_protocol == BLOCKFETCH_PROTOCOL_ID:
                    self.stats["blockfetch_messages_observed"] += 1
                    if message_kind == "request_range":
                        self.stats["block_range_requests_observed"] += 1
                    elif message_kind == "block":
                        self.stats["blocks_fetched"] += 1
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            event = {
                "ts": time.time(),
                "direction": direction,
                "protocol": protocol,
                "normalized_protocol": _normalize_protocol_id(protocol),
                "mini_protocol_num": _normalize_protocol_id(protocol),
                "initiator": 0 if (protocol & RESPONDER_PROTOCOL_BIT) else 1,
                "payload_length": payload_length,
                "mutated": mutated,
            }
            if message_kind is not None:
                event["message_kind"] = message_kind
            if original_message_kind is not None:
                event["original_message_kind"] = original_message_kind
            with self.events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, sort_keys=True) + "\n")
            self._write_stats_locked()

    def _observe_chainsync_fork_switch(self, *, direction: str, protocol: int, message_kind: str | None) -> None:
        if self.mutation_mode != "chainsync_fork_switch_once":
            return
        if _normalize_protocol_id(protocol) != CHAINSYNC_PROTOCOL_ID:
            return
        if self._chainsync_fork_switch_triggered:
            return
        if direction == "outbound" and message_kind == "find_intersect":
            self._chainsync_fork_switch_saw_find_intersect = True
            return
        if (
            direction == "inbound"
            and message_kind == "intersect_found"
            and self._chainsync_fork_switch_saw_find_intersect
        ):
            self._chainsync_fork_switch_saw_intersect_found = True
            return
        if (
            direction == "outbound"
            and message_kind == "request_next"
            and self._chainsync_fork_switch_saw_intersect_found
        ):
            self._chainsync_fork_switch_mutate_next_inbound = True

    def should_mutate(self, *, direction: str, protocol: int, message_kind: str | None = None) -> bool:
        if self.mutation_mode == "pass_through":
            return False
        if self.mutation_direction not in {direction, "both"}:
            return False
        if self.mutation_protocol != "any" and int(self.mutation_protocol) != protocol:
            return False
        if self.mutation_mode == "chainsync_fork_switch_once":
            if _normalize_protocol_id(protocol) != CHAINSYNC_PROTOCOL_ID:
                return False
            if direction != "inbound":
                return False
            if self._chainsync_fork_switch_triggered:
                return False
            if self._chainsync_fork_switch_mutate_next_inbound:
                self._chainsync_fork_switch_mutate_next_inbound = False
                self._chainsync_fork_switch_triggered = True
                return True
            return False
        return (self.stats["intercepted_segments"] + 1) >= self.mutate_after_segments

    def stop(self) -> None:
        with self.lock:
            self.stop_event.set()
            self._write_stats_locked()

    def _write_stats_locked(self) -> None:
        body = dict(self.stats)
        body["uptime_seconds"] = round(time.time() - self.started_at, 3)
        self.stats_path.parent.mkdir(parents=True, exist_ok=True)
        self.stats_path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _mutate_segment(segment: bytes, *, state: ProxyState, direction: str) -> tuple[bytes, bool]:
    header = parse_mux_segment_header(segment[:HEADER_LENGTH])
    original_payload = segment[HEADER_LENGTH:]
    original_message_kind = classify_segment_message(protocol=header["protocol"], payload=original_payload)
    state._observe_chainsync_fork_switch(
        direction=direction,
        protocol=header["protocol"],
        message_kind=original_message_kind,
    )
    mutated = False
    if (
        state.should_mutate(
            direction=direction,
            protocol=header["protocol"],
            message_kind=original_message_kind,
        )
        and header["payload_length"] > 0
    ):
        payload = bytearray(segment[HEADER_LENGTH:])
        payload[-1] ^= 0x01
        segment = segment[:HEADER_LENGTH] + bytes(payload)
        mutated = True
    message_kind = classify_segment_message(protocol=header["protocol"], payload=segment[HEADER_LENGTH:])
    state.note_segment_message(
        direction=direction,
        protocol=header["protocol"],
        payload_length=header["payload_length"],
        mutated=mutated,
        message_kind=message_kind,
        original_message_kind=original_message_kind,
    )
    return segment, mutated


def _relay_segments(source: socket.socket, target: socket.socket, *, state: ProxyState, direction: str) -> None:
    buffer = bytearray()
    while not state.stop_event.is_set():
        chunk = source.recv(65536)
        if not chunk:
            break
        buffer.extend(chunk)
        while len(buffer) >= HEADER_LENGTH:
            header = parse_mux_segment_header(buffer[:HEADER_LENGTH])
            total_length = header["segment_length"]
            if len(buffer) < total_length:
                break
            segment = bytes(buffer[:total_length])
            del buffer[:total_length]
            segment, _ = _mutate_segment(segment, state=state, direction=direction)
            target.sendall(segment)
    if buffer:
        target.sendall(bytes(buffer))


class MuxProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        state: ProxyState = self.server.proxy_state
        state.note_connection()
        with socket.create_connection((state.upstream_host, state.upstream_port), timeout=10.0) as upstream:
            upstream.settimeout(1.0)
            self.request.settimeout(1.0)
            stop = threading.Event()

            def pump(source: socket.socket, target: socket.socket, direction: str) -> None:
                try:
                    _relay_segments(source, target, state=state, direction=direction)
                except (ConnectionError, OSError):
                    pass
                finally:
                    stop.set()
                    try:
                        target.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

            outbound = threading.Thread(target=pump, args=(self.request, upstream, "outbound"), daemon=True)
            inbound = threading.Thread(target=pump, args=(upstream, self.request, "inbound"), daemon=True)
            outbound.start()
            inbound.start()
            while not stop.is_set() and not state.stop_event.is_set():
                time.sleep(0.1)


class ThreadedMuxProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, server_address, request_handler_class, *, proxy_state: ProxyState):
        self.proxy_state = proxy_state
        super().__init__(server_address, request_handler_class)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", required=True, type=int)
    parser.add_argument("--upstream-address", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--mutation-mode", default="flip_payload_byte")
    parser.add_argument("--mutation-direction", default="outbound")
    parser.add_argument("--mutation-protocol", default="any")
    parser.add_argument("--mutate-after-segments", type=int, default=1)
    args = parser.parse_args(argv)

    upstream_host, upstream_port_text = str(args.upstream_address).rsplit(":", 1)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    state = ProxyState(
        upstream_host=upstream_host,
        upstream_port=int(upstream_port_text),
        output_dir=output_dir,
        mutation_mode=str(args.mutation_mode),
        mutation_direction=str(args.mutation_direction),
        mutation_protocol=str(args.mutation_protocol),
        mutate_after_segments=max(1, int(args.mutate_after_segments)),
    )

    def _handle_signal(_signum, _frame):
        state.stop()
        server.shutdown()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    server = ThreadedMuxProxy((args.listen_host, int(args.listen_port)), MuxProxyHandler, proxy_state=state)
    print(f"listening={args.listen_host}:{args.listen_port} upstream={args.upstream_address}", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        state.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
