"""Fail-closed observer for paired SP4 mini-protocol state-machine fuzzing."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
from typing import Any

try:
    from antithesis.assertions import always, reachable, sometimes
except Exception:  # pragma: no cover - local unit-test fallback
    def always(condition, message, details):
        return None

    def reachable(message, details):
        return None

    def sometimes(condition, message, details):
        return None


PROTOCOLS = ("chainsync", "blockfetch", "txsubmission", "keepalive")
DEPARTURE_CLASSES = (
    "WrongAgency",
    "OutOfState",
    "PrematureTerminal",
    "PostTerminal",
    "Flood",
    "Duplicate",
)
REQUIRED_CELLS = frozenset(
    (protocol, departure)
    for protocol in PROTOCOLS
    for departure in DEPARTURE_CLASSES
)
SP4_IMAGE_DIGEST = (
    "sha256:c5e35065b9a58c337cd770ef0317bf11c1057a5869d654f7873275d3c8fe1aa1"
)
INJECTION_RE = re.compile(
    r"^(?:dwarf-adversary:\s+)?state-machine-init\[(?P<protocol>chainsync|blockfetch|txsubmission|keepalive)\]: "
    r"dialing; injecting departure=(?P<class>WrongAgency|OutOfState|PrematureTerminal|"
    r"PostTerminal|Flood|Duplicate) legalPrefix=(?P<legal_prefix>\d+) "
    r"frames=(?P<frames>\d+)\s*$"
)
SEED_RE = re.compile(r"seed: using explicit seed (?P<seed>0x[0-9a-fA-F]+|\d+)")
AMARU_TIP_RE = re.compile(
    r"tip\.adopt slot=(?P<slot>\d+) header_hash=\"(?P<hash>[0-9a-fA-F]{64})\""
    r" block_height=(?P<block>\d+)"
)


def read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_fuzzer_log(text: str) -> dict[str, Any]:
    """Extract only concrete SP4 pool injection records from stdout."""
    seed = None
    injections: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        seed_match = SEED_RE.search(line)
        if seed_match:
            seed = seed_match.group("seed").lower()
        match = INJECTION_RE.fullmatch(line)
        if match:
            injections.append(
                {
                    "protocol": match.group("protocol"),
                    "class": match.group("class"),
                    "legal_prefix": int(match.group("legal_prefix")),
                    "frames": int(match.group("frames")),
                }
            )
    return {"seed": seed, "injections": injections}


def cells_for(injections: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {
        (item.get("protocol", ""), item.get("class", ""))
        for item in injections
        if (item.get("protocol", ""), item.get("class", "")) in REQUIRED_CELLS
    }


def compare_prefault_streams(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> dict[str, Any]:
    common_length = min(len(left), len(right))
    mismatch = next(
        (index for index in range(common_length) if left[index] != right[index]),
        None,
    )
    matched = mismatch is None
    common = left[:common_length] if matched else left[:mismatch]
    covered = cells_for(common)
    missing = sorted(REQUIRED_CELLS - covered, key=lambda cell: (PROTOCOLS.index(cell[0]), DEPARTURE_CLASSES.index(cell[1])))
    return {
        "matched": matched,
        "first_mismatch_index": mismatch,
        "common_length": len(common),
        "complete_coverage": matched and not missing,
        "covered_cells": [list(cell) for cell in sorted(covered)],
        "missing_cells": [list(cell) for cell in missing],
        "left_count": len(left),
        "right_count": len(right),
    }


def classify_fatal_signals(text: str) -> dict[str, Any]:
    """Classify fatal evidence while keeping reported EADDRINUSE non-novel."""
    lowered = text.lower()
    known: list[str] = []
    novel: list[str] = []
    eaddr = "eaddrinuse" in lowered or "address already in use" in lowered
    if eaddr:
        known.append("amaru_listener_eaddrinuse")
    markers = {
        "panic": "panicked at",
        "fatal": " fatal ",
        "abort": "aborted",
        "oom": "out of memory",
    }
    for name, marker in markers.items():
        if marker in f" {lowered} " and not (name == "panic" and eaddr):
            novel.append(name)
    return {
        "fatal": bool(known or novel),
        "known_background": known,
        "novel": novel,
    }


def parse_amaru_tip(text: str) -> dict[str, Any]:
    latest = None
    for match in AMARU_TIP_RE.finditer(text):
        latest = {
            "status": "ok",
            "slot": int(match.group("slot")),
            "hash": match.group("hash").lower(),
            "block": int(match.group("block")),
        }
    return latest or {"status": "unavailable", "error": "no tip.adopt record"}


def query_tip(socket_path: str, network_magic: int = 42) -> dict[str, Any]:
    timeout_seconds = 8
    try:
        result = subprocess.run(
            [
                "cardano-cli",
                "query",
                "tip",
                "--socket-path",
                socket_path,
                "--testnet-magic",
                str(network_magic),
            ],
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env={**os.environ, "CARDANO_NODE_SOCKET_PATH": socket_path},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"status": "unavailable", "error": str(error)[:300]}
    if result.returncode:
        return {
            "status": "unavailable",
            "error": (result.stderr or result.stdout).strip()[:300],
        }
    try:
        value = json.loads(result.stdout)
        return {
            "status": "ok",
            "slot": int(value.get("slot", -1)),
            "hash": str(value.get("hash", "")).lower(),
            "block": int(value.get("block", -1)),
        }
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return {"status": "unavailable", "error": f"invalid tip: {error}"}


def tcp_reachable(host: str, port: int, timeout_seconds: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def evaluate_runtime(
    *,
    prefault: dict[str, Any],
    cardano_cells: list[list[str]],
    amaru_cells: list[list[str]],
    post_setup_counts: dict[str, int],
    targets_reachable: dict[str, bool],
    control_progress: bool,
    unrelated_peers_usable: bool,
    fatal: dict[str, bool],
    recovered: dict[str, bool],
    converged: bool,
) -> dict[str, Any]:
    inputs = {
        "prefault": prefault,
        "cardano_cells": cardano_cells,
        "amaru_cells": amaru_cells,
        "post_setup_counts": post_setup_counts,
        "targets_reachable": targets_reachable,
        "control_progress": control_progress,
        "unrelated_peers_usable": unrelated_peers_usable,
        "fatal": fatal,
        "recovered": recovered,
        "converged": converged,
    }
    cardano_coverage = {tuple(cell) for cell in cardano_cells}
    amaru_coverage = {tuple(cell) for cell in amaru_cells}
    classifiable = all(
        (
            prefault.get("matched") is True,
            prefault.get("complete_coverage") is True,
            prefault.get("common_length", 0) >= len(REQUIRED_CELLS),
            cardano_coverage >= REQUIRED_CELLS,
            amaru_coverage >= REQUIRED_CELLS,
            post_setup_counts.get("cardano", 0) > 0,
            post_setup_counts.get("amaru", 0) > 0,
            targets_reachable.get("cardano") is True,
            targets_reachable.get("amaru") is True,
        )
    )
    safe = classifiable and all(
        (
            control_progress,
            unrelated_peers_usable,
            not fatal.get("cardano", True),
            not fatal.get("amaru", True),
            recovered.get("cardano") is True,
            recovered.get("amaru") is True,
            recovered.get("consumer") is True,
            converged,
        )
    )
    return {
        "classifiable": classifiable,
        "safe": safe if classifiable else None,
        "inputs": inputs,
    }


def load_prefault_state() -> dict[str, Any]:
    try:
        return json.loads(
            (Path(os.environ["STATUS_DIR"]) / "prefault.json").read_text(encoding="utf-8")
        )
    except (OSError, KeyError, json.JSONDecodeError):
        return {}


def observe_runtime() -> dict[str, Any]:
    baseline = load_prefault_state()
    cardano_log = parse_fuzzer_log(read_text(os.environ["CARDANO_FUZZER_LOG"]))
    amaru_log = parse_fuzzer_log(read_text(os.environ["AMARU_FUZZER_LOG"]))
    cardano_tip = query_tip(os.environ["CARDANO_SOCKET_PATH"])
    p1_tip = query_tip(os.environ["CONTROL_P1_SOCKET_PATH"])
    p2_tip = query_tip(os.environ["CONTROL_P2_SOCKET_PATH"])
    p3_tip = query_tip(os.environ["CONTROL_P3_SOCKET_PATH"])
    consumer_tip = query_tip(os.environ["CONTROL_CONSUMER_SOCKET_PATH"])
    amaru_text = read_text(os.environ["AMARU_RUNTIME_LOG"])
    cardano_text = read_text(os.environ["CARDANO_RUNTIME_LOG"])
    amaru_tip = parse_amaru_tip(amaru_text)
    base_counts = baseline.get("counts", {})
    post_setup_counts = {
        "cardano": max(0, len(cardano_log["injections"]) - int(base_counts.get("cardano", 0))),
        "amaru": max(0, len(amaru_log["injections"]) - int(base_counts.get("amaru", 0))),
    }
    targets = {
        "cardano": tcp_reachable(
            os.environ["CARDANO_TARGET_HOST"], int(os.environ["CARDANO_TARGET_PORT"])
        ),
        "amaru": tcp_reachable(
            os.environ["AMARU_TARGET_HOST"], int(os.environ["AMARU_TARGET_PORT"])
        ),
    }
    baseline_tips = baseline.get("tips", {})
    producer_tips = [p1_tip, p2_tip, p3_tip]
    producer_hashes = {
        tip.get("hash") for tip in producer_tips if tip.get("status") == "ok"
    }
    control_progress = all(
        (
            p1_tip.get("status") == "ok",
            consumer_tip.get("status") == "ok",
            p1_tip.get("slot", -1) > baseline_tips.get("p1", {}).get("slot", -1),
            consumer_tip.get("slot", -1)
            > baseline_tips.get("consumer", {}).get("slot", -1),
        )
    )
    unrelated_peers_usable = (
        all(tip.get("status") == "ok" for tip in producer_tips)
        and consumer_tip.get("status") == "ok"
        and consumer_tip.get("hash") in producer_hashes
    )
    amaru_fatal = classify_fatal_signals(amaru_text)
    cardano_fatal = classify_fatal_signals(cardano_text)
    cardano_recovered = (
        cardano_tip.get("status") == "ok"
        and cardano_tip.get("slot", -1) > baseline_tips.get("cardano", {}).get("slot", -1)
        and targets["cardano"]
    )
    amaru_recovered = (
        amaru_tip.get("status") == "ok"
        and amaru_tip.get("slot", -1) > baseline_tips.get("amaru", {}).get("slot", -1)
        and targets["amaru"]
    )
    recovered = {
        "cardano": cardano_recovered,
        "amaru": amaru_recovered,
        "consumer": control_progress and consumer_tip.get("hash") in producer_hashes,
    }
    inputs = {
        "prefault": baseline.get("comparison", {}),
        "cardano_cells": [list(cell) for cell in sorted(cells_for(cardano_log["injections"]))],
        "amaru_cells": [list(cell) for cell in sorted(cells_for(amaru_log["injections"]))],
        "post_setup_counts": post_setup_counts,
        "targets_reachable": targets,
        "control_progress": control_progress,
        "unrelated_peers_usable": unrelated_peers_usable,
        "fatal": {
            "cardano": cardano_fatal["fatal"],
            "amaru": amaru_fatal["fatal"],
        },
        "recovered": recovered,
        "converged": consumer_tip.get("status") == "ok"
        and consumer_tip.get("hash") in producer_hashes,
    }
    evaluation = evaluate_runtime(**inputs)
    return {
        **evaluation,
        "tips": {
            "cardano": cardano_tip,
            "amaru": amaru_tip,
            "p1": p1_tip,
            "p2": p2_tip,
            "p3": p3_tip,
            "consumer": consumer_tip,
        },
        "sample_counts": {
            "cardano_total": len(cardano_log["injections"]),
            "amaru_total": len(amaru_log["injections"]),
            **post_setup_counts,
        },
        "amaru_fatal_signals": amaru_fatal,
        "cardano_fatal_signals": cardano_fatal,
    }


def emit_assertions(result: dict[str, Any], phase: str) -> None:
    inputs = result["inputs"]
    details = {
        "phase": phase,
        "seed": os.environ.get("DWARF_SM_SEED", "unknown"),
        "sp4_image_digest": SP4_IMAGE_DIGEST,
        "prefault": inputs["prefault"],
        "post_setup_counts": inputs["post_setup_counts"],
    }
    reachable("mixed_sm_workload_command_invoked", {"phase": phase})
    sometimes(
        inputs["targets_reachable"].get("cardano", False)
        and inputs["targets_reachable"].get("amaru", False),
        "mixed_sm_both_targets_reached",
        details,
    )
    sometimes(
        inputs["prefault"].get("complete_coverage", False),
        "mixed_sm_identical_prefault_transcript",
        details,
    )
    for target, cells in (
        ("cardano", inputs["cardano_cells"]),
        ("amaru", inputs["amaru_cells"]),
    ):
        covered = {tuple(cell) for cell in cells}
        for protocol, departure in sorted(REQUIRED_CELLS):
            reachable_name = (
                f"mixed_sm_{target}_{protocol}_{departure.lower()}_exercised"
            )
            sometimes(
                (protocol, departure) in covered,
                reachable_name,
                {**details, "target": target, "protocol": protocol, "class": departure},
            )
    if result["classifiable"]:
        always(
            not inputs["fatal"].get("cardano", True),
            "mixed_sm_cardano_no_panic_or_fatal_termination",
            details,
        )
        always(
            not inputs["fatal"].get("amaru", True),
            "mixed_sm_amaru_no_panic_or_fatal_termination",
            details,
        )
        always(
            result["safe"],
            "mixed_sm_illegal_sessions_contained",
            details,
        )


def wait_until(predicate, timeout_seconds: float, interval_seconds: float = 1.0):
    deadline = time.monotonic() + timeout_seconds
    value = predicate()
    while not value and time.monotonic() < deadline:
        time.sleep(interval_seconds)
        value = predicate()
    return value
