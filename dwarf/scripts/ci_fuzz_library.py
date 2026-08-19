#!/usr/bin/env python3
"""DWARF library-tier fuzz runner for CI.

Runs every registered decoder-harness target (dwarf/targets/manifests/*) over a
bounded, deterministic stream of mutated inputs and enforces the harness contract:

    exit 0, stdout "OK"      -> input parsed cleanly
    exit 1, stdout "ERR ..." -> input rejected with a clean decode error
    anything else            -> CRASH (panic / abort / signal / hang)

A CRASH fails the run (exit 1) and the offending input is saved for triage.

This is stdlib-only on purpose: CI does not need the full DWARF runtime, only the
built harness binaries. Seeds are derived deterministically from --seed so a failing
run reproduces exactly.

Usage:
    ci_fuzz_library.py --iterations 500 --out report.json --crashers-dir crashers
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_manifest(path: Path) -> dict | None:
    """Manifests are single-line JSON (despite the .yaml suffix)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def discover_targets(manifests_dir: Path, only_impl: set[str] | None, name_filter: str | None) -> list[dict]:
    targets = []
    for path in sorted(manifests_dir.glob("*.yaml")) + sorted(manifests_dir.glob("*.json")):
        m = _load_manifest(path)
        if not m or "binary" not in m or m.get("input_format") != "stdin_bytes":
            continue
        if only_impl and m.get("implementation") not in only_impl:
            continue
        if name_filter and name_filter not in m.get("id", ""):
            continue
        binary = (REPO_ROOT / m["binary"]).resolve()
        if not binary.exists() or not os.access(binary, os.X_OK):
            m["_skip_reason"] = f"binary not built: {m['binary']}"
        m["_binary"] = str(binary)
        targets.append(m)
    return targets


# A few structural CBOR anchors to mutate from, so we probe real decode paths and
# not only random noise (random bytes almost always bounce off the first type check).
_ANCHORS = [
    b"",
    b"\x80", b"\x81\x80", b"\x82\x80\x80",           # arrays
    b"\xa0", b"\xa1\x00\x00",                          # maps
    b"\x9f\xff", b"\xbf\xff",                          # indefinite array/map
    b"\x40", b"\x58\x20" + b"\x00" * 32,              # byte strings
    b"\x1a\x00\x00\x00\x00", b"\x1b" + b"\xff" * 8,   # ints
    b"\xf5", b"\xf4", b"\xf6",                         # bool/null
    bytes(range(24)),
]


def make_input(rng: random.Random, max_bytes: int) -> bytes:
    mode = rng.random()
    if mode < 0.45:                       # mutate a structural anchor
        buf = bytearray(rng.choice(_ANCHORS))
        for _ in range(rng.randint(0, 6)):
            if not buf:
                buf.append(rng.randint(0, 255))
                continue
            op = rng.randint(0, 3)
            i = rng.randrange(len(buf))
            if op == 0:
                buf[i] = rng.randint(0, 255)              # flip
            elif op == 1:
                buf.insert(i, rng.randint(0, 255))        # insert
            elif op == 2 and len(buf) > 1:
                del buf[i]                                # delete
            else:
                buf += bytes(rng.randint(1, 8))           # extend (trailing bytes)
        data = bytes(buf[:max_bytes])
    elif mode < 0.7:                      # short random
        data = bytes(rng.randint(0, 255) for _ in range(rng.randint(1, min(32, max_bytes))))
    else:                                 # longer random
        data = bytes(rng.randint(0, 255) for _ in range(rng.randint(1, max_bytes)))
    return data or b"\x00"


def classify(rc: int, out: bytes) -> str:
    if rc == 0 and out.startswith(b"OK"):
        return "ok"
    if rc == 1 and out.startswith(b"ERR"):
        return "err"
    return "crash"


