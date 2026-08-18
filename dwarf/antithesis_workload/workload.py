"""Dwarf Antithesis workload: dial the Amaru node and drive CBOR, emitting
Antithesis SDK assertions. Runs as the Amaru node's peer inside the sim.

SDK is optional: when the antithesis package is absent (local dry-run), the
helpers no-op so the workload is unit-testable without the runtime.
"""
import os
import sys

try:
    from antithesis.assertions import always, sometimes, reachable
    _HAVE_SDK = True
except Exception:  # pragma: no cover - exercised only with the SDK installed
    _HAVE_SDK = False

    def always(condition, message, details):
        return None

    def sometimes(condition, message, details):
        return None

    def reachable(message, details):
        return None


class NullTransport:
    """Dry-run transport: returns a fixed observation. label identifies the node."""

    def __init__(self, label: str = "amaru-1", accepted: bool = True,
                 panic: bool = False, alive: bool = True):
        self.label = label
        self._obs = {"accepted": accepted, "panic": panic, "alive": alive}

    def send(self, payload: bytes) -> dict:
        return dict(self._obs)


class TcpTransport:  # pragma: no cover - real network path, exercised in the sim
    def __init__(self, target: str, label: str | None = None):
        host, _, port = target.partition(":")
        self.host, self.port = host, int(port or "3001")
        self.label = label or host

    def send(self, payload: bytes) -> dict:
        import socket
        with socket.create_connection((self.host, self.port), timeout=5) as s:
            s.sendall(payload)
            try:
                s.recv(64)
                alive = True
            except OSError:
                alive = False
        return {"accepted": True, "panic": False, "alive": alive}


# Response-body signatures that mean "the node's CBOR decoder FAILED to parse the
# bytes as a transaction" (as opposed to decoding fine but failing ledger validation).
# This is the crux of the differential: if one implementation decodes bytes the other
# rejects at decode, that is a CDDL-conformance divergence = a finding.
_DECODE_FAIL_MARKERS = (
    "invalid cbor",                    # Amaru: "Invalid CBOR transaction: ..."
    "decodeerror",                     # Haskell: DecoderError*
    "deserialisefailure",              # Haskell: DeserialiseFailure
    "txcmdtxreaderror",                # Haskell: TxCmdTxReadError (read/deserialise)
    "expected type",                   # generic cbor shape errors
    "overflows target type",           # Amaru: u64->u32 overflow during decode
)
# Markers that mean "decoded OK, rejected at ledger/mempool VALIDATION" => decoded=True.
_VALIDATION_MARKERS = (
    "txvalidationerror",               # Haskell: TxValidationErrorInCardanoMode
    "shelleytxvalidationerror",
    "mempoolfailure",                  # Haskell: ConwayMempoolFailure
    "submitvalidationerror",
    "for validation",                  # Amaru: "failed to prepare transaction ... for validation"
    "badinputsutxo", "valuenotconserved", "outsidevalidityinterval",
)


def classify_decode(status, body: str):
    """Return (accepted, decoded) from an HTTP submit response.
    accepted: node took the tx (200/202). decoded: the CBOR parsed as a tx at all
    (True for accept OR validation-reject; False only for a genuine decode error)."""
    if status in (200, 202):
        return True, True
    low = (body or "").lower()
    if any(m in low for m in _DECODE_FAIL_MARKERS):
        return False, False
    if any(m in low for m in _VALIDATION_MARKERS):
        return False, True
    # Unknown 4xx/5xx body: conservatively treat as "not decoded" is wrong (would
    # mask real decodes); mark decoded=None so agreement logic can ignore it.
    return False, None


