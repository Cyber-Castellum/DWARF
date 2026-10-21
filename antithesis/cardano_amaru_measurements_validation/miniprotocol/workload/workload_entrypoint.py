#!/usr/bin/env python3
"""Gate setup_complete on identical, complete pre-fault SP4 delivery."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time

from miniprotocol_observer import (
    compare_prefault_streams,
    parse_amaru_tip,
    parse_fuzzer_log,
    query_tip,
    read_text,
    tcp_reachable,
)


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    status = Path(os.environ["STATUS_DIR"])
    status.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + int(os.environ.get("PREFAULT_TIMEOUT_SECONDS", "1800"))
    while time.monotonic() < deadline:
        cardano = parse_fuzzer_log(read_text(os.environ["CARDANO_FUZZER_LOG"]))
        amaru = parse_fuzzer_log(read_text(os.environ["AMARU_FUZZER_LOG"]))
        comparison = compare_prefault_streams(cardano["injections"], amaru["injections"])
        if comparison["matched"] is False:
            atomic_json(
                status / "prefault-failed.json",
                {"reason": "paired SP4 transcript mismatch", "comparison": comparison},
            )
            return 2
        same_seed = cardano["seed"] == amaru["seed"] == os.environ["DWARF_SM_SEED"].lower()
        targets = {
            "cardano": tcp_reachable(
                os.environ["CARDANO_TARGET_HOST"], int(os.environ["CARDANO_TARGET_PORT"])
            ),
            "amaru": tcp_reachable(
                os.environ["AMARU_TARGET_HOST"], int(os.environ["AMARU_TARGET_PORT"])
            ),
        }
        if same_seed and comparison["complete_coverage"] and all(targets.values()):
            baseline = {
                "seed": cardano["seed"],
                "comparison": comparison,
                "counts": {
                    "cardano": len(cardano["injections"]),
                    "amaru": len(amaru["injections"]),
                },
                "tips": {
                    "p1": query_tip(os.environ["CONTROL_P1_SOCKET_PATH"]),
                    "consumer": query_tip(os.environ["CONTROL_CONSUMER_SOCKET_PATH"]),
                    "cardano": query_tip(os.environ["CARDANO_SOCKET_PATH"]),
                    "amaru": parse_amaru_tip(read_text(os.environ["AMARU_RUNTIME_LOG"])),
                },
                "targets_reachable": targets,
            }
            atomic_json(status / "prefault.json", baseline)
            (status / "prefault-ready").write_text("paired 24-cell SP4 coverage\n", encoding="utf-8")
            while True:
                time.sleep(60)
        time.sleep(1)
    atomic_json(status / "prefault-failed.json", {"reason": "pre-fault pairing deadline"})
    return 3


if __name__ == "__main__":
    raise SystemExit(main())

