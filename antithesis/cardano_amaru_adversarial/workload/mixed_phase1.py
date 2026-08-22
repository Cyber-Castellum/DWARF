"""Mixed Cardano/Amaru phase-1 transaction-admission differential.

The fixture is a correctly signed Conway transaction whose fee is exactly one
lovelace below the minimum calculated by cardano-cli. Transport outages are
inconclusive; they are never promoted into ledger disagreements.
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

try:
    from antithesis.assertions import always, reachable, sometimes
except Exception:  # pragma: no cover - local unit-test fallback
    def always(condition, message, details):
        return None

    def reachable(message, details):
        return None

    def sometimes(condition, message, details):
        return None


ACCEPTED = "accepted"
PHASE1_REJECT = "phase1_reject"
DECODE_REJECT = "decode_reject"
UNAVAILABLE = "unavailable"
UNKNOWN = "unknown"

_DECODE_MARKERS = (
    "invalid cbor",
    "decodeerror",
    "deserialisefailure",
    "txcmdtxreaderror",
    "expected type",
    "expected/found mismatch",
)

_PHASE1_MARKERS = (
    "feetosmallutxo",
    "fee too small",
    "fee below minimum",
    "minimum fee",
    "shelleytxvalidationerror",
    "conwaymempoolfailure",
    "submitvalidationerror",
    "txvalidationerror",
)

# Amaru deliberately keeps validation details out of the submit API response.
# This exact response shape is emitted by TransactionValidationError::Validation;
# preparation failures use "failed to prepare ... for validation" instead.
_AMARU_VALIDATION_RE = re.compile(
    r"^transaction [0-9a-f]{64} is invalid$", re.IGNORECASE
)


@dataclass(frozen=True)
class Fixture:
    payload: bytes
    minimum_fee: int
    actual_fee: int
    tx_id: str


class SubmitTransport(Protocol):
    def send(self, payload: bytes) -> dict:
        ...


def classify_response(
    status: int | None, body: str, transport_error: str | None = None
) -> str:
    """Classify only observations supported by an endpoint's actual response."""
    if transport_error is not None or status is None:
        return UNAVAILABLE
    if status in (200, 202):
        return ACCEPTED

    lowered = (body or "").lower()
    if any(marker in lowered for marker in _DECODE_MARKERS):
        return DECODE_REJECT
    if any(marker in lowered for marker in _PHASE1_MARKERS):
        return PHASE1_REJECT
    if status == 400 and _AMARU_VALIDATION_RE.fullmatch((body or "").strip()):
        return PHASE1_REJECT
    return UNKNOWN


def load_fixture(root: str | Path) -> Fixture:
    """Load and validate the atomically published fixture artifacts."""
    root = Path(root)
    envelope = json.loads((root / "underfee.tx").read_text(encoding="utf-8"))
    metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))

    cbor_hex = envelope.get("cborHex")
    if not isinstance(cbor_hex, str) or not cbor_hex or len(cbor_hex) % 2:
        raise ValueError("fixture cborHex must be non-empty, even-length hexadecimal")
    try:
        payload = bytes.fromhex(cbor_hex)
    except ValueError as exc:
        raise ValueError("fixture cborHex is not hexadecimal") from exc

    minimum_fee = int(metadata["minimum_fee"])
    actual_fee = int(metadata["actual_fee"])
    if minimum_fee <= 0 or actual_fee != minimum_fee - 1:
        raise ValueError("fixture fee must equal minimum fee minus one lovelace")

    tx_id = str(metadata["tx_id"])
    if len(tx_id) != 64 or any(char not in "0123456789abcdefABCDEF" for char in tx_id):
        raise ValueError("fixture transaction id must be 32-byte hexadecimal")

    return Fixture(payload, minimum_fee, actual_fee, tx_id.lower())


class HttpSubmitTransport:
    """POST raw transaction CBOR and preserve enough evidence to classify it."""

    def __init__(self, url: str, timeout: float = 5.0):
        self.url = url
        self.timeout = timeout

    def send(self, payload: bytes) -> dict:
        request = urllib.request.Request(
            self.url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/cbor"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(4096).decode("utf-8", "replace")
                status = response.status
            return _observation(status, body)
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read(4096).decode("utf-8", "replace")
            except Exception:
                body = str(exc.reason or "http error")
            return _observation(exc.code, body)
        except (urllib.error.URLError, ConnectionError, socket.timeout, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            return _observation(None, "", type(reason).__name__)
        except OSError as exc:
            return _observation(None, "", type(exc).__name__)


def _observation(
    status: int | None, body: str, transport_error: str | None = None
) -> dict:
    reason = body[:400] if body else (transport_error or "")
    return {
        "classification": classify_response(status, body, transport_error),
        "status": status,
        "reason": reason,
    }


def observe_differential(
    payload: bytes, transports: Mapping[str, SubmitTransport]
) -> dict:
    """Submit one immutable payload to every endpoint and compare semantics."""
    observations = {label: transport.send(payload) for label, transport in transports.items()}
    classes = {label: obs["classification"] for label, obs in observations.items()}
    both_classifiable = len(classes) >= 2 and all(
        value in (ACCEPTED, PHASE1_REJECT, DECODE_REJECT) for value in classes.values()
    )
    any_accepted = any(value == ACCEPTED for value in classes.values())
    if both_classifiable:
        phase1_agreement = all(value == PHASE1_REJECT for value in classes.values())
    else:
        phase1_agreement = None

    return {
        "observations": observations,
        "both_classifiable": both_classifiable,
        "phase1_agreement": phase1_agreement,
        "any_accepted": any_accepted,
    }


def emit_assertions(fixture: Fixture, result: dict, recovery: bool = False) -> None:
    """Emit non-vacuous Antithesis assertions for one real observation."""
    observations = result["observations"]
    details = {
        "tx_id": fixture.tx_id,
        "minimum_fee": fixture.minimum_fee,
        "actual_fee": fixture.actual_fee,
        "responses": {
            label: {
                "classification": observation["classification"],
                "status": observation.get("status"),
                "reason": str(observation.get("reason", ""))[:200],
            }
            for label, observation in observations.items()
        },
    }

    reachable("mixed phase-1 underfee fixture loaded", details)
    if result["both_classifiable"]:
        reachable("both implementations returned classifiable phase-1 results", details)
    sometimes(
        result["both_classifiable"],
        "both implementations sometimes return classifiable phase-1 results",
        details,
    )
    always(
        not result["any_accepted"],
        "neither implementation accepts a one-lovelace-under-minimum transaction",
        details,
    )
    always(
        not result["both_classifiable"] or result["phase1_agreement"] is True,
        "classifiable Cardano and Amaru results agree on phase-1 fee rejection",
        details,
    )
    if recovery:
        sometimes(
            result["phase1_agreement"] is True,
            "both implementations recover phase-1 fee rejection after faults",
            details,
        )


def default_transports(
    amaru_url: str, cardano_url: str, timeout: float = 5.0
) -> dict[str, HttpSubmitTransport]:
    return {
        "amaru": HttpSubmitTransport(amaru_url, timeout),
        "cardano": HttpSubmitTransport(cardano_url, timeout),
    }


def public_result(result: dict) -> dict:
    """Return a compact JSON-safe result for command logs."""
    return {
        "both_classifiable": result["both_classifiable"],
        "phase1_agreement": result["phase1_agreement"],
        "any_accepted": result["any_accepted"],
        "observations": {
            label: {
                "classification": observation["classification"],
                "status": observation.get("status"),
                "reason": str(observation.get("reason", ""))[:200],
            }
            for label, observation in result["observations"].items()
        },
    }
