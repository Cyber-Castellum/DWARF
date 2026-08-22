<<<<<<< HEAD
"""Mixed Cardano/Amaru signed phase-1 fee-boundary differential.

The immutable corpus spans negative, exact-minimum, and positive fee deltas.
Transport outages are inconclusive; they are never promoted into ledger
disagreements.
=======
"""Mixed Cardano/Amaru phase-1 transaction-admission differential.

The fixture is a correctly signed Conway transaction whose fee is exactly one
lovelace below the minimum calculated by cardano-cli. Transport outages are
inconclusive; they are never promoted into ledger disagreements.
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
"""

from __future__ import annotations

import json
<<<<<<< HEAD
import hashlib
import re
import socket
import time
=======
import re
import socket
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
<<<<<<< HEAD
from typing import Callable, Mapping, Protocol
=======
from typing import Mapping, Protocol
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47

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
<<<<<<< HEAD
    case_id: str = "underfee-minus-1"
    fee_delta: int = -1
    expected: str = PHASE1_REJECT
    input: str = ""
=======
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47


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


<<<<<<< HEAD
def _load_payload(path: Path) -> bytes:
    envelope = json.loads(path.read_text(encoding="utf-8"))
=======
def load_fixture(root: str | Path) -> Fixture:
    """Load and validate the atomically published fixture artifacts."""
    root = Path(root)
    envelope = json.loads((root / "underfee.tx").read_text(encoding="utf-8"))
    metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))

>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
    cbor_hex = envelope.get("cborHex")
    if not isinstance(cbor_hex, str) or not cbor_hex or len(cbor_hex) % 2:
        raise ValueError("fixture cborHex must be non-empty, even-length hexadecimal")
    try:
<<<<<<< HEAD
        return bytes.fromhex(cbor_hex)
    except ValueError as exc:
        raise ValueError("fixture cborHex is not hexadecimal") from exc


def _validate_tx_id(value: object) -> str:
    tx_id = str(value)
    if len(tx_id) != 64 or any(char not in "0123456789abcdefABCDEF" for char in tx_id):
        raise ValueError("fixture transaction id must be 32-byte hexadecimal")
    return tx_id.lower()


def load_fixture(root: str | Path) -> Fixture:
    """Load and validate the original one-lovelace-under fixture artifacts."""
    root = Path(root)
    payload = _load_payload(root / "underfee.tx")
    metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))

=======
        payload = bytes.fromhex(cbor_hex)
    except ValueError as exc:
        raise ValueError("fixture cborHex is not hexadecimal") from exc

>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
    minimum_fee = int(metadata["minimum_fee"])
    actual_fee = int(metadata["actual_fee"])
    if minimum_fee <= 0 or actual_fee != minimum_fee - 1:
        raise ValueError("fixture fee must equal minimum fee minus one lovelace")

<<<<<<< HEAD
    return Fixture(payload, minimum_fee, actual_fee, _validate_tx_id(metadata["tx_id"]))


def load_corpus(root: str | Path) -> list[Fixture]:
    """Load a validated, ordered corpus of signed phase-1 boundary cases."""
    root = Path(root).resolve()
    manifest = json.loads((root / "corpus.json").read_text(encoding="utf-8"))
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("fixture corpus must contain a non-empty cases list")

    fixtures: list[Fixture] = []
    seen_ids: set[str] = set()
    for case in cases:
        case_id = str(case.get("case_id", ""))
        if not case_id:
            raise ValueError("fixture corpus case id must be non-empty")
        if case_id in seen_ids:
            raise ValueError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)

        expected = str(case.get("expected", ""))
        if expected not in (ACCEPTED, PHASE1_REJECT):
            raise ValueError(f"unknown expected outcome for {case_id}: {expected}")

        minimum_fee = int(case["minimum_fee"])
        actual_fee = int(case["actual_fee"])
        fee_delta = int(case["fee_delta"])
        if minimum_fee <= 0 or actual_fee - minimum_fee != fee_delta:
            raise ValueError(f"fee delta mismatch for {case_id}")

        tx_path = (root / str(case["tx_file"])).resolve()
        if root not in tx_path.parents:
            raise ValueError(f"transaction for {case_id} must be inside fixture root")
        if not tx_path.is_file():
            raise FileNotFoundError(f"missing transaction file for {case_id}: {tx_path}")

        payload = _load_payload(tx_path)
        expected_size = int(case["cbor_size"])
        if len(payload) != expected_size:
            raise ValueError(f"CBOR size mismatch for {case_id}")
        expected_sha256 = str(case["cbor_sha256"]).lower()
        if len(expected_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in expected_sha256
        ):
            raise ValueError(f"invalid CBOR SHA-256 for {case_id}")
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise ValueError(f"CBOR SHA-256 mismatch for {case_id}")

        fixtures.append(
            Fixture(
                payload=payload,
                minimum_fee=minimum_fee,
                actual_fee=actual_fee,
                tx_id=_validate_tx_id(case["tx_id"]),
                case_id=case_id,
                fee_delta=fee_delta,
                expected=expected,
                input=str(case.get("input", "")),
            )
        )
    return fixtures
=======
    tx_id = str(metadata["tx_id"])
    if len(tx_id) != 64 or any(char not in "0123456789abcdefABCDEF" for char in tx_id):
        raise ValueError("fixture transaction id must be 32-byte hexadecimal")

    return Fixture(payload, minimum_fee, actual_fee, tx_id.lower())
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47


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


<<<<<<< HEAD
def matches_expected(result: dict, expected: str) -> bool:
    """Return true only when every classifiable implementation matches the case."""
    return bool(result["both_classifiable"]) and all(
        observation["classification"] == expected
        for observation in result["observations"].values()
    )


def negative_cases(corpus: list[Fixture]) -> list[Fixture]:
    """Return the idempotent rejection cases that are safe to replay under faults."""
    cases = [
        fixture
        for fixture in corpus
        if fixture.expected == PHASE1_REJECT and fixture.fee_delta < 0
    ]
    if not cases:
        raise ValueError("fixture corpus contains no negative fee cases")
    return cases


def select_negative_case(
    corpus: list[Fixture], chooser: Callable[[list[Fixture]], Fixture]
) -> Fixture:
    """Offer only replay-safe negative cases to an Antithesis-aware chooser."""
    candidates = negative_cases(corpus)
    selected = chooser(candidates)
    if selected not in candidates:
        raise ValueError("negative-case chooser returned a case it was not offered")
    return selected


def probe_valid_boundaries(
    corpus: list[Fixture],
    transports: Mapping[str, SubmitTransport],
    readiness_attempts: int = 10,
    pause: Callable[[float], None] = time.sleep,
) -> dict:
    """Gate valid transactions on an idempotent probe, then submit each once."""
    if readiness_attempts < 1:
        raise ValueError("readiness attempts must be positive")
    try:
        readiness_fixture = next(
            fixture for fixture in corpus if fixture.case_id == "underfee-minus-1"
        )
    except StopIteration as exc:
        raise ValueError("fixture corpus is missing underfee-minus-1 readiness case") from exc

    readiness_result = None
    ready = False
    for attempt in range(readiness_attempts):
        readiness_result = observe_differential(readiness_fixture.payload, transports)
        if matches_expected(readiness_result, readiness_fixture.expected):
            ready = True
            break
        if attempt + 1 < readiness_attempts:
            pause(0.5)

    valid_results = []
    if ready:
        for fixture in corpus:
            if fixture.expected == ACCEPTED:
                valid_results.append(
                    (fixture, observe_differential(fixture.payload, transports))
                )

    return {
        "ready": ready,
        "readiness": (readiness_fixture, readiness_result),
        "valid": valid_results,
    }


=======
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
def emit_assertions(fixture: Fixture, result: dict, recovery: bool = False) -> None:
    """Emit non-vacuous Antithesis assertions for one real observation."""
    observations = result["observations"]
    details = {
<<<<<<< HEAD
        "case_id": fixture.case_id,
        "fee_delta": fixture.fee_delta,
        "expected": fixture.expected,
        "input": fixture.input,
=======
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
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

<<<<<<< HEAD
    reachable("mixed phase-1 fee corpus case loaded", details)
    if result["both_classifiable"]:
        reachable("both implementations returned classifiable fee-corpus results", details)
    sometimes(
        matches_expected(result, fixture.expected),
        "both implementations sometimes match the expected fee-corpus outcome",
        details,
    )
    always(
        not result["both_classifiable"]
        or matches_expected(result, fixture.expected),
        "classifiable results match the expected fee-corpus outcome",
        details,
    )
    if fixture.expected == PHASE1_REJECT:
        always(
            not result["any_accepted"],
            "no implementation accepts a negative fee-corpus case",
            details,
        )
    if recovery:
        sometimes(
            matches_expected(result, fixture.expected),
            "both implementations recover the expected fee-corpus outcome after faults",
            details,
        )

    # Each corpus member has a distinct, fixed property identity. Keeping the
    # message literal at its own call site lets Antithesis catalog reachability
    # and correctness independently instead of merging every case together.
    if fixture.case_id == "underfee-minus-100":
        reachable("underfee minus 100 case reached", details)
        sometimes(
            matches_expected(result, fixture.expected),
            "underfee minus 100 sometimes matches on both implementations",
            details,
        )
        always(
            not result["both_classifiable"] or matches_expected(result, fixture.expected),
            "underfee minus 100 matches on both implementations",
            details,
        )
    elif fixture.case_id == "underfee-minus-2":
        reachable("underfee minus 2 case reached", details)
        sometimes(
            matches_expected(result, fixture.expected),
            "underfee minus 2 sometimes matches on both implementations",
            details,
        )
        always(
            not result["both_classifiable"] or matches_expected(result, fixture.expected),
            "underfee minus 2 matches on both implementations",
            details,
        )
    elif fixture.case_id == "underfee-minus-1":
        reachable("underfee minus 1 case reached", details)
        sometimes(
            matches_expected(result, fixture.expected),
            "underfee minus 1 sometimes matches on both implementations",
            details,
        )
        always(
            not result["both_classifiable"] or matches_expected(result, fixture.expected),
            "underfee minus 1 matches on both implementations",
            details,
        )
    elif fixture.case_id == "minimum-exact":
        reachable("minimum exact fee case reached", details)
        sometimes(
            matches_expected(result, fixture.expected),
            "minimum exact fee sometimes matches on both implementations",
            details,
        )
        always(
            not result["both_classifiable"] or matches_expected(result, fixture.expected),
            "minimum exact fee matches on both implementations",
            details,
        )
    elif fixture.case_id == "minimum-plus-1":
        reachable("minimum plus 1 fee case reached", details)
        sometimes(
            matches_expected(result, fixture.expected),
            "minimum plus 1 fee sometimes matches on both implementations",
            details,
        )
        always(
            not result["both_classifiable"] or matches_expected(result, fixture.expected),
            "minimum plus 1 fee matches on both implementations",
            details,
        )

    # Preserve the original assertion identities for historical run comparison.
    if fixture.case_id == "underfee-minus-1":
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


def emit_readiness_assertion(fixture: Fixture, result: dict) -> None:
    """Catalog successful readiness independently of the replayed -1 case."""
    details = {
        "case_id": fixture.case_id,
        "expected": fixture.expected,
        "tx_id": fixture.tx_id,
        **public_result(result),
    }
    sometimes(
        matches_expected(result, fixture.expected),
        "mixed fee-corpus readiness succeeds",
        details,
    )

=======
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

>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47

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
<<<<<<< HEAD


def public_case_result(fixture: Fixture, result: dict) -> dict:
    """Add public corpus metadata to the compact command result."""
    return {
        "case_id": fixture.case_id,
        "fee_delta": fixture.fee_delta,
        "expected": fixture.expected,
        "tx_id": fixture.tx_id,
        **public_result(result),
    }


def report_command_error(command: str, exc: Exception) -> None:
    """Make a bounded command failure visible without failing its process."""
    details = {
        "command": command,
        "error_type": type(exc).__name__,
        "error": str(exc)[:300],
    }
    always(
        False,
        "mixed phase-1 test commands complete without error",
        details,
    )
    print(json.dumps(details, sort_keys=True), flush=True)
=======
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47
