#!/usr/bin/env python3
"""Generate structurally valid corpus seeds from grammar hints and structure specs."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path


EXIT_INVALID_GRAMMAR = 1
EXIT_GENERATION_FAILURE = 2


class CorpusSynthesizerError(Exception):
    exit_code = EXIT_GENERATION_FAILURE


class CorpusSynthesizerConfigError(CorpusSynthesizerError):
    exit_code = EXIT_INVALID_GRAMMAR


def parse_dictionary_tokens(path: Path) -> list[bytes]:
    tokens: list[bytes] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(stripped) < 2 or not (stripped.startswith('"') and stripped.endswith('"')):
            raise CorpusSynthesizerConfigError(f"invalid dictionary token line: {stripped!r}")
        decoded = bytes(stripped[1:-1], "utf-8").decode("unicode_escape").encode("latin1")
        tokens.append(decoded)
    if not tokens:
        raise CorpusSynthesizerConfigError(f"dictionary contains no tokens: {path}")
    return tokens


def load_structure_spec(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != "cbor":
        raise CorpusSynthesizerConfigError(f"unsupported structure format in {path}: {data.get('format')!r}")
    if not isinstance(data.get("shape"), dict):
        raise CorpusSynthesizerConfigError(f"missing shape in structure spec: {path}")
    return data


def _cbor_uint(value: int) -> bytes:
    if value < 0:
        raise ValueError("uint must be non-negative")
    if value < 24:
        return bytes([value])
    if value < 0x100:
        return bytes([0x18, value])
    if value < 0x10000:
        return bytes([0x19]) + value.to_bytes(2, "big")
    if value < 0x100000000:
        return bytes([0x1A]) + value.to_bytes(4, "big")
    return bytes([0x1B]) + value.to_bytes(8, "big")


def _cbor_text(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return _cbor_head(3, len(encoded)) + encoded


def _cbor_bytes(value: bytes) -> bytes:
    return _cbor_head(2, len(value)) + value


def _cbor_head(major: int, length: int) -> bytes:
    prefix = major << 5
    if length < 24:
        return bytes([prefix | length])
    if length < 0x100:
        return bytes([prefix | 24, length])
    if length < 0x10000:
        return bytes([prefix | 25]) + length.to_bytes(2, "big")
    if length < 0x100000000:
        return bytes([prefix | 26]) + length.to_bytes(4, "big")
    return bytes([prefix | 27]) + length.to_bytes(8, "big")


def cbor_encode(value) -> bytes:
    if value is None:
        return b"\xf6"
    if isinstance(value, bool):
        return b"\xf5" if value else b"\xf4"
    if isinstance(value, int):
        return _cbor_uint(value)
    if isinstance(value, bytes):
        return _cbor_bytes(value)
    if isinstance(value, str):
        return _cbor_text(value)
    if isinstance(value, list):
        return _cbor_head(4, len(value)) + b"".join(cbor_encode(item) for item in value)
    if isinstance(value, dict):
        encoded = [_cbor_head(5, len(value))]
        for key, item in value.items():
            encoded.append(cbor_encode(key))
            encoded.append(cbor_encode(item))
        return b"".join(encoded)
    if isinstance(value, Tag24):
        return b"\xd8\x18" + cbor_encode(value.value)
    raise TypeError(f"unsupported CBOR value: {type(value)!r}")


class Tag24:
    def __init__(self, value: bytes):
        self.value = value


def _handshake_hints(tokens: list[bytes]) -> dict:
    message_keys = set()
    map_sizes = set()
    versions = set()
    bools = set()
    for token in tokens:
        if token in (b"\xf4", b"\xf5"):
            bools.add(token == b"\xf5")
        if len(token) == 1 and token[0] in (0xA0, 0xA1, 0xA2):
            map_sizes.add(token[0] - 0xA0)
        if len(token) == 1 and token[0] <= 0x17:
            versions.add(token[0])
        if len(token) == 2 and token[0] in (0x82, 0x83) and token[1] in (0, 1, 2, 3):
            message_keys.add(token[1])
    return {
        "message_keys": sorted(message_keys) or [0, 1, 2, 3],
        "map_sizes": sorted(map_sizes) or [1, 2],
        "versions": sorted(v for v in versions if v >= 10) or [10, 11, 12],
        "bools": sorted(bools) if bools else [False, True],
    }


def _rand_bool(rng: random.Random, hints: dict) -> bool:
    return hints["bools"][rng.randrange(len(hints["bools"]))]


def _rand_bytes(rng: random.Random, length: int) -> bytes:
    return bytes(rng.getrandbits(8) for _ in range(length))


def _rand_hash32(rng: random.Random) -> bytes:
    return _rand_bytes(rng, 32)


def _rand_small_payload(rng: random.Random, *, min_len: int = 0, max_len: int = 96) -> bytes:
    return _rand_bytes(rng, rng.randint(min_len, max_len))


def _rand_point(rng: random.Random):
    if rng.random() < 0.35:
        return []
    return [rng.randint(0, 2**20), _rand_hash32(rng)]


def _rand_tip(rng: random.Random):
    return [_rand_point(rng), rng.randint(0, 2**20)]


def _version_data(version: int, *, rng: random.Random, hints: dict) -> list:
    network_magic = rng.choice([1, 2, 42, 764824073, 1097911063])
    initiator_only = _rand_bool(rng, hints)
    if version >= 11:
        return [network_magic, initiator_only, rng.choice([0, 1]), _rand_bool(rng, hints)]
    return [network_magic, initiator_only]


def _version_table(*, rng: random.Random, hints: dict) -> dict:
    table_size = rng.choice(hints["map_sizes"])
    available = sorted(set(hints["versions"]) | {10, 11, 12})
    count = min(table_size, len(available))
    chosen = rng.sample(available, count)
    return {version: _version_data(version, rng=rng, hints=hints) for version in sorted(chosen)}


def _refuse_reason(*, rng: random.Random, hints: dict) -> list:
    key = rng.choice([0, 1, 2])
    if key == 0:
        versions = sorted(rng.sample(sorted(set(hints["versions"]) | {10, 11, 12}), rng.choice([1, 2, 3])))
        return [0, versions]
    version = rng.choice(sorted(set(hints["versions"]) | {10, 11, 12}))
    message = rng.choice(["decode error", "version mismatch", "refused"])
    return [key, version, message]


def generate_handshake_message(*, rng: random.Random, tokens: list[bytes], strategy: str) -> tuple[str, bytes]:
    hints = _handshake_hints(tokens)
    if strategy == "dictionary":
        allowed = [key for key in hints["message_keys"] if key in (0, 1, 2, 3)] or [0, 1, 2, 3]
    elif strategy == "structure":
        allowed = [0, 1, 2, 3]
    else:
        raise CorpusSynthesizerConfigError(f"unsupported strategy: {strategy}")

    message_key = rng.choice(allowed)
    if message_key == 0:
        payload = [0, _version_table(rng=rng, hints=hints)]
        kind = "propose"
    elif message_key == 1:
        version = rng.choice(sorted(set(hints["versions"]) | {10, 11, 12}))
        payload = [1, version, _version_data(version, rng=rng, hints=hints)]
        kind = "accept"
    elif message_key == 2:
        payload = [2, _refuse_reason(rng=rng, hints=hints)]
        kind = "refuse"
    else:
        payload = [3, _version_table(rng=rng, hints=hints)]
        kind = "query-reply"
    return kind, cbor_encode(payload)


def generate_blockfetch_message(*, rng: random.Random, strategy: str) -> tuple[str, bytes]:
    if strategy != "structure":
        raise CorpusSynthesizerConfigError(f"unsupported strategy for blockfetch: {strategy}")
    key = rng.choice([0, 1, 2, 3, 4, 5])
    if key == 0:
        payload = [0, _rand_point(rng), _rand_point(rng)]
        kind = "request-range"
    elif key == 4:
        payload = [4, Tag24(_rand_small_payload(rng, min_len=0, max_len=128))]
        kind = "block"
    else:
        payload = [key]
        kind = {
            1: "client-done",
            2: "start-batch",
            3: "no-blocks",
            5: "batch-done",
        }[key]
    return kind, cbor_encode(payload)


def _rand_header_content(rng: random.Random):
    era = rng.choice([1, 2, 3, 4, 5, 6, 7])
    return [era, Tag24(_rand_small_payload(rng, min_len=0, max_len=160))]


def generate_chainsync_message(*, rng: random.Random, strategy: str) -> tuple[str, bytes]:
    if strategy != "structure":
        raise CorpusSynthesizerConfigError(f"unsupported strategy for chainsync: {strategy}")
    key = rng.choice([0, 1, 2, 3, 4, 5, 6, 7])
    if key in (0, 1, 7):
        payload = [key]
        kind = {0: "request-next", 1: "await-reply", 7: "done"}[key]
    elif key == 2:
        payload = [2, _rand_header_content(rng), _rand_tip(rng)]
        kind = "roll-forward"
    elif key in (3, 5):
        payload = [key, _rand_point(rng), _rand_tip(rng)]
        kind = {3: "roll-backward", 5: "intersect-found"}[key]
    elif key == 4:
        points = [_rand_point(rng) for _ in range(rng.randint(0, 4))]
        payload = [4, points]
        kind = "find-intersect"
    else:
        payload = [6, _rand_tip(rng)]
        kind = "intersect-not-found"
    return kind, cbor_encode(payload)


def _rand_txid(rng: random.Random):
    return _rand_hash32(rng)


def generate_txsubmission_message(*, rng: random.Random, strategy: str) -> tuple[str, bytes]:
    if strategy != "structure":
        raise CorpusSynthesizerConfigError(f"unsupported strategy for txsubmission: {strategy}")
    key = rng.choice([0, 1, 2, 3, 4, 6])
    if key == 0:
        payload = [0, rng.choice([False, True]), rng.randint(0, 32), rng.randint(0, 32)]
        kind = "init"
    elif key == 1:
        pairs = [[_rand_txid(rng), rng.randint(0, 65535)] for _ in range(rng.randint(0, 4))]
        payload = [1, pairs]
        kind = "reply-txids"
    elif key == 2:
        payload = [2, [_rand_txid(rng) for _ in range(rng.randint(0, 4))]]
        kind = "request-txids"
    elif key == 3:
        payload = [3, [Tag24(_rand_small_payload(rng, min_len=0, max_len=192)) for _ in range(rng.randint(0, 3))]]
        kind = "reply-txs"
    elif key == 4:
        payload = [4]
        kind = "request-txs"
    else:
        payload = [6]
        kind = "done"
    return kind, cbor_encode(payload)


def _rand_tx_input(rng: random.Random):
    return [_rand_hash32(rng), rng.randint(0, 8)]


def generate_ledger_fees_case(*, rng: random.Random, strategy: str) -> tuple[str, bytes]:
    if strategy != "structure":
        raise CorpusSynthesizerConfigError(f"unsupported strategy for ledger-fees: {strategy}")
    is_valid = rng.choice([False, True])
    collateral_count = 0 if is_valid else rng.randint(0, 3)
    collateral = [_rand_tx_input(rng) for _ in range(collateral_count)]
    collateral_return = None if rng.random() < 0.5 else rng.randint(0, 2_000_000)
    collateral_utxos = [[_rand_tx_input(rng), rng.randint(0, 5_000_000)] for _ in range(rng.randint(0, 3))]
    payload = [
        is_valid,
        rng.randint(0, 2_000_000),
        collateral,
        collateral_return,
        collateral_utxos,
        rng.randint(0, 100_000),
    ]
    kind = "phase-one-valid" if is_valid else "phase-one-invalid"
    return kind, cbor_encode(payload)


def _rand_block_header(rng: random.Random):
    # Conservative five-field header shell with common CBOR shapes: unsigneds, bytes, and tag24-wrapped bytes.
    return [
        rng.randint(0, 7),
        _rand_hash32(rng),
        _rand_hash32(rng),
        rng.randint(0, 100_000),
        Tag24(_rand_small_payload(rng, min_len=0, max_len=192)),
    ]


def _rand_block_body_map(rng: random.Random):
    entry_count = rng.randint(0, 3)
    body = {}
    for index in range(entry_count):
        body[index] = rng.randint(0, 10_000) if rng.random() < 0.5 else _rand_small_payload(rng, min_len=0, max_len=64)
    return body


def generate_block_case(*, rng: random.Random, strategy: str) -> tuple[str, bytes]:
    if strategy != "structure":
        raise CorpusSynthesizerConfigError(f"unsupported strategy for block: {strategy}")
    payload = [
        _rand_block_header(rng),
        [_rand_block_body_map(rng) for _ in range(rng.randint(0, 2))],
        [_rand_block_body_map(rng) for _ in range(rng.randint(0, 2))],
        {rng.randint(0, 4): Tag24(_rand_small_payload(rng, min_len=0, max_len=96)) for _ in range(rng.randint(0, 2))},
        None if rng.random() < 0.5 else sorted(set(rng.randint(0, 4) for _ in range(rng.randint(0, 3)))),
    ]
    return "conway-shell", cbor_encode(payload)


def generate_seed_bytes(
    *,
    target_id: str,
    strategy: str,
    tokens: list[bytes],
    structure_spec: dict,
    rng: random.Random,
) -> tuple[str, bytes]:
    if structure_spec.get("target") != target_id:
        raise CorpusSynthesizerConfigError(
            f"structure target mismatch: expected {target_id!r}, found {structure_spec.get('target')!r}"
        )
    if target_id == "amaru-cargo-fuzz-handshake":
        return generate_handshake_message(rng=rng, tokens=tokens, strategy=strategy)
    if target_id == "amaru-cargo-fuzz-blockfetch":
        return generate_blockfetch_message(rng=rng, strategy=strategy)
    if target_id == "amaru-cargo-fuzz-chainsync":
        return generate_chainsync_message(rng=rng, strategy=strategy)
    if target_id == "amaru-cargo-fuzz-txsubmission":
        return generate_txsubmission_message(rng=rng, strategy=strategy)
    if target_id == "amaru-cargo-fuzz-ledger-fees":
        return generate_ledger_fees_case(rng=rng, strategy=strategy)
    if target_id == "amaru-cargo-fuzz-block":
        return generate_block_case(rng=rng, strategy=strategy)
    raise CorpusSynthesizerConfigError(f"unsupported target for this slice: {target_id}")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grammar-dict", required=True)
    parser.add_argument("--structure-spec", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--count", required=True, type=int)
    parser.add_argument("--strategy", required=True, choices=("dictionary", "structure"))
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seed", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        grammar_dict = Path(args.grammar_dict)
        structure_spec_path = Path(args.structure_spec)
        output_dir = Path(args.output_dir)
        if args.count < 1:
            raise CorpusSynthesizerConfigError("--count must be >= 1")

        tokens = parse_dictionary_tokens(grammar_dict)
        structure_spec = load_structure_spec(structure_spec_path)
        seed = args.seed if args.seed is not None else random.SystemRandom().randrange(0, 2**32)
        rng = random.Random(seed)

        output_dir.mkdir(parents=True, exist_ok=True)
        for stale in output_dir.glob("seed-*.cbor"):
            stale.unlink()

        files = []
        for index in range(1, args.count + 1):
            kind, payload = generate_seed_bytes(
                target_id=args.target_id,
                strategy=args.strategy,
                tokens=tokens,
                structure_spec=structure_spec,
                rng=rng,
            )
            name = f"seed-{index:03d}-{kind}.cbor"
            path = output_dir / name
            path.write_bytes(payload)
            files.append(
                {
                    "name": name,
                    "sha256": _sha256_hex(payload),
                    "size": len(payload),
                    "message_kind": kind,
                }
            )

        manifest = {
            "target_id": args.target_id,
            "strategy": args.strategy,
            "count_requested": args.count,
            "count_generated": len(files),
            "rng_seed": seed,
            "grammar_dict": str(grammar_dict),
            "structure_spec": str(structure_spec_path),
            "strategy_counts": {args.strategy: len(files)},
            "files": files,
        }
        (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        print(f"generated={len(files)} strategy={args.strategy} target={args.target_id}")
        return 0
    except CorpusSynthesizerError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