class HttpSubmitTransport:  # pragma: no cover - real network path, exercised in the sim
    """Fuzz Amaru's (and cardano-submit-api's) HTTP submit endpoint with mutated
    transaction CBOR. Yields a REAL observation: the node's own decoder+validator
    answers over HTTP, and we classify decode-failure vs validation-failure.

    - 200/202                        -> accepted, decoded
    - 400 w/ decode-error body       -> rejected, NOT decoded  (CDDL-relevant)
    - 400 w/ validation-error body   -> rejected, decoded
    - connection refused/reset       -> panic (node process died mid-run)
    - timeout                        -> not-alive, not-panic (hang; ambiguous)
    """

    def __init__(self, target: str, label: str | None = None,
                 path: str = "/api/submit/tx"):
        host, _, port = target.partition(":")
        self.host, self.port = host, int(port or "3011")
        self.path = path
        self.label = label or host

    def send(self, payload: bytes) -> dict:
        import urllib.request
        import urllib.error
        url = f"http://{self.host}:{self.port}{self.path}"
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={"Content-Type": "application/cbor"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read(4096).decode("utf-8", "replace")
                accepted, decoded = classify_decode(resp.status, body)
            return {"accepted": accepted, "decoded": decoded, "panic": False,
                    "alive": True, "status": resp.status, "reason": body[:200]}
        except urllib.error.HTTPError as e:
            # The node answered with an error status = it is alive; classify why.
            body = ""
            try:
                body = e.read(4096).decode("utf-8", "replace")
            except Exception:
                pass
            accepted, decoded = classify_decode(e.code, body)
            return {"accepted": accepted, "decoded": decoded, "panic": False,
                    "alive": True, "status": e.code, "reason": (body[:200] or e.reason or "http-error")}
        except (ConnectionRefusedError, ConnectionResetError) as e:
            return {"accepted": False, "decoded": None, "panic": True, "alive": False,
                    "status": None, "reason": f"conn:{type(e).__name__}"}
        except urllib.error.URLError as e:
            inner = getattr(e, "reason", None)
            if isinstance(inner, (ConnectionRefusedError, ConnectionResetError)):
                return {"accepted": False, "decoded": None, "panic": True, "alive": False,
                        "status": None, "reason": f"conn:{type(inner).__name__}"}
            return {"accepted": False, "decoded": None, "panic": False, "alive": False,
                    "status": None, "reason": f"url:{inner}"}
        except Exception as e:  # timeout / unexpected: ambiguous, not a proven panic
            return {"accepted": False, "decoded": None, "panic": False, "alive": False,
                    "status": None, "reason": f"err:{type(e).__name__}"}


def mutate_cbor(seed_bytes: bytes, seed: int) -> bytes:
    """Deterministic single-byte CBOR mutation (seedable for reproducibility)."""
    if not seed_bytes:
        return seed_bytes
    data = bytearray(seed_bytes)
    idx = seed % len(data)
    data[idx] ^= (seed * 31 + 7) & 0xFF
    return bytes(data)


SEED_CORPUS = [b"\x82\x01\x02", b"\xa1\x01\x02", b"\x9f\x01\x02\xff"]


def _assert_node(label: str, obs: dict, payload: bytes, seed: int) -> int:
    """Phase-1 per-node invariants for one target. Returns the assertion count."""
    always(obs["panic"] is False, f"{label} does not panic on fuzzed CBOR",
           {"label": label, "payload_len": len(payload), "seed": seed})
    always(obs["alive"] is True, f"{label} stays alive after fuzzed CBOR",
           {"label": label, "seed": seed})
    sometimes(obs["accepted"] is False, f"{label} sometimes rejects malformed CBOR",
              {"label": label, "seed": seed})
    return 3


def drive_once(transport=None, seed: int = 0) -> dict:
    """Single-target drive (Phase 1). Sends one mutated CBOR frame, asserts per-node invariants."""
    transport = transport or TcpTransport(os.environ.get("AMARU_TARGET", "amaru-1:3001"),
                                          label="amaru-1")
    payload = mutate_cbor(SEED_CORPUS[seed % len(SEED_CORPUS)], seed)
    obs = transport.send(payload)
    asserted = _assert_node(getattr(transport, "label", "amaru"), obs, payload, seed)
    reachable("workload drove one CBOR frame", {"seed": seed})
    return {"assertions": asserted, "panic": obs["panic"], "alive": obs["alive"]}


def parse_targets() -> list[tuple[str, str]]:
    """Return [(label, host:port)] from WORKLOAD_TARGETS, else fall back to AMARU_TARGET.

    WORKLOAD_TARGETS format: "label=host:port,label=host:port" (label optional).
    """
    raw = os.environ.get("WORKLOAD_TARGETS")
    if raw:
        specs: list[tuple[str, str]] = []
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            if "=" in item:
                label, target = item.split("=", 1)
            else:
                label, target = item.split(":")[0], item
            specs.append((label.strip(), target.strip()))
        return specs
    return [("amaru-1", os.environ.get("AMARU_TARGET", "amaru-1:3001"))]


def drive_differential(transports=None, seed: int = 0) -> dict:
    """Send the same fuzzed CBOR to every node; assert per-node invariants + decode agreement."""
    if transports is None:
        transports = [TcpTransport(target, label=label) for label, target in parse_targets()]
    payload = mutate_cbor(SEED_CORPUS[seed % len(SEED_CORPUS)], seed)
    results: list[tuple[str, dict]] = []
    asserted = 0
    for t in transports:
        label = getattr(t, "label", "node")
        obs = t.send(payload)
        asserted += _assert_node(label, obs, payload, seed)
        results.append((label, obs))
    accepts = {label: obs["accepted"] for label, obs in results}
    agree = len(set(accepts.values())) <= 1
    always(agree, "implementations agree on accept/reject of fuzzed CBOR",
           {"accepts": accepts, "seed": seed})
    asserted += 1
    reachable("workload drove one differential frame",
              {"targets": len(transports), "seed": seed})
    return {"assertions": asserted, "targets": len(transports), "agree": agree,
            "panic": any(obs["panic"] for _, obs in results)}


def load_seed_corpus() -> list[bytes]:
    """Real tx-CBOR seeds from WORKLOAD_CORPUS dir (cuddle-generated), else the
    tiny built-in SEED_CORPUS. Mutating valid txs reaches deep decode/validation;
    pure garbage only ever exercises the outermost decoder."""
    corpus_dir = os.environ.get("WORKLOAD_CORPUS")
    if corpus_dir and os.path.isdir(corpus_dir):
        seeds = []
        for name in sorted(os.listdir(corpus_dir)):
            p = os.path.join(corpus_dir, name)
            if os.path.isfile(p):
                with open(p, "rb") as fh:
                    data = fh.read()
                if data:
                    seeds.append(data)
        if seeds:
            return seeds
    return list(SEED_CORPUS)


def submit_transports() -> list:
    """Build HTTP submit-api transports from WORKLOAD_TARGETS (label=host:port,...).
    Default port for submit endpoints is 3011."""
    return [HttpSubmitTransport(target if ":" in target else f"{target}:3011",
                                label=label)
            for label, target in parse_targets()]


def drive_submit(transports=None, seed: int = 0, corpus=None) -> dict:
    """Fuzz mutated transaction CBOR at the HTTP submit-api of every target and
    assert per-node invariants + accept/reject agreement, with REAL observations."""
    transports = transports if transports is not None else submit_transports()
    corpus = corpus if corpus is not None else load_seed_corpus()
    payload = mutate_cbor(corpus[seed % len(corpus)], seed)
    return _drive_differential_with(transports, payload, seed)


def _drive_differential_with(transports, payload: bytes, seed: int) -> dict:
    results = []
    asserted = 0
    for t in transports:
        label = getattr(t, "label", "node")
        obs = t.send(payload)
        asserted += _assert_node(label, obs, payload, seed)
        results.append((label, obs))
    accepts = {label: obs["accepted"] for label, obs in results}
    agree = len(set(accepts.values())) <= 1
    always(agree, "implementations agree on accept/reject of fuzzed tx CBOR",
           {"accepts": accepts, "seed": seed,
            "reasons": {l: o.get("reason") for l, o in results}})
    asserted += 1

    # THE differential that matters: do the implementations agree on whether the
    # bytes DECODE as a transaction? A split (one decoded, one didn't) is a
    # CDDL-conformance divergence = a real finding. Ignore None (unclassified).
    decodes = {label: obs.get("decoded") for label, obs in results}
    known = {l: d for l, d in decodes.items() if d is not None}
    decode_agree = len(set(known.values())) <= 1
    always(decode_agree, "implementations agree on whether fuzzed CBOR decodes as a tx",
           {"decoded": decodes, "seed": seed,
            "reasons": {l: o.get("reason") for l, o in results}})
    asserted += 1
    reachable("workload drove one submit-api differential frame",
              {"targets": len(transports), "seed": seed})
    return {"assertions": asserted, "targets": len(transports), "agree": agree,
            "decode_agree": decode_agree, "panic": any(o["panic"] for _, o in results),
            "detail": {l: {"accepted": o["accepted"], "decoded": o.get("decoded"),
                           "status": o.get("status"), "reason": (o.get("reason") or "")[:80]}
                       for l, o in results}}


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv and argv[0] == "drive-once":
        print(drive_once(seed=int(argv[1]) if len(argv) > 1 else 0))
        return 0
    if argv and argv[0] == "drive-differential":
        print(drive_differential(seed=int(argv[1]) if len(argv) > 1 else 0))
        return 0
    if argv and argv[0] == "drive-submit":
        print(drive_submit(seed=int(argv[1]) if len(argv) > 1 else 0))
        return 0
    print("usage: workload.py {drive-once|drive-differential|drive-submit} [seed]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
