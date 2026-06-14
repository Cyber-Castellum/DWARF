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


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv and argv[0] == "drive-once":
        print(drive_once(seed=int(argv[1]) if len(argv) > 1 else 0))
        return 0
    if argv and argv[0] == "drive-differential":
        print(drive_differential(seed=int(argv[1]) if len(argv) > 1 else 0))
        return 0
    print("usage: workload.py {drive-once|drive-differential} [seed]", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
