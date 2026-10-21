"""Extract per-header validation verdicts and reasons from node logs.

Both parsers turn a node's normal structured log lines into
``{"header_hash", "verdict", "reason", "at"}`` events, correlated later by
header hash. A line whose verdict cannot be extracted is skipped; the caller
treats a missing verdict as ``unavailable``, never as an implicit accept.
"""
from __future__ import annotations

import json
import re
from typing import Iterable

_OCERT = re.compile(
    r"(KESBeforeStartOCERT|KESAfterEndOCERT|CounterTooSmallOCERT|"
    r"CounterOverIncrementedOCERT|InvalidKesSignatureOCERT|InvalidSignatureOCERT)"
)
# Amaru logs the *Display* of the header-validation error (thiserror
# #[error(...)]) in the trace "error" field, not the enum variant name. Each
# entry below maps a distinctive Display phrase (lower-cased) to the canonical
# reason token the opcert case table declares. The variant name itself is also
# accepted, so a future Amaru that serializes the variant still matches. Order
# matters: more specific phrases first.
_AMARU_REASON_PHRASES = (
    ("InvalidKesSignature", "invalid kes signature from leader"),
    ("InvalidSignature", "invalid operational certificate signature from issuer"),
    ("SequenceNumberTooFarAhead", "is too far ahead of the latest known sequence number"),
    ("SequenceNumberTooSmall", "is less than the latest known sequence number"),
    ("OpCertKesPeriodTooLarge", "is greater than the block slot kes period"),
)
_AMARU_VARIANT = re.compile(
    r"(OpCertKesPeriodTooLarge|OpCertKesPeriodTooOld|InvalidKesSignature|"
    r"SequenceNumberTooSmall|SequenceNumberTooFarAhead|InvalidSignature)"
)


def amaru_reason(error):
    """Map an Amaru header-validation error Display string to a canonical token.

    Returns None when no known reason is recognised (the caller then treats the
    rejection as reason-unknown, which fails a reason-sensitive case closed).
    """
    if not error:
        return None
    variant = _AMARU_VARIANT.search(error)
    if variant:
        return variant.group(1)
    text = error.lower()
    # OpCertKesPeriodTooOld shares the "kes period" prefix with TooLarge, so it
    # is matched on its own "is too old" phrase first.
    if "kes period" in text and "is too old" in text:
        return "OpCertKesPeriodTooOld"
    for token, phrase in _AMARU_REASON_PHRASES:
        if phrase in text:
            return token
    return None


def _loads(line: str):
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _norm_hash(value):
    """Normalise a cardano-node hash field to a bare 64-hex string.

    Real 11.1.2 logs render ``newtip`` as ``"<hash>@<slot>"`` and
    ``headers[].hash`` as a JSON-quoted ``"\\"<hash>\\""``; ``tipBlockHash`` is
    already bare. Strip any surrounding quotes and a trailing ``@slot``.
    """
    if value is None:
        return None
    text = str(value).strip().strip('"')
    text = text.split("@", 1)[0]
    return text or None


def parse_cardano_header_events(lines: Iterable[str]) -> list[dict]:
    out: list[dict] = []
    for line in lines:
        document = _loads(line)
        if not isinstance(document, dict):
            continue
        namespace = str(document.get("ns", ""))
        data = document.get("data")
        if not isinstance(data, dict):
            continue
        if namespace.endswith("AddedToCurrentChain") or namespace.endswith("SwitchedToAFork"):
            block = data.get("block")
            block_hash = block.get("hash") if isinstance(block, dict) else block
            header_hash = _norm_hash(
                data.get("tipBlockHash") or data.get("newtip") or block_hash
            )
            if header_hash:
                out.append({"header_hash": str(header_hash), "verdict": "accepted",
                            "reason": None, "at": document.get("at")})
        elif namespace.endswith("AddBlockValidation.ValidCandidate") or namespace.endswith("ValidCandidate"):
            # Every fully validated block emits ValidCandidate with data.block
            # "<hash>@<slot>". AddedToCurrentChain only names the *selected* tip,
            # so a valid header that the honest chain immediately extends past is
            # still recorded here as accepted (the per-header accept signal).
            header_hash = _norm_hash(data.get("block"))
            if header_hash:
                out.append({"header_hash": str(header_hash), "verdict": "accepted",
                            "reason": None, "at": document.get("at")})
        elif "InvalidBlock" in namespace:
            block = data.get("block")
            header_hash = _norm_hash(block.get("hash") if isinstance(block, dict) else block)
            match = _OCERT.search(json.dumps(data))
            if header_hash:
                out.append({"header_hash": str(header_hash), "verdict": "rejected",
                            "reason": match.group(1) if match else None, "at": document.get("at")})
        elif namespace.endswith("ChainSync.Client.Exception") or "ChainSyncClient" in namespace:
            # Praos header validation runs in the ChainSync client: an invalid
            # header raises a HeaderError there (not a ChainDB InvalidBlock),
            # naming the header via blockPointHash inside the exception text.
            blob = json.dumps(data)
            if "HeaderError" in blob:
                hash_match = re.search(r"blockPointHash = ([0-9a-fA-F]{64})", blob)
                reason = _OCERT.search(blob)
                if hash_match:
                    out.append({"header_hash": hash_match.group(1), "verdict": "rejected",
                                "reason": reason.group(1) if reason else None, "at": document.get("at")})
    return out


