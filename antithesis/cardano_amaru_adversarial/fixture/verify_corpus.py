#!/usr/bin/env python3
"""Verify that corpus declarations match their immutable signed transactions."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def cardano_cli(*arguments: str) -> str:
    completed = subprocess.run(
        ["cardano-cli", *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def load_payload(path: Path) -> bytes:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    return bytes.fromhex(envelope["cborHex"])


def verify_case(root: Path, case: dict) -> None:
    case_id = case["case_id"]
    tx_path = root / case["tx_file"]
    payload = load_payload(tx_path)

    if len(payload) != case["cbor_size"]:
        raise ValueError(f"{case_id}: CBOR size mismatch")
    if hashlib.sha256(payload).hexdigest() != case["cbor_sha256"]:
        raise ValueError(f"{case_id}: CBOR SHA-256 mismatch")

    tx_id_output = cardano_cli(
        "conway", "transaction", "txid", "--tx-file", str(tx_path)
    )
    try:
        tx_id = json.loads(tx_id_output)["txhash"]
    except (json.JSONDecodeError, KeyError, TypeError):
        tx_id = tx_id_output
    if tx_id != case["tx_id"]:
        raise ValueError(f"{case_id}: transaction id mismatch")

    view = json.loads(
        cardano_cli(
            "debug",
            "transaction",
            "view",
            "--tx-file",
            str(tx_path),
            "--output-json",
        )
    )
    if view["era"] != "Conway":
        raise ValueError(f"{case_id}: expected a Conway transaction")
    witnesses = view.get("witnesses")
    if not isinstance(witnesses, list) or len(witnesses) != 1:
        raise ValueError(f"{case_id}: expected exactly one key witness")
    if not isinstance(witnesses[0], dict) or not {
        "key",
        "signature",
    }.issubset(witnesses[0]):
        raise ValueError(f"{case_id}: malformed key witness")
    fee = int(view["fee"].split()[0])
    if fee != case["actual_fee"]:
        raise ValueError(f"{case_id}: signed transaction fee mismatch")
    if view["inputs"] != [case["input"]]:
        raise ValueError(f"{case_id}: signed transaction input mismatch")
    if case["actual_fee"] - case["minimum_fee"] != case["fee_delta"]:
        raise ValueError(f"{case_id}: declared fee delta mismatch")


def verify_contracts(cases: list[dict]) -> None:
    case_ids = [case["case_id"] for case in cases]
    tx_files = [case["tx_file"] for case in cases]
    tx_ids = [case["tx_id"] for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("corpus case ids must be unique")
    if len(tx_files) != len(set(tx_files)) or len(tx_ids) != len(set(tx_ids)):
        raise ValueError("every corpus case must have a unique signed transaction")

    negative_inputs = {
        case["input"] for case in cases if case["expected"] == "phase1_reject"
    }
    accepted_inputs = [
        case["input"] for case in cases if case["expected"] == "accepted"
    ]
    if len(negative_inputs) != 1:
        raise ValueError("negative fee cases must intentionally share one replay-safe input")
    if len(accepted_inputs) != len(set(accepted_inputs)):
        raise ValueError("accepted fee cases must use distinct exactly-once inputs")
    if negative_inputs.intersection(accepted_inputs):
        raise ValueError("accepted fee cases must not reuse the negative-case input")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parent / "static",
    )
    root = parser.parse_args().root.resolve()
    cases = json.loads((root / "corpus.json").read_text(encoding="utf-8"))["cases"]
    verify_contracts(cases)
    for case in cases:
        verify_case(root, case)
    print(f"verified {len(cases)} signed fee-corpus transactions")


if __name__ == "__main__":
    main()
