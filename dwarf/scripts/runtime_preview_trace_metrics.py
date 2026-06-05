#!/usr/bin/env python3

import re
from datetime import datetime
from pathlib import Path


ADOPTED_TIP_RE = re.compile(r"adopted tip .*?tip\.slot=(\d+)")
CARDANO_ADOPTED_TIP_RE = re.compile(r'"ns":"ChainDB\.AddBlockEvent\.AddedToCurrentChain".*?"newtip":"[^"@]+@(\d+)"')
CARDANO_HANDSHAKE_SUCCESS_RE = re.compile(r"Net\.ConnectionManager\.Remote\.ConnectionHandler\.HandshakeSuccess|TrHandshakeSuccess")
CARDANO_CONNECTION_DIED_RE = re.compile(r"Net\.ConnectionManager\.Remote\.ConnectionHandler\.Error|TrConnectionHandlerError.*BearerClosed")
AMARU_LINE_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)")
CARDANO_LINE_TS_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\]")


def read_log_window(log_path: Path, start_offset: int, end_offset: int) -> str:
    size = max(0, end_offset - start_offset)
    if size <= 0:
        return ""
    with log_path.open("rb") as handle:
        handle.seek(start_offset)
        payload = handle.read(size)
    return payload.decode("utf-8", errors="replace")


def _parse_line_epoch_ms(line: str, target_implementation: str) -> int | None:
    if target_implementation == "amaru":
        match = AMARU_LINE_TS_RE.search(line)
        if not match:
            return None
        return int(datetime.fromisoformat(match.group(1).replace("Z", "+00:00")).timestamp() * 1000)
    if target_implementation == "cardano-node":
        match = CARDANO_LINE_TS_RE.search(line)
        if not match:
            return None
        return int(datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S.%fZ").replace(tzinfo=datetime.UTC).timestamp() * 1000)
    raise ValueError(f"unsupported target implementation: {target_implementation!r}")


def read_timestamp_window(log_path: Path, start_epoch_ms: int, end_epoch_ms: int, target_implementation: str) -> str:
    matches = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        ts = _parse_line_epoch_ms(line, target_implementation)
        if ts is None:
            continue
        if start_epoch_ms <= ts <= end_epoch_ms:
            matches.append(line)
    return "\n".join(matches) + ("\n" if matches else "")


def collect_amaru_trace_metrics(log_window: str) -> dict[str, int]:
    adopted_tip_count = 0
    peer_connected_count = 0
    peer_connection_died_count = 0
    first_tip_slot = None
    last_tip_slot = None

    for line in log_window.splitlines():
        match = ADOPTED_TIP_RE.search(line)
        if match:
            adopted_tip_count += 1
            slot = int(match.group(1))
            if first_tip_slot is None:
                first_tip_slot = slot
            last_tip_slot = slot
        if "connected to peer" in line:
            peer_connected_count += 1
        if "connection died" in line:
            peer_connection_died_count += 1

    tip_slot_delta = 0
    if first_tip_slot is not None and last_tip_slot is not None:
        tip_slot_delta = max(0, last_tip_slot - first_tip_slot)

    return {
        "adopted_tip_count": adopted_tip_count,
        "tip_slot_delta": tip_slot_delta,
        "peer_connected_count": peer_connected_count,
        "peer_connection_died_count": peer_connection_died_count,
    }


def collect_cardano_trace_metrics(log_window: str) -> dict[str, int]:
    adopted_tip_count = 0
    peer_connected_count = 0
    peer_connection_died_count = 0
    first_tip_slot = None
    last_tip_slot = None

    for line in log_window.splitlines():
        if CARDANO_HANDSHAKE_SUCCESS_RE.search(line):
            peer_connected_count += 1
        if CARDANO_CONNECTION_DIED_RE.search(line):
            peer_connection_died_count += 1
        match = CARDANO_ADOPTED_TIP_RE.search(line)
        if not match:
            continue
        adopted_tip_count += 1
        slot = int(match.group(1))
        if first_tip_slot is None:
            first_tip_slot = slot
        last_tip_slot = slot

    tip_slot_delta = 0
    if first_tip_slot is not None and last_tip_slot is not None:
        tip_slot_delta = max(0, last_tip_slot - first_tip_slot)

    return {
        "adopted_tip_count": adopted_tip_count,
        "tip_slot_delta": tip_slot_delta,
        "peer_connected_count": peer_connected_count,
        "peer_connection_died_count": peer_connection_died_count,
    }
