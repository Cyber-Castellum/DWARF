#!/usr/bin/env python3
"""Generate DWARF fuzz seed corpora from official Cardano CDDL via `cuddle`.

Phase-1 ("shallow") cuddle integration — see dwarf/docs/cuddle-relationship.md.
This is an OFFLINE step: it drives the `cuddle` CLI to emit spec-valid CBOR for
named CDDL rules and writes them as seed files DWARF's fuzzers consume via
--seed-dir. It does NOT change DWARF's mutation engine or scenario format.

Structural validity only: cuddle produces CBOR that matches the CDDL shape. It
does NOT produce consensus-valid crypto (VRF/signatures/hashes) — that stays
DWARF-native (see the relationship doc's "structural vs semantic" ceiling).

Requirements:
  - a built `cuddle` binary (>= 1.8). Point to it with --cuddle-bin or $CUDDLE_BIN,
    or have `cuddle` on PATH. Build: `cabal build exe:cuddle` in the cuddle repo
    (GHC 9.6/9.8/9.10/9.12).
  - the Cardano CDDL for the era you want (e.g. cardano-ledger conway.cddl).

Generation uses `--no-twiddle` (definite-length / canonical-ish encodings, which
Cardano decoders expect) and deterministic `-s <seed>` so corpora are reproducible.

Example:
  python3 dwarf/scripts/gen_cuddle_seeds.py \
      --cuddle-bin /path/to/cuddle \
      --cddl /path/to/conway.cddl \
      --out-root dwarf/corpora/cuddle-generated \
      --count 16
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# DWARF shape -> Cardano CDDL rule name (Conway-era ledger CDDL).
DEFAULT_SHAPE_RULES = {
    "block-header": "header",
    "block": "block",
    "tx-body": "transaction_body",
    "certificate": "certificate",
    "auxiliary-data": "auxiliary_data",
}

# --wire: which existing coverage-guided fuzz corpora each shape's valid seeds feed
# into (relative to the repo root). These are the --seed-dir corpora the AFL++ /
# cargo-fuzz primitives consume; cuddle seeds are copied in with a `cuddle-` prefix
# (non-destructive — existing hand seeds are kept). certificate / auxiliary-data have
# no coverage-guided corpus yet, so they stay in cuddle-generated/ until one exists.
DEFAULT_CORPUS_WIRING = {
    "block-header": [
        "dwarf/corpora/afl/package-a/block-header-stage1/seeds",
        "dwarf/corpora/cargo-fuzz/package-a/block-header-stage1/seeds",
    ],
    "tx-body": [
        "dwarf/corpora/afl/package-a/tx-body-stage1/seeds",
        "dwarf/corpora/cargo-fuzz/package-a/tx-body-stage1/seeds",
    ],
    "block": [
        "dwarf/corpora/amaru-cargo-fuzz-block/seeds",
    ],
}


def find_cuddle(explicit: str | None) -> str:
    cand = explicit or os.environ.get("CUDDLE_BIN") or shutil.which("cuddle")
    if not cand or not Path(cand).exists():
        sys.exit("error: cuddle binary not found. Pass --cuddle-bin or set $CUDDLE_BIN "
                 "or put `cuddle` on PATH (build with `cabal build exe:cuddle`).")
    return str(cand)


def cuddle_version(cuddle: str) -> str:
    try:
        return subprocess.run([cuddle, "--version"], capture_output=True, text=True,
                              timeout=30).stdout.strip().splitlines()[0]
    except Exception:
        return "unknown"


def gen_one(cuddle: str, cddl: Path, rule: str, seed: int, out_file: Path,
            negative: bool = False) -> bool:
    """Generate one CBOR term for `rule` at `seed` into out_file. Returns success."""
    cmd = [cuddle, "gen", "-s", str(seed), "--no-twiddle", "-f", "binary",
           "-o", str(out_file)]
    if negative:
        cmd.append("-n")
    cmd += [rule, str(cddl)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return proc.returncode == 0 and out_file.exists() and out_file.stat().st_size > 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate DWARF fuzz seeds from Cardano CDDL via cuddle")
    ap.add_argument("--cuddle-bin", help="path to the cuddle executable (or set $CUDDLE_BIN)")
    ap.add_argument("--cddl", help="path to the Cardano CDDL file (e.g. conway.cddl); "
                                    "required unless --wire-only")
    ap.add_argument("--out-root", required=True, help="root dir for generated corpora")
    ap.add_argument("--count", type=int, default=16, help="valid seeds per shape (default 16)")
    ap.add_argument("--negative", type=int, default=0,
                    help="ALSO generate N negative/invalid ('zapped') examples per shape")
    ap.add_argument("--seed-start", type=int, default=1, help="first seed value (default 1)")
    ap.add_argument("--era", default="conway", help="era label recorded in the manifest")
    ap.add_argument("--wire", action="store_true",
                    help="after generating, copy valid seeds into the coverage-guided fuzz "
                         "corpora (DEFAULT_CORPUS_WIRING), non-destructively")
    ap.add_argument("--repo-root", default=".",
                    help="repo root for --wire destination paths (default: cwd)")
    ap.add_argument("--wire-only", action="store_true",
                    help="skip generation; just wire existing --out-root seeds into the corpora "
                         "(runs without cuddle)")
    args = ap.parse_args(argv)

    if args.wire_only:
        return _wire(Path(args.out_root), Path(args.repo_root))

    if not args.cddl:
        ap.error("--cddl is required unless --wire-only")
    cuddle = find_cuddle(args.cuddle_bin)
    cddl = Path(args.cddl).resolve()
    if not cddl.exists():
        sys.exit(f"error: CDDL file not found: {cddl}")
    out_root = Path(args.out_root)
    version = cuddle_version(cuddle)

    manifest = {
        "generator": "cuddle",
        "cuddle_version": version,
        "cddl_file": cddl.name,
        "era": args.era,
        "shape_rules": DEFAULT_SHAPE_RULES,
        "count": args.count,
        "negative": args.negative,
        "seed_start": args.seed_start,
        "note": "structurally-valid CBOR only; not consensus-valid (no real VRF/sigs/hashes)",
        "corpora": {},
    }

    total_ok = total_fail = 0
    for shape, rule in DEFAULT_SHAPE_RULES.items():
        seed_dir = out_root / shape / "seeds"
        seed_dir.mkdir(parents=True, exist_ok=True)
        ok = fail = 0
        for i in range(args.count):
            seed = args.seed_start + i
            out_file = seed_dir / f"{shape}-s{seed:04d}.cbor"
            if gen_one(cuddle, cddl, rule, seed, out_file):
                ok += 1
            else:
                fail += 1
        # optional negative examples
        for i in range(args.negative):
            seed = args.seed_start + i
            out_file = seed_dir / f"{shape}-neg-s{seed:04d}.cbor"
            gen_one(cuddle, cddl, rule, seed, out_file, negative=True)
        manifest["corpora"][shape] = {"rule": rule, "seeds_ok": ok, "seeds_fail": fail,
                                      "dir": str(seed_dir)}
        total_ok += ok
        total_fail += fail
        print(f"  {shape:16} rule={rule:20} {ok} ok, {fail} fail  -> {seed_dir}")

    (out_root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\ncuddle: {version}")
    print(f"total: {total_ok} seeds generated, {total_fail} failed")
    print(f"manifest: {out_root / 'manifest.json'}")

    if args.wire:
        _wire(out_root, Path(args.repo_root))

    return 0 if total_fail == 0 else 1


def _wire(out_root: Path, repo: Path) -> int:
    """Copy already-generated valid seeds into the coverage-guided fuzz corpora."""
    print("wiring valid cuddle seeds into coverage-guided fuzz corpora")
    total = 0
    for shape, dests in DEFAULT_CORPUS_WIRING.items():
        srcs = sorted((out_root / shape / "seeds").glob(f"{shape}-s*.cbor"))  # valid only (skip -neg-)
        if not srcs:
            print(f"  {shape:16} no generated seeds in {out_root/shape/'seeds'} — run generation first")
            continue
        for dest_rel in dests:
            dest = repo / dest_rel
            if not dest.parent.parent.exists():
                print(f"  {shape:16} skip {dest_rel} (corpus not present)")
                continue
            dest.mkdir(parents=True, exist_ok=True)
            for s in srcs:
                shutil.copy2(s, dest / f"cuddle-{s.name}")
                total += 1
            print(f"  {shape:16} -> {dest_rel}  (+{len(srcs)})")
    print(f"wired {total} seed copies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
