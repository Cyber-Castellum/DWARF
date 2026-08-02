"""Antithesis workload driver: loop the DWARF submit-api fuzzer against every
target, forever. Antithesis controls the schedule + injects faults; each pass
sends one mutated transaction and registers assertions from its real observation.

Entropy: inside the sim we draw the per-iteration seed from antithesis_random
(deterministic-yet-varied under the Antithesis hypervisor); locally we fall back
to a plain counter so the container is runnable with `docker compose up`.
"""
import os
import socket
import time

import workload

try:
    from antithesis.random import get_random  # provided by the SDK in-sim
    def _seed():
        return get_random() & 0xFFFFFFFF
except Exception:
    _ctr = 0
    def _seed():
        global _ctr
        _ctr += 1
        return _ctr

try:
    # Signals Antithesis that the system under test is initialized, so it may
    # start injecting faults and scoring. Without it a run never leaves setup.
    from antithesis.lifecycle import setup_complete as _setup_complete
except Exception:
    def _setup_complete(details):  # local dry-run: no-op
        pass


def _target_addrs(targets: str):
    """Parse WORKLOAD_TARGETS ('name=host:port,...') into [(name, host, port)]."""
    out = []
    for entry in targets.split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, _, hostport = entry.partition("=")
        host, _, port = hostport.rpartition(":")
        try:
            out.append((name or hostport, host, int(port)))
        except ValueError:
            continue
    return out


def _wait_ready_then_signal_setup(targets: str, timeout: float = 300.0):
    """Block until every target's submit-api TCP port is reachable, then emit the
    Antithesis setup-complete signal exactly once. Amaru/cardano-submit-api open
    their listener only after the node has loaded, so this gates fuzzing on a real
    system-ready state rather than a fixed sleep."""
    addrs = _target_addrs(targets)
    deadline = time.time() + timeout
    pending = list(addrs)
    while pending and time.time() < deadline:
        still = []
        for name, host, port in pending:
            try:
                with socket.create_connection((host, port), timeout=3):
                    print(f"[driver] target ready: {name} {host}:{port}", flush=True)
            except OSError:
                still.append((name, host, port))
        pending = still
        if pending:
            time.sleep(2)
    _setup_complete({"targets": [f"{h}:{p}" for _, h, p in addrs],
                     "unreachable": [f"{h}:{p}" for _, h, p in pending]})
    print(f"[driver] setup_complete signalled (unreachable={len(pending)})", flush=True)


def main():
    targets = os.environ.get("WORKLOAD_TARGETS", "amaru=amaru-baked:3011")
    os.environ["WORKLOAD_TARGETS"] = targets
    delay = float(os.environ.get("WORKLOAD_DELAY_SECS", "0.2"))
    print(f"[driver] fuzzing submit-api targets={targets} corpus={os.environ.get('WORKLOAD_CORPUS')}",
          flush=True)
    _wait_ready_then_signal_setup(targets)
    while True:
        try:
            result = workload.drive_submit(seed=_seed())
            if result.get("panic"):
                print(f"[driver] PANIC observed: {result}", flush=True)
        except Exception as e:  # never let the driver itself die
            print(f"[driver] iteration error: {type(e).__name__}: {e}", flush=True)
        time.sleep(delay)


if __name__ == "__main__":
    main()
