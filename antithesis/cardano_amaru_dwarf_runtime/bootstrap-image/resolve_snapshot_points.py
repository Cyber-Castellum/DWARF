#!/usr/bin/env python3
"""Resolve Amaru's three epoch-end snapshot points from db-analyser output."""
import argparse
import re
import sys
from dataclasses import dataclass


ROW_RE = re.compile(r"BlockNo\s+(\d+)\s+SlotNo\s+(\d+)\s+([0-9a-f]{64})\s*$")


@dataclass(frozen=True)
class Row:
    block_no: int
    slot: int
    block_hash: str


def parse_rows(text):
    by_block = {}
    for line in text.splitlines():
        if not re.search(r"\bBlockNo\s+", line):
            continue
        match = ROW_RE.search(line)
        if not match:
            raise ValueError(f"malformed db-analyser block row: {line}")
        row = Row(int(match.group(1)), int(match.group(2)), match.group(3))
        prior = by_block.get(row.block_no)
        if prior is not None and prior != row:
            raise ValueError(f"conflicting rows for block {row.block_no}")
        by_block[row.block_no] = row
    if not by_block:
        raise ValueError("no block rows in db-analyser output")
    return sorted(by_block.values(), key=lambda row: row.block_no)


def select_points(rows, epoch_length, count=3):
    if epoch_length <= 0:
        raise ValueError("epoch length must be positive")
    if not rows or max(row.slot for row in rows) < epoch_length * count:
        count_name = "three" if count == 3 else str(count)
        raise ValueError(f"chain has fewer than {count_name} completed epochs")
    by_block = {row.block_no: row for row in rows}
    points = []
    for epoch in range(count):
        start, end = epoch * epoch_length, (epoch + 1) * epoch_length
        candidates = [row for row in rows if start <= row.slot < end]
        if not candidates:
            raise ValueError(f"epoch {epoch} contains no blocks")
        tip = max(candidates, key=lambda row: row.block_no)
        parent = by_block.get(tip.block_no - 1)
        if parent is None:
            raise ValueError(f"missing parent for epoch {epoch} tip block {tip.block_no}")
        points.append(f"{tip.slot}.{tip.block_hash}::{parent.slot}.{parent.block_hash}")
    return points


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--epoch-length", type=int, required=True)
    parser.add_argument("path", nargs="?")
    args = parser.parse_args(argv)
    text = open(args.path, encoding="utf-8").read() if args.path else sys.stdin.read()
    print("\n".join(select_points(parse_rows(text), args.epoch_length)))


if __name__ == "__main__":
    main()
