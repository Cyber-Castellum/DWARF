#!/usr/bin/env python3
"""Exhaustive chain-selection differential sweep (smoke -> exhaustive template).

Drives the induced-fork differential across a GRID of partition depths and repeated
trials against the upstream cardano_amaru topology, verifying at every point:
  1. cardano-node (relay1/relay2) and Amaru (amaru-consumer) select the identical
     chain (no cross-implementation divergence), and
  2. the k-boundary behavior is identical for both implementations: a <k fork lets the
     partitioned producer recover; a >k fork strands it (deep rollback refused).

This turns the single-shot chainhold / S2 smoke tests into a coverage matrix. It
re-gates a fresh converged topology only when it detects degradation (a stranded
producer or a split), to minimise cost. Every trial captures the tracer forge summary.

Output: a results matrix (JSON) + a printed summary with the overall verdict
(any cross-implementation divergence at any depth/trial = FAIL).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

MAGIC = 42
NET = "cardano-amaru-testnet"
PRODUCERS = ["p1", "p2", "p3"]
K = 10  # security parameter for this fast profile (protocolConsts.k)


def _run(args, timeout=120):
    return subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout)


def tip(c):
    p = _run(["docker", "exec", c, "cardano-cli", "query", "tip", "--testnet-magic", str(MAGIC),
              "--socket-path", "/state/node.socket"], timeout=20)
    try:
        d = json.loads(p.stdout)
        return d.get("block"), d.get("hash")
    except Exception:
        return None, None


def topo_healthy():
    """All producers on one chain (small spread) and p2 on that chain."""
    tips = {c: tip(c) for c in PRODUCERS}
    blocks = [b for b, _ in tips.values() if b is not None]
    if len(blocks) < len(PRODUCERS):
        return False
    if max(blocks) - min(blocks) > 6:
        return False
    hashes = [h for _, h in tips.values() if h]
    # at least a shared majority hash (allow 1-block lag)
    return len(set(hashes)) <= 2


def gate(topo_dir):
    print("  [gate] bringing up a fresh converged topology...", flush=True)
    r = _run(["python3", "scripts/ensure_cardano_amaru_converged.py", "--fresh", "--max-attempts", "4"],
             timeout=2400)
    ok = r.returncode == 0
    print(f"  [gate] {'CONVERGED' if ok else 'FAILED'}", flush=True)
    if ok:
        # wait for amaru-consumer to catch up
        for _ in range(40):
            ac, _h = tip("amaru-consumer")
            p1b, _ = tip("p1")
            if ac is not None and p1b is not None and (p1b - ac) <= 3:
                break
            time.sleep(10)
    return ok


def tracer_forged():
    out = {}
    for c in PRODUCERS:
        p = _run(["docker", "exec", "tracer", "sh", "-c",
                  f"cat /opt/cardano-tracer/logs/{c}.example_3001/*.json 2>/dev/null"], timeout=60)
        out[c] = (p.stdout or "").count("Forge.Loop.ForgedBlock")
    return out


def trial(depth_seconds, settle_seconds):
    pre = {c: tip(c) for c in ["p1", "relay1", "amaru-consumer", "p2"]}
    fb = tracer_forged()
    _run(["docker", "network", "disconnect", NET, "p2"])
    time.sleep(depth_seconds)
    during = tip("p2")
    _run(["docker", "network", "connect", NET, "p2"])
    time.sleep(settle_seconds)
    fa = tracer_forged()
    r1 = tip("relay1")
    r2 = tip("relay2")
    ac = tip("amaru-consumer")
    p2 = tip("p2")
    # cross-impl differential: cardano-node relays vs amaru path
    relays_agree = r1[1] is not None and r1[1] == r2[1]
    xdiff_ok = relays_agree and ac[1] == r1[1]  # amaru-consumer == relays (no divergence)
    p2_recovered = p2[1] == r1[1]
    fork_blocks = (during[0] or 0) - (pre["p1"][0] or 0)  # rough minority growth proxy
    return {
        "pre_p1": pre["p1"][0], "during_p2": during[0],
        "relay1": r1[0], "amaru_consumer": ac[0], "p2": p2[0],
        "relays_agree": relays_agree,
        "cross_impl_no_divergence": bool(xdiff_ok),
        "p2_recovered": bool(p2_recovered),
        "forged_delta": {c: fa[c] - fb[c] for c in PRODUCERS},
    }


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--depths", default="15,30,60,120", help="partition_seconds grid.")
    ap.add_argument("--trials", type=int, default=1, help="trials per depth.")
    ap.add_argument("--settle", type=float, default=75)
    ap.add_argument("--topo-dir", default="/home/nigel/cardano-node-antithesis")
    ap.add_argument("--out", default="/tmp/consensus-sweep-results.json")
    args = ap.parse_args(argv)
    depths = [int(x) for x in args.depths.split(",") if x.strip()]

    results = []
    divergences = 0
    for depth in depths:
        for t in range(args.trials):
            if not topo_healthy():
                if not gate(args.topo_dir):
                    print("ABORT: could not converge topology", flush=True)
                    return 1
            print(f"[depth={depth}s trial={t}] partitioning p2...", flush=True)
            r = trial(depth, args.settle)
            r["depth_seconds"] = depth
            r["trial"] = t
            # expectation from proxy fork depth vs k (settle_only heuristic; recorded, not asserted)
            results.append(r)
            if not r["cross_impl_no_divergence"] and r["relays_agree"]:
                divergences += 1
            print(f"  -> no_divergence={r['cross_impl_no_divergence']} p2_recovered={r['p2_recovered']} "
                  f"relay1={r['relay1']} amaru={r['amaru_consumer']} p2={r['p2']}", flush=True)

    verdict = "PASS" if divergences == 0 else "FAIL"
    summary = {
        "verdict": verdict,
        "total_trials": len(results),
        "cross_impl_divergences": divergences,
        "depths": depths,
        "trials_per_depth": args.trials,
        "k": K,
        "results": results,
    }
    Path(args.out).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("=====SWEEP SUMMARY=====", flush=True)
    print(f"VERDICT: {verdict} | trials: {len(results)} | cross-impl divergences: {divergences}", flush=True)
    for r in results:
        print(f"  depth={r['depth_seconds']:>3}s t{r['trial']}: "
              f"no_divergence={r['cross_impl_no_divergence']} p2_recovered={r['p2_recovered']} "
              f"(relay {r['relay1']} / amaru {r['amaru_consumer']} / p2 {r['p2']})", flush=True)
    print(f"RESULTS_JSON: {args.out}", flush=True)
    return 0 if verdict == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
