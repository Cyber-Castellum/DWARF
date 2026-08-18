#!/usr/bin/env python3
"""Bring up the upstream cardano_amaru topology, gated on producer convergence.

The topology's producers occasionally split-brain at startup: they begin forging
before the peer mesh converges, and with dense blocks (activeSlotsCoeff=0.2) the
divergence can exceed the security parameter k and never heal — a non-deterministic
startup race (one producer forges its own chain from ~genesis; once it is >k from the
rest, honest nodes correctly refuse the deep rollback and the split is permanent).

This wraps the bring-up so consensus scenarios run on a converged base: it starts the
topology, verifies p1/p2/p3 stay within a small block spread (converged on one chain,
allowing normal 1-2 block lag), and on a detected split tears down (`down -v`) and
retries. A huge spread (e.g. 43 vs 4462) is the split signal; a spread of <= tolerance
is healthy lag.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time


def _run(args: list[str], timeout: float = 300) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout)


def _tip(container: str, magic: int) -> tuple[int | None, str | None]:
    p = _run(
        ["docker", "exec", container, "cardano-cli", "query", "tip",
         "--testnet-magic", str(magic), "--socket-path", "/state/node.socket"],
        timeout=20,
    )
    try:
        d = json.loads(p.stdout)
        return d.get("block"), d.get("hash")
    except Exception:
        return None, None


def _compose(topo_dir: str, compose_file: str, *args: str) -> subprocess.CompletedProcess:
    cmd = f"cd {topo_dir} && INTERNAL_NETWORK=false docker compose -f {compose_file} {' '.join(args)}"
    return _run(["bash", "-c", cmd], timeout=600)


def _producer_state(producers: list[str], magic: int) -> tuple[bool, int, dict]:
    tips = {c: _tip(c, magic) for c in producers}
    blocks = [b for b, _ in tips.values() if b is not None]
    if len(blocks) < len(producers):
        return False, -1, tips  # not all reporting yet
    spread = max(blocks) - min(blocks)
    return True, spread, tips


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--topo-dir", default="/home/dwarf/cardano-node-antithesis")
    ap.add_argument("--compose-file", default="testnets/cardano_amaru/docker-compose.yaml")
    ap.add_argument("--network-magic", type=int, default=42)
    ap.add_argument("--producers", default="p1,p2,p3")
    ap.add_argument("--min-block", type=int, default=20, help="Minimum block height before judging convergence.")
    ap.add_argument("--spread-tolerance", type=int, default=4, help="Max block spread across producers to count as converged.")
    ap.add_argument("--stable-checks", type=int, default=3, help="Consecutive in-tolerance checks required.")
    ap.add_argument("--converge-timeout", type=float, default=420)
    ap.add_argument("--max-attempts", type=int, default=4)
    ap.add_argument("--poll", type=float, default=15)
    ap.add_argument("--fresh", action="store_true", help="down -v before the first up.")
    args = ap.parse_args(argv)
    producers = [c.strip() for c in args.producers.split(",") if c.strip()]

    for attempt in range(1, args.max_attempts + 1):
        if attempt > 1 or args.fresh:
            print(f"[attempt {attempt}] down -v (clean slate)", flush=True)
            _compose(args.topo_dir, args.compose_file, "down", "-v")
        print(f"[attempt {attempt}/{args.max_attempts}] up -d", flush=True)
        _compose(args.topo_dir, args.compose_file, "up", "-d")
        start = time.monotonic()
        stable = 0
        while time.monotonic() - start < args.converge_timeout:
            all_up, spread, tips = _producer_state(producers, args.network_magic)
            blocks = [b for b, _ in tips.values() if b is not None]
            minb = min(blocks) if blocks else -1
            if all_up and spread <= args.spread_tolerance and minb >= args.min_block:
                stable += 1
                print(f"  in-tolerance ({stable}/{args.stable_checks}) spread={spread} tips="
                      f"{ {c:(b) for c,(b,_) in tips.items()} }", flush=True)
                if stable >= args.stable_checks:
                    print(f"CONVERGED after attempt {attempt}: spread={spread}, min_block={minb}", flush=True)
                    return 0
            else:
                stable = 0
                print(f"  not-yet spread={spread} min_block={minb} tips="
                      f"{ {c:(b) for c,(b,_) in tips.items()} }", flush=True)
                # early split detection: huge spread with a real chain -> abandon this attempt fast
                if all_up and spread > 50 and minb >= 0 and (max(blocks) >= args.min_block):
                    print(f"  SPLIT-BRAIN detected (spread={spread}) — abandoning attempt {attempt}", flush=True)
                    break
            time.sleep(args.poll)
        else:
            print(f"  timed out after {args.converge_timeout}s without stable convergence", flush=True)

    print("FAILED: topology never converged after all attempts", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
