#!/usr/bin/env python3
"""Offline credential generation wrapper around cardano-testnet create-env."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


def _cardano_testnet_binary(explicit: str | None, *, env: dict[str, str]) -> str:
    if explicit:
        return explicit
    path_value = env.get("PATH", "")
    for root in path_value.split(os.pathsep):
        candidate = Path(root) / "cardano-testnet"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return "cardano-testnet"


def _default_local_binary(name: str) -> str | None:
    candidate = Path.home() / ".local" / "bin" / name
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _first_existing(root: Path, names: list[str]) -> Path | None:
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _write_summary(output_dir: Path, report: dict) -> None:
    lines = [
        "# Credential Ceremony",
        "",
        f"- Keys generated: {report['keys_generated']}",
        f"- KES period window: {report['kes_period_window']}",
        f"- VRF pubkey hash: `{report['vrf_pubkey_hash']}`",
        f"- Stake address hash: `{report['stake_addr_hash']}`",
        f"- Deterministic seed used: `{report['deterministic_seed_used']}`",
    ]
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_credential_ceremony(config: dict, *, env: dict[str, str] | None = None) -> Path:
    output_dir = Path(config["output_dir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    pool_count = int(config.get("pool_count", 1))
    testnet_magic = int(config.get("testnet_magic", 42))
    kes_period_window = int(config.get("kes_period_window", 1))
    deterministic_seed = str(config.get("deterministic_seed", "record-only"))
    ceremony_env = dict(os.environ if env is None else env)
    if "CARDANO_CLI" not in ceremony_env:
        cardano_cli = _default_local_binary("cardano-cli")
        if cardano_cli:
            ceremony_env["CARDANO_CLI"] = cardano_cli
    cardano_testnet = _cardano_testnet_binary(config.get("cardano_testnet_bin"), env=ceremony_env)

    command = [
        cardano_testnet,
        "create-env",
        "--output",
        str(output_dir / "env"),
        "--num-pool-nodes",
        str(pool_count),
        "--testnet-magic",
        str(testnet_magic),
        "--node-logging-format",
        "json",
    ]
    proc = subprocess.run(command, capture_output=True, text=True, check=False, env=ceremony_env)
    (output_dir / "stdout.log").write_text(proc.stdout or "", encoding="utf-8")
    (output_dir / "stderr.log").write_text(proc.stderr or "", encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or f"cardano-testnet exited {proc.returncode}")

    pools_root = output_dir / "env" / "pools-keys"
    generated_credentials = []
    vrf_hashes: list[str] = []
    stake_hashes: list[str] = []
    for pool_dir in sorted(path for path in pools_root.glob("pool*") if path.is_dir()):
        vrf_source = _first_existing(pool_dir, ["vrf.vkey", "vrf.skey"])
        stake_source = _first_existing(
            pool_dir,
            [
                "stake.addr",
                "stake.vkey",
                "staking-reward.vkey",
                "staking.vkey",
                "stake-verification.vkey",
            ],
        )
        vrf_hash = _hash_file(vrf_source) if vrf_source is not None else ""
        stake_hash = _hash_file(stake_source) if stake_source is not None else ""
        if vrf_hash:
            vrf_hashes.append(vrf_hash)
        if stake_hash:
            stake_hashes.append(stake_hash)
        generated_credentials.append(
            {
                "pool_id": pool_dir.name,
                "path": str(pool_dir),
                "vrf_source": str(vrf_source) if vrf_source is not None else None,
                "stake_source": str(stake_source) if stake_source is not None else None,
                "vrf_pubkey_hash": vrf_hash,
                "stake_addr_hash": stake_hash,
            }
        )

    report = {
        "keys_generated": len(generated_credentials),
        "kes_period_window": kes_period_window,
        "vrf_pubkey_hash": vrf_hashes[0] if vrf_hashes else "",
        "stake_addr_hash": stake_hashes[0] if stake_hashes else "",
        "deterministic_seed_used": deterministic_seed,
        "generated_credentials": generated_credentials,
        "command": command,
    }
    report_path = output_dir / "result.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_summary(output_dir, report)
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--pool-count", type=int, default=1)
    parser.add_argument("--testnet-magic", type=int, default=42)
    parser.add_argument("--kes-period-window", type=int, default=1)
    parser.add_argument("--deterministic-seed")
    parser.add_argument("--cardano-testnet-bin")
    args = parser.parse_args()
    report_path = run_credential_ceremony(
        {
            "output_dir": args.output_dir,
            "pool_count": args.pool_count,
            "testnet_magic": args.testnet_magic,
            "kes_period_window": args.kes_period_window,
            "deterministic_seed": args.deterministic_seed or "record-only",
            "cardano_testnet_bin": args.cardano_testnet_bin,
        }
    )
    print(f"credential_ceremony_completed=true report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