def run_target(target: dict, iterations: int, max_bytes: int, timeout: float,
               seed: int, crashers_dir: Path) -> dict:
    tid = target.get("id", "unknown")
    result = {"id": tid, "binary": target.get("binary"), "iterations": 0,
              "ok": 0, "err": 0, "crash": 0, "crashers": [], "skipped": None}
    if target.get("_skip_reason"):
        result["skipped"] = target["_skip_reason"]
        return result
    rng = random.Random(f"{seed}:{tid}")
    binary = target["_binary"]
    for i in range(iterations):
        data = make_input(rng, max_bytes)
        try:
            proc = subprocess.run([binary], input=data, capture_output=True, timeout=timeout)
            verdict = classify(proc.returncode, proc.stdout)
        except subprocess.TimeoutExpired:
            verdict = "crash"  # a hang is a liveness failure
            proc = None
        result["iterations"] += 1
        result[verdict] += 1
        if verdict == "crash":
            crashers_dir.mkdir(parents=True, exist_ok=True)
            crasher = crashers_dir / f"{tid}-{i:06d}.bin"
            crasher.write_bytes(data)
            rc = None if proc is None else proc.returncode
            stderr = b"" if proc is None else proc.stderr[:2000]
            result["crashers"].append({
                "input_file": str(crasher.relative_to(REPO_ROOT) if crasher.is_relative_to(REPO_ROOT) else crasher),
                "returncode": rc, "reason": "timeout" if proc is None else "abnormal-exit",
                "stderr": stderr.decode("utf-8", "replace"),
            })
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="DWARF library-tier decoder fuzz for CI")
    ap.add_argument("--manifests-dir", default=str(REPO_ROOT / "dwarf/targets/manifests"))
    ap.add_argument("--iterations", type=int, default=500, help="inputs per target")
    ap.add_argument("--max-bytes", type=int, default=4096)
    ap.add_argument("--timeout", type=float, default=5.0, help="per-input seconds; a hang counts as a crash")
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--impl", default="amaru", help="comma list, or 'all'")
    ap.add_argument("--filter", default=None, help="substring match on target id")
    ap.add_argument("--out", default="dwarf-fuzz-report.json")
    ap.add_argument("--crashers-dir", default="fuzz-crashers")
    args = ap.parse_args(argv)

    only_impl = None if args.impl == "all" else set(args.impl.split(","))
    manifests_dir = Path(args.manifests_dir)
    targets = discover_targets(manifests_dir, only_impl, args.filter)
    if not targets:
        print(f"ERROR: no targets found in {manifests_dir}", file=sys.stderr)
        return 2

    crashers_dir = Path(args.crashers_dir)
    started = time.time()
    reports, total_crash, total_iters, skipped = [], 0, 0, 0
    print(f"DWARF library fuzz: {len(targets)} target(s), {args.iterations} inputs each, seed={args.seed}")
    for t in targets:
        r = run_target(t, args.iterations, args.max_bytes, args.timeout, args.seed, crashers_dir)
        reports.append(r)
        if r["skipped"]:
            skipped += 1
            print(f"  SKIP {r['id']}: {r['skipped']}")
            continue
        total_crash += r["crash"]
        total_iters += r["iterations"]
        flag = "  ok " if r["crash"] == 0 else ">>CRASH"
        print(f"{flag} {r['id']}: ok={r['ok']} err={r['err']} crash={r['crash']}")

    summary = {
        "targets": len(targets), "targets_skipped": skipped,
        "total_iterations": total_iters, "total_crashes": total_crash,
        "duration_seconds": round(time.time() - started, 1),
        "seed": args.seed, "iterations_per_target": args.iterations,
        "reports": reports,
    }
    Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}: {total_iters} inputs, {total_crash} crash(es), "
          f"{skipped} skipped, {summary['duration_seconds']}s")
    if skipped == len(targets):
        print("ERROR: every target was skipped (harnesses not built?)", file=sys.stderr)
        return 2
    return 1 if total_crash else 0


if __name__ == "__main__":
    raise SystemExit(main())
