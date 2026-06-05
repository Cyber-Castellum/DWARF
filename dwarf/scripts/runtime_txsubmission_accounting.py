from __future__ import annotations

import argparse
import json
from pathlib import Path


class TxSubmissionDecodeError(ValueError):
    pass


def _read_uint(data: bytes, index: int, additional: int) -> tuple[int, int]:
    if additional < 24:
        return additional, index
    if additional == 24:
        if index >= len(data):
            raise TxSubmissionDecodeError("truncated uint8")
        return data[index], index + 1
    if additional == 25:
        if index + 2 > len(data):
            raise TxSubmissionDecodeError("truncated uint16")
        return int.from_bytes(data[index : index + 2], "big"), index + 2
    if additional == 26:
        if index + 4 > len(data):
            raise TxSubmissionDecodeError("truncated uint32")
        return int.from_bytes(data[index : index + 4], "big"), index + 4
    if additional == 27:
        if index + 8 > len(data):
            raise TxSubmissionDecodeError("truncated uint64")
        return int.from_bytes(data[index : index + 8], "big"), index + 8
    raise TxSubmissionDecodeError(f"unsupported additional info {additional}")


def _read_item(data: bytes, index: int = 0) -> tuple[object, int]:
    if index >= len(data):
        raise TxSubmissionDecodeError("truncated item")
    head = data[index]
    index += 1
    major = head >> 5
    additional = head & 0x1F
    if major == 0:
        return _read_uint(data, index, additional)
    if major == 2:
        length, index = _read_uint(data, index, additional)
        end = index + length
        if end > len(data):
            raise TxSubmissionDecodeError("truncated bytes")
        return data[index:end], end
    if major == 4:
        length, index = _read_uint(data, index, additional)
        items: list[object] = []
        for _ in range(length):
            item, index = _read_item(data, index)
            items.append(item)
        return items, index
    if major == 7:
        if additional == 20:
            return False, index
        if additional == 21:
            return True, index
        raise TxSubmissionDecodeError(f"unsupported simple value {additional}")
    raise TxSubmissionDecodeError(f"unsupported major type {major}")


def decode_txsubmission_payload(payload: bytes) -> dict:
    term, end = _read_item(payload, 0)
    if end != len(payload):
        raise TxSubmissionDecodeError(f"trailing bytes at position {end}")
    if not isinstance(term, list) or not term:
        raise TxSubmissionDecodeError("expected top-level array")
    key = term[0]
    if not isinstance(key, int):
        raise TxSubmissionDecodeError("message key must be unsigned integer")
    if key == 0:
        if len(term) != 4 or not isinstance(term[1], bool) or not isinstance(term[2], int) or not isinstance(term[3], int):
            raise TxSubmissionDecodeError("request-txids shape invalid")
        return {
            "message_kind": "request-txids",
            "blocking": bool(term[1]),
            "acknowledged_txids": int(term[2]),
            "requested_txids": int(term[3]),
            "txids_processed": 0,
        }
    if key == 1:
        if len(term) != 2 or not isinstance(term[1], list):
            raise TxSubmissionDecodeError("reply-txids shape invalid")
        return {"message_kind": "reply-txids", "txids_processed": len(term[1])}
    if key == 2:
        if len(term) != 2 or not isinstance(term[1], list):
            raise TxSubmissionDecodeError("request-txs shape invalid")
        return {"message_kind": "request-txs", "txids_processed": len(term[1])}
    if key == 3:
        if len(term) != 2 or not isinstance(term[1], list):
            raise TxSubmissionDecodeError("reply-txs shape invalid")
        return {"message_kind": "reply-txs", "txids_processed": len(term[1])}
    if key == 4:
        if len(term) != 1:
            raise TxSubmissionDecodeError("done shape invalid")
        return {"message_kind": "done", "txids_processed": 0}
    if key == 6:
        if len(term) != 1:
            raise TxSubmissionDecodeError("init shape invalid")
        return {"message_kind": "init", "txids_processed": 0}
    raise TxSubmissionDecodeError(f"unexpected txsubmission message key {key}")


def account_transcript(transcript: list[dict]) -> dict:
    message_kinds: list[str] = []
    decode_failures: list[dict] = []
    txids_processed = 0
    txsubmission_messages_observed = 0
    for index, entry in enumerate(transcript):
        payload_hex = str(entry.get("payload_hex", "") or "")
        if not payload_hex:
            continue
        txsubmission_messages_observed += 1
        try:
            decoded = decode_txsubmission_payload(bytes.fromhex(payload_hex))
        except (ValueError, TxSubmissionDecodeError) as exc:
            decode_failures.append(
                {
                    "index": index,
                    "direction": entry.get("direction"),
                    "error": str(exc),
                }
            )
            continue
        message_kinds.append(str(decoded["message_kind"]))
        txids_processed += int(decoded["txids_processed"])
    return {
        "txsubmission_messages_observed": txsubmission_messages_observed,
        "txids_processed": txids_processed,
        "txsubmission_message_kinds": message_kinds,
        "decode_failure_count": len(decode_failures),
        "decode_failures": decode_failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    transcript = json.loads(Path(args.transcript).read_text(encoding="utf-8"))
    report = account_transcript(list(transcript))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        " ".join(
            [
                f"txsubmission_messages_observed={report['txsubmission_messages_observed']}",
                f"txids_processed={report['txids_processed']}",
                f"decode_failure_count={report['decode_failure_count']}",
            ]
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
