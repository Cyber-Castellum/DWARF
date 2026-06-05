from __future__ import annotations

import argparse
import json
import random
import signal
import socket
import threading
import time
from pathlib import Path


class ProxyStats:
    def __init__(
        self,
        *,
        output_dir: Path,
        mode: str,
        target_rate_bytes_per_sec: int = 0,
        latency_ms: int = 0,
        jitter_ms: int = 0,
        loss_percent: int = 0,
        partition: bool = False,
    ):
        self.output_dir = output_dir
        self.mode = mode
        self.target_rate_bytes_per_sec = int(target_rate_bytes_per_sec)
        self.latency_ms = int(latency_ms)
        self.jitter_ms = int(jitter_ms)
        self.loss_percent = int(loss_percent)
        self.partition = bool(partition)
        self.started_at = time.time()
        self.lock = threading.Lock()
        self.connections_seen = 0
        self.client_to_server_bytes = 0
        self.server_to_client_bytes = 0
        self.throttled_chunks = 0
        self.dropped_chunks = 0
        self.max_observed_delay_ms = 0.0
        self.stats_path = output_dir / "proxy-stats.json"
        self.result_path = output_dir / "result.json"

    def record_connection(self) -> None:
        with self.lock:
            self.connections_seen += 1
            self._write_locked()

    def record_bytes(self, *, direction: str, count: int, delay_seconds: float) -> None:
        with self.lock:
            if direction == "client_to_server":
                self.client_to_server_bytes += count
            else:
                self.server_to_client_bytes += count
            if delay_seconds > 0:
                self.throttled_chunks += 1
                self.max_observed_delay_ms = max(self.max_observed_delay_ms, delay_seconds * 1000.0)
            self._write_locked()

    def record_drop(self) -> None:
        with self.lock:
            self.dropped_chunks += 1
            self._write_locked()

    def snapshot(self) -> dict:
        elapsed = max(0.001, time.time() - self.started_at)
        total = self.client_to_server_bytes + self.server_to_client_bytes
        return {
            "mode": self.mode,
            "started_at": self.started_at,
            "elapsed_seconds": elapsed,
            "connections_seen": self.connections_seen,
            "client_to_server_bytes": self.client_to_server_bytes,
            "server_to_client_bytes": self.server_to_client_bytes,
            "bytes_forwarded": total,
            "throttled_chunks": self.throttled_chunks,
            "dropped_chunks": self.dropped_chunks,
            "max_observed_delay_ms": round(self.max_observed_delay_ms, 3),
        }

    def _write_locked(self) -> None:
        snapshot = self.snapshot()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.stats_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if self.mode == "bandwidth_throttle":
            result_body = {
                "throttle_applied": True,
                "observed_throughput_bytes_per_sec": int(snapshot["bytes_forwarded"] / snapshot["elapsed_seconds"]),
                "target_rate_bytes_per_sec": self.target_rate_bytes_per_sec,
                "connections_seen": int(snapshot["connections_seen"]),
            }
        elif self.mode == "slow_loris_chainsync":
            result_body = {
                "slow_loris_active_seconds": round(snapshot["elapsed_seconds"], 3),
                "responses_throttled": int(snapshot["throttled_chunks"]),
                "max_response_delay_ms": round(snapshot["max_observed_delay_ms"], 3),
                "connections_seen": int(snapshot["connections_seen"]),
            }
        else:
            result_body = {
                "impairment_applied": True,
                "latency_ms": self.latency_ms,
                "jitter_ms": self.jitter_ms,
                "loss_percent": self.loss_percent,
                "partition": self.partition,
                "dropped_chunks": int(snapshot["dropped_chunks"]),
                "max_observed_delay_ms": round(snapshot["max_observed_delay_ms"], 3),
                "connections_seen": int(snapshot["connections_seen"]),
            }
        result = {"mode": self.mode, "result": result_body}
        self.result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _forward(
    source: socket.socket,
    destination: socket.socket,
    *,
    direction: str,
    stats: ProxyStats,
    stop_event: threading.Event,
    bytes_per_second: int | None,
    grace_bytes: int,
    chunk_size: int,
    delay_ms: int,
    jitter_ms: int,
    loss_percent: int,
    partition: bool,
) -> None:
    seen = 0
    try:
        while not stop_event.is_set():
            payload = source.recv(chunk_size)
            if not payload:
                break
            seen += len(payload)
            if partition:
                stats.record_drop()
                time.sleep(0.1)
                continue
            if loss_percent > 0 and random.uniform(0.0, 100.0) < float(loss_percent):
                stats.record_drop()
                continue
            delay_seconds = 0.0
            if delay_ms > 0:
                if jitter_ms > 0:
                    lower = max(0.0, float(delay_ms - jitter_ms))
                    upper = float(delay_ms + jitter_ms)
                    delay_seconds += random.uniform(lower, upper) / 1000.0
                else:
                    delay_seconds += float(delay_ms) / 1000.0
            if bytes_per_second and seen > grace_bytes:
                delay_seconds += len(payload) / float(bytes_per_second)
            if delay_seconds > 0:
                time.sleep(delay_seconds)
            destination.sendall(payload)
            stats.record_bytes(direction=direction, count=len(payload), delay_seconds=delay_seconds)
    except OSError:
        pass
    finally:
        stop_event.set()
        try:
            destination.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def run_proxy(
    *,
    listen_host: str,
    listen_port: int,
    upstream_host: str,
    upstream_port: int,
    output_dir: Path,
    mode: str,
    upstream_bytes_per_second: int | None,
    downstream_bytes_per_second: int | None,
    grace_bytes: int,
    chunk_size: int,
    target_rate_bytes_per_sec: int,
    latency_ms: int,
    jitter_ms: int,
    loss_percent: int,
    partition: bool,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = ProxyStats(
        output_dir=output_dir,
        mode=mode,
        target_rate_bytes_per_sec=target_rate_bytes_per_sec,
        latency_ms=latency_ms,
        jitter_ms=jitter_ms,
        loss_percent=loss_percent,
        partition=partition,
    )
    stop_event = threading.Event()

    def _stop(_signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((listen_host, listen_port))
        server.listen(16)
        server.settimeout(1.0)
        while not stop_event.is_set():
            try:
                client, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            stats.record_connection()
            try:
                upstream = socket.create_connection((upstream_host, upstream_port), timeout=10.0)
            except OSError:
                client.close()
                continue
            connection_stop = threading.Event()
            threads = [
                threading.Thread(
                    target=_forward,
                    args=(client, upstream),
                    kwargs={
                        "direction": "client_to_server",
                        "stats": stats,
                        "stop_event": connection_stop,
                        "bytes_per_second": upstream_bytes_per_second,
                        "grace_bytes": grace_bytes,
                        "chunk_size": chunk_size,
                        "delay_ms": latency_ms,
                        "jitter_ms": jitter_ms,
                        "loss_percent": loss_percent,
                        "partition": partition,
                    },
                    daemon=True,
                ),
                threading.Thread(
                    target=_forward,
                    args=(upstream, client),
                    kwargs={
                        "direction": "server_to_client",
                        "stats": stats,
                        "stop_event": connection_stop,
                        "bytes_per_second": downstream_bytes_per_second,
                        "grace_bytes": grace_bytes,
                        "chunk_size": chunk_size,
                        "delay_ms": latency_ms,
                        "jitter_ms": jitter_ms,
                        "loss_percent": loss_percent,
                        "partition": partition,
                    },
                    daemon=True,
                ),
            ]
            for thread in threads:
                thread.start()
            while not connection_stop.is_set() and not stop_event.is_set():
                time.sleep(0.1)
            try:
                client.close()
            except OSError:
                pass
            try:
                upstream.close()
            except OSError:
                pass
    stats._write_locked()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", required=True)
    parser.add_argument("--listen-port", required=True, type=int)
    parser.add_argument("--upstream-address", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--mode", choices=("bandwidth_throttle", "slow_loris_chainsync", "network_impairment"), required=True)
    parser.add_argument("--upstream-bytes-per-second", type=int, default=0)
    parser.add_argument("--downstream-bytes-per-second", type=int, default=0)
    parser.add_argument("--grace-bytes", type=int, default=4096)
    parser.add_argument("--chunk-size", type=int, default=1024)
    parser.add_argument("--target-rate-bytes-per-second", type=int, default=0)
    parser.add_argument("--latency-ms", type=int, default=0)
    parser.add_argument("--jitter-ms", type=int, default=0)
    parser.add_argument("--loss-percent", type=int, default=0)
    parser.add_argument("--partition", action="store_true")
    args = parser.parse_args(argv)
    upstream_host, upstream_port_text = str(args.upstream_address).rsplit(":", 1)
    return run_proxy(
        listen_host=str(args.listen_host),
        listen_port=int(args.listen_port),
        upstream_host=upstream_host,
        upstream_port=int(upstream_port_text),
        output_dir=Path(args.output_dir),
        mode=str(args.mode),
        upstream_bytes_per_second=int(args.upstream_bytes_per_second) or None,
        downstream_bytes_per_second=int(args.downstream_bytes_per_second) or None,
        grace_bytes=max(0, int(args.grace_bytes)),
        chunk_size=max(1, int(args.chunk_size)),
        target_rate_bytes_per_sec=int(args.target_rate_bytes_per_second),
        latency_ms=max(0, int(args.latency_ms)),
        jitter_ms=max(0, int(args.jitter_ms)),
        loss_percent=max(0, int(args.loss_percent)),
        partition=bool(args.partition),
    )


if __name__ == "__main__":
    raise SystemExit(main())
