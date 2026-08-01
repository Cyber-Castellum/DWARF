"""Antithesis workload driver: loop the DWARF submit-api fuzzer against every
target, forever. Antithesis controls the schedule + injects faults; each pass
sends one mutated transaction and registers assertions from its real observation.

Entropy: inside the sim we draw the per-iteration seed from antithesis_random
(deterministic-yet-varied under the Antithesis hypervisor); locally we fall back
to a plain counter so the container is runnable with `docker compose up`.
"""
import os
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


def main():
    targets = os.environ.get("WORKLOAD_TARGETS", "amaru=amaru-baked:3011")
    os.environ["WORKLOAD_TARGETS"] = targets
    delay = float(os.environ.get("WORKLOAD_DELAY_SECS", "0.2"))
    print(f"[driver] fuzzing submit-api targets={targets} corpus={os.environ.get('WORKLOAD_CORPUS')}",
          flush=True)
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