def parse_amaru_header_events(lines: Iterable[str]) -> list[dict]:
    out: list[dict] = []
    for line in lines:
        document = _loads(line)
        if not isinstance(document, dict):
            continue
        fields = document.get("fields") or {}
        header_hash = fields.get("header_hash")
        if not header_hash:
            continue
        message = str(fields.get("message", ""))
        error = str(fields.get("error", ""))
        outcome = fields.get("outcome")
        # Accept: the header is adopted as the new tip (INFO "tip.adopt") or the
        # header lifecycle records a valid outcome ("perf.header.lifecycle").
        if (
            message == "tip.adopt"
            or (message == "perf.header.lifecycle" and outcome == "valid")
            or outcome == "new_tip"
            or message == "chain.tip_accepted"
        ):
            out.append({"header_hash": str(header_hash), "verdict": "accepted",
                        "reason": None, "at": document.get("timestamp")})
        # Reject: header validation failed ("perf.header.lifecycle" with
        # outcome "invalid_header"); the Display of the validation error is in
        # the "error" field. _AMARU maps that Display to a canonical reason token.
        elif outcome == "invalid_header" or "header_rejected" in message or "validation failed" in error:
            out.append({"header_hash": str(header_hash), "verdict": "rejected",
                        "reason": amaru_reason(error), "at": document.get("timestamp")})
    return out


def verdict_by_hash(events: list[dict]) -> dict[str, dict]:
    """Last event per header hash wins (a later accept supersedes an earlier reject)."""
    result: dict[str, dict] = {}
    for event in events:
        result[event["header_hash"]] = event
    return result


def merge_verdicts_sticky(accumulator: dict, events: list[dict]) -> dict:
    """Fold verdict ``events`` into ``accumulator`` IN PLACE, reject-sticky.

    A ``rejected`` verdict for a header hash is TERMINAL: once a node has
    rejected a specific header hash, no later event may overwrite it -- neither a
    canonical re-serve of the same hash that the node then accepts, nor a
    re-follow. This closes the family-A differential masking where a strict
    decoder rejects a served deviant header and a subsequent accept on the SAME
    hash would otherwise flip the recorded verdict to ``accepted`` (the plain
    last-wins ``verdict_by_hash`` did exactly that), scoring a real
    reject-vs-accept disagreement as agreement.

    A served deviant header has its own unique hash, so a verdict on a DIFFERENT
    (canonical / real-pool) header -- a different key -- never touches it. Accept
    events keep last-wins among themselves (a normal adoption still records
    ``accepted``); a reject wins over an accept regardless of arrival order. A
    hash never seen stays absent (the caller treats absent as ``inconclusive``,
    never an implicit accept). Returns ``accumulator``.
    """
    for event in events:
        header_hash = event["header_hash"]
        existing = accumulator.get(header_hash)
        if existing is not None and existing.get("verdict") == "rejected":
            continue  # reject is terminal for this hash; never overwritten
        accumulator[header_hash] = event
    return accumulator


# --------------------------------------------------------------------------- #
# Canonical opcert reject-rule mapping (differential reason parity).
#
# cardano-node and Amaru name the SAME opcert rejection rule with DIFFERENT
# tokens (cardano `*OCERT`; amaru a distinct enum/Display token). The two token
# namespaces are disjoint, so a single flat map is unambiguous: it takes either
# implementation's reject reason token to the one canonical rule it means. Two
# nodes that both reject a served deviant header AGREE on the reason iff their
# tokens map to the SAME canonical rule; a both-reject-but-different-canonical
# case is a REASON-DIVERGENCE finding (distinct from a verdict disagreement).
# --------------------------------------------------------------------------- #
CANONICAL_OCERT_REASON = {
    # cold-key authorization (opcert signed by the wrong cold key)
    "InvalidSignatureOCERT": "cold-key-unauthorized",
    "InvalidSignature": "cold-key-unauthorized",
    # counter monotonicity: replayed below the recorded counter
    "CounterTooSmallOCERT": "counter-too-small",
    "SequenceNumberTooSmall": "counter-too-small",
    # counter monotonicity: jumped more than +1 over the recorded counter
    "CounterOverIncrementedOCERT": "counter-too-large",
    "SequenceNumberTooFarAhead": "counter-too-large",
    # KES window: opcert start period AFTER the block slot period
    "KESBeforeStartOCERT": "kes-before-window",
    "OpCertKesPeriodTooLarge": "kes-before-window",
    # KES window: opcert start period aged past the end of the window
    "KESAfterEndOCERT": "kes-after-window",
    "OpCertKesPeriodTooOld": "kes-after-window",
    # KES hot-key binding: body signed by a KES key not bound in the opcert
    "InvalidKesSignatureOCERT": "hot-key-mismatch",
    "InvalidKesSignature": "hot-key-mismatch",
}


def canonical_reason(token):
    """Map a per-implementation opcert reject reason token to its canonical rule.

    Returns ``None`` for a missing / empty / unrecognised token (the caller then
    treats the reason as unknown, which fails a reason-parity check closed --
    two rejects cannot be shown to agree on a rule that is not recognised).
    """
    if not token:
        return None
    return CANONICAL_OCERT_REASON.get(token)
