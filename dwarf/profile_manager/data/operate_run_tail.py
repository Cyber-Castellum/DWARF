"""Live-tail SSE for an in-flight scenario run.

Streams new lines from ``runs/<id>/log.ndjson`` as Server-Sent Events,
matching the existing /api/scenario/run streaming pattern. Closes the
stream when the run's manifest.json materialises (the framework writes
manifest.json only after every phase completes — so its presence is
the natural "run is done" signal).

Read-only endpoint: no token required, no mutation.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterator


def _runs_dir() -> Path:
    env = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "runs"


def _safe_run_id(run_id: str) -> bool:
    return bool(run_id) and "/" not in run_id and ".." not in run_id


def _format_event(name: str, data: str) -> bytes:
    """SSE record. Multi-line data values must prefix every line with
    ``data:``; single-line is the common case."""
    lines = [f"event: {name}"]
    for chunk in data.split("\n"):
        lines.append(f"data: {chunk}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def stream_run_tail(
    run_id: str,
    *,
    runs_dir: Path | None = None,
    poll_seconds: float = 0.5,
    max_idle_seconds: float = 600.0,
) -> Iterator[bytes]:
    """Yield SSE chunks for the named run's log.ndjson.

    First emits a ``hello`` event with the run-id; then every existing
    log line as a ``log`` event so the page bootstraps with whatever is
    already on disk; then enters a tail loop. The loop exits when:

    - manifest.json appears (run finished — emits ``end`` event with
      reason="manifest"),
    - the consumer disconnects (BrokenPipeError handled by _send_stream),
    - or ``max_idle_seconds`` elapses without new bytes (emits
      ``end`` with reason="idle_timeout").
    """
    if not _safe_run_id(run_id):
        yield _format_event("error", "invalid run-id")
        return
    base = Path(runs_dir) if runs_dir is not None else _runs_dir()
    run_dir = base / run_id
    log_path = run_dir / "log.ndjson"
    manifest_path = run_dir / "manifest.json"

    yield _format_event("hello", run_id)

    # Read whatever's already there (so a reload picks up history) and
    # then start tailing from the end.
    last_size = 0
    if log_path.is_file():
        try:
            with log_path.open("r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.rstrip("\n")
                    if line:
                        yield _format_event("log", line)
                last_size = fp.tell()
        except OSError as exc:
            yield _format_event("error", f"read error: {exc}")
            return

    if manifest_path.is_file():
        yield _format_event("end", "manifest")
        return

    last_activity = time.monotonic()
    while True:
        # Watch for manifest BEFORE reading more log lines so a
        # newly-completed run terminates promptly.
        if manifest_path.is_file():
            yield _format_event("end", "manifest")
            return

        if log_path.is_file():
            try:
                size = log_path.stat().st_size
            except OSError:
                size = last_size
            if size > last_size:
                with log_path.open("r", encoding="utf-8") as fp:
                    fp.seek(last_size)
                    chunk = fp.read()
                    last_size = fp.tell()
                for line in chunk.split("\n"):
                    line = line.rstrip("\r")
                    if line:
                        yield _format_event("log", line)
                last_activity = time.monotonic()
            elif size < last_size:
                # Log was truncated/rotated — replay from start.
                last_size = 0
                continue

        if (time.monotonic() - last_activity) >= max_idle_seconds:
            yield _format_event("end", "idle_timeout")
            return

        # Heartbeat keeps proxies (Caddy / nginx) from closing the
        # connection on idle. Sent as an SSE comment so consumer
        # event listeners ignore it.
        yield b": ping\n\n"

        time.sleep(poll_seconds)
