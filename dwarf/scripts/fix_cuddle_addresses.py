#!/usr/bin/env python3
"""Post-process cuddle-generated CBOR to inject structurally-valid Cardano addresses.

Why: the Cardano CDDL models `address = bytes` (opaque), so cuddle emits random short
byte strings where a real address is expected. The Amaru/pallas decoder validates the
address's internal structure (header nibble + credential length), so block/tx-body seeds
are rejected with `invalid address` (MissingHeader / InvalidAddressLength) before reaching
deep decode. This walks the known tx-body / block structure and replaces address fields
with a valid **enterprise, testnet** address (header 0x60 + 28-byte key hash = 29 bytes),
and reward_account fields with a valid **stake** address (header 0xe0 + 28 bytes).

Structural-only: this keeps the seeds structurally valid (they now decode); it does NOT make
them consensus-valid. See dwarf/docs/cuddle-relationship.md.

Usage:
  python3 dwarf/scripts/fix_cuddle_addresses.py <in.cbor> <out.cbor> --kind tx-body|block
  python3 dwarf/scripts/fix_cuddle_addresses.py --dir <seeds_dir> --kind tx-body   # in place-ish
"""
from __future__ import annotations

import argparse
import glob
from pathlib import Path

import cbor2  # from dwarf/scripts/requirements-ci.txt-adjacent tooling; pip install cbor2

# Valid enterprise (type 6) address, network 0 (testnet): 0x60 + 28-byte key hash.
VALID_ADDR = bytes([0x60] + [0x11] * 28)
# Valid stake (type 14) reward account, testnet: 0xe0 + 28-byte key hash.
VALID_REWARD = bytes([0xE0] + [0x22] * 28)
# Valid 32-byte hash (for CDDL fields that use bare `bytes` where the decoder wants hash32).
VALID_HASH32 = bytes([0x33] * 32)


def fix_pool_metadata(cert):
    """A pool_registration cert = [3, operator, vrf_keyhash, ..., pool_metadata].
    The Conway CDDL defines `pool_metadata = [url, bytes]` (bare bytes) instead of
    `[url, hash32]`, so cuddle emits a wrong-size metadata hash. Resize it to 32B."""
    if (isinstance(cert, list) and len(cert) >= 10 and cert[0] == 3
            and isinstance(cert[9], list) and len(cert[9]) >= 2):
        cert[9][1] = VALID_HASH32
    return cert


def fix_output(out):
    """An output is [address, value, ...] (legacy) or {0: address, 1: value, ...} (Babbage+)."""
    if isinstance(out, list) and out:
        out[0] = VALID_ADDR
    elif isinstance(out, dict) and 0 in out:
        out[0] = VALID_ADDR
    return out


def fix_tx_body(tb):
    """tx_body is a map: key 1 = outputs, key 16 = collateral_return, key 5 = withdrawals."""
    if not isinstance(tb, dict):
        return tb
    if 1 in tb and isinstance(tb[1], list):
        tb[1] = [fix_output(o) for o in tb[1]]
    if 16 in tb:                      # collateral_return: a single output
        tb[16] = fix_output(tb[16])
    if 5 in tb and isinstance(tb[5], dict):   # withdrawals: {reward_account(bytes): coin}
        tb[5] = {VALID_REWARD: v for v in tb[5].values()}
    if 4 in tb and isinstance(tb[4], list):   # certificates: fix pool_registration metadata hash
        tb[4] = [fix_pool_metadata(c) for c in tb[4]]
    return tb


def fix_block(blk):
    """block = [header, [tx_bodies], [witness_sets], aux, invalid_txs]. Fix the tx bodies."""
    if isinstance(blk, list) and len(blk) >= 2 and isinstance(blk[1], list):
        blk[1] = [fix_tx_body(tb) for tb in blk[1]]
    return blk


FIXERS = {"tx-body": fix_tx_body, "block": fix_block}


def process_file(inp: Path, outp: Path, kind: str) -> None:
    obj = cbor2.loads(inp.read_bytes())
    fixed = FIXERS[kind](obj)
    # canonical=False keeps definite-length encoding similar to input
    outp.write_bytes(cbor2.dumps(fixed))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Inject valid Cardano addresses into cuddle CBOR seeds")
    ap.add_argument("infile", nargs="?")
    ap.add_argument("outfile", nargs="?")
    ap.add_argument("--kind", required=True, choices=sorted(FIXERS))
    ap.add_argument("--dir", help="process every <kind>-s*.cbor in this dir, writing *-fixed.cbor")
    args = ap.parse_args(argv)

    if args.dir:
        n = 0
        for f in sorted(glob.glob(f"{args.dir}/{args.kind}-s*.cbor")):
            p = Path(f)
            process_file(p, p.with_name(p.stem + "-fixed.cbor"), args.kind)
            n += 1
        print(f"fixed {n} {args.kind} seeds in {args.dir}")
        return 0

    if not args.infile or not args.outfile:
        ap.error("need infile and outfile (or --dir)")
    process_file(Path(args.infile), Path(args.outfile), args.kind)
    print(f"wrote {args.outfile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
