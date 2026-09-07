"""Fail-closed observer for the mixed live hot-KES differential."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
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


HASH_RE = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
KES_MARKERS = (
    "invalid kes signature",
    "invalidkessignature",
    "invalid_kes_signature",
)
PAIR_FIELDS = (
    "source_slot",
    "source_hash",
    "mutated_hash",
    "signature_offset",
    "bit",
    "seed",
)


def normalize_hash(value: Any) -> str:
    matches = HASH_RE.findall(str(value))
    return matches[0].lower() if matches else ""


def parse_proxy_events(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if event.get("kind") != "kes_mutation" or not all(
            field in event for field in PAIR_FIELDS
        ):
            continue
        event = dict(event)
        event["source_hash"] = normalize_hash(event["source_hash"])
        event["mutated_hash"] = normalize_hash(event["mutated_hash"])
        if event["source_hash"] and event["mutated_hash"]:
            events.append(event)
    return events


def mutation_key(event: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(event.get(field) for field in PAIR_FIELDS)


def latest_paired_mutation(
    cardano_events: list[dict[str, Any]], amaru_events: list[dict[str, Any]]
) -> dict[str, Any] | None:
    amaru_keys = {mutation_key(event) for event in amaru_events}
    for event in reversed(cardano_events):
        if mutation_key(event) in amaru_keys:
            return event
    return None


def parse_amaru_log(text: str) -> dict[str, Any]:
    adopted_hashes: set[str] = set()
    invalid_kes_hashes: set[str] = set()
    for line in text.splitlines():
        lowered = line.lower()
        hashes = {value.lower() for value in HASH_RE.findall(line)}
        if "tip.adopt" in lowered:
            adopted_hashes.update(hashes)
        if any(marker in lowered for marker in KES_MARKERS):
            invalid_kes_hashes.update(hashes)
    return {
        "status": "ok" if text else "unavailable",
        "adopted_hashes": adopted_hashes,
        "invalid_kes_hashes": invalid_kes_hashes,
    }


def query_tip(socket_path: str, config_path: str, network_magic: int) -> dict[str, Any]:
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
    except subprocess.TimeoutExpired:
        return {
            "status": "unavailable",
            "error": f"cardano-cli query tip timed out after {timeout_seconds} seconds",
        }
    except OSError as error:
        return {
            "status": "unavailable",
            "error": f"cardano-cli query tip failed: {error}",
        }
    if result.returncode:
        return {
            "status": "unavailable",
            "error": (result.stderr or result.stdout).strip()[:300],
        }
    try:
        tip = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"status": "unavailable", "error": "tip output was not JSON"}
    return {
        "status": "ok",
        "slot": int(tip.get("slot", -1)),
        "hash": normalize_hash(tip.get("hash", "")),
        "block": int(tip.get("block", -1)),
    }


def evaluate_observation(
    *, mutation: dict[str, Any] | None, cardano_tip: dict[str, Any], amaru: dict[str, Any]
) -> dict[str, Any]:
    if mutation is None:
        return {"classifiable": False, "safe": None, "reason": "no paired mutation"}
    if cardano_tip.get("status") != "ok" or amaru.get("status") != "ok":
        return {"classifiable": False, "safe": None, "reason": "victim unavailable"}
    mutated_hash = normalize_hash(mutation.get("mutated_hash", ""))
    if not mutated_hash or mutated_hash not in amaru.get("invalid_kes_hashes", set()):
        return {
            "classifiable": False,
            "safe": None,
            "reason": "Amaru lacks explicit invalid-KES classification",
        }
    cardano_adopted = normalize_hash(cardano_tip.get("hash", "")) == mutated_hash
    amaru_adopted = mutated_hash in amaru.get("adopted_hashes", set())
    return {
        "classifiable": True,
        "safe": not cardano_adopted and not amaru_adopted,
        "cardano_adopted": cardano_adopted,
        "amaru_adopted": amaru_adopted,
        "reason": "paired delivery and explicit Amaru classification observed",
    }


def read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def observe_once() -> dict[str, Any]:
    cardano_events = parse_proxy_events(read_text(os.environ["CARDANO_PROXY_EVENTS"]))
    amaru_events = parse_proxy_events(read_text(os.environ["AMARU_PROXY_EVENTS"]))
    mutation = latest_paired_mutation(cardano_events, amaru_events)
    cardano_tip = query_tip(
        os.environ["CARDANO_SOCKET_PATH"],
        os.environ["CARDANO_CONFIG_PATH"],
        int(os.environ.get("NETWORK_MAGIC", "42")),
    )
    amaru = parse_amaru_log(read_text(os.environ["AMARU_EVIDENCE_LOG"]))
    evaluation = evaluate_observation(
        mutation=mutation, cardano_tip=cardano_tip, amaru=amaru
    )
    return {
        "mutation": mutation,
        "cardano_tip": cardano_tip,
        "amaru_status": amaru["status"],
        "evaluation": evaluation,
        "cardano_mutation_count": len(cardano_events),
        "amaru_mutation_count": len(amaru_events),
    }


def control_tips() -> tuple[dict[str, Any], dict[str, Any]]:
    magic = int(os.environ.get("NETWORK_MAGIC", "42"))
    config = os.environ["CARDANO_CONFIG_PATH"]
    return (
        query_tip(os.environ["CONTROL_P1_SOCKET_PATH"], config, magic),
        query_tip(os.environ["CONTROL_CONSUMER_SOCKET_PATH"], config, magic),
    )


def emit_result(result: dict[str, Any], *, phase: str) -> None:
    details = {
        "phase": phase,
        "mutation": result.get("mutation"),
        "cardano_tip": result.get("cardano_tip"),
        "evaluation": result["evaluation"],
        "cardano_mutation_count": result["cardano_mutation_count"],
        "amaru_mutation_count": result["amaru_mutation_count"],
    }
    reachable("mixed_kes_driver_invoked", {"phase": phase})
    sometimes(
        result.get("mutation") is not None,
        "mixed_kes_same_mutation_reached_both_victims",
        details,
    )
    sometimes(
        result["evaluation"]["classifiable"],
        "mixed_kes_both_victims_produced_classifiable_observations",
        details,
    )
    if result["evaluation"]["classifiable"]:
        always(
            result["evaluation"]["safe"],
            "mixed_kes_invalid_header_was_never_adopted",
            details,
        )


def run_bounded(phase: str, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    result = observe_once()
    while not result["evaluation"]["classifiable"] and time.monotonic() < deadline:
        time.sleep(1)
        result = observe_once()
    emit_result(result, phase=phase)
    print(json.dumps(result, sort_keys=True), flush=True)
    return result
