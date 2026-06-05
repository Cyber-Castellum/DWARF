#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_multi_node_observation import (  # noqa: E402
    _load_runtime_nodes,
    _observe_tip_state,
    _resolve_network_magic,
    _resolve_node_ids,
)
from runtime_telemetry import emit_target_event  # noqa: E402


DEFAULT_OUTPUT_NAME = "substrate-tip-warmup"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _tip_has_real_chain_progress(tip: dict) -> bool:
    hash_value = str((tip or {}).get("hash") or "").strip()
    slot_value = (tip or {}).get("slot")
    try:
        slot_int = int(slot_value)
    except (TypeError, ValueError):
        return False
    return slot_int > 0 and bool(hash_value)


def _write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_substrate_tip_warmup(
    *,
    runtime_metadata_path: Path,
    node_ids: list[str],
    output_dir: Path,
    timeout_seconds: float,
    sample_interval_seconds: float,
    minimum_ready_nodes: int | None = None,
    minimum_slot: int = 1,
    network_magic: int | None = None,
    cardano_cli: str = "cardano-cli",
) -> dict:
    metadata, nodes = _load_runtime_nodes(runtime_metadata_path)
    resolved_node_ids = _resolve_node_ids(node_ids, nodes)
    resolved_network_magic = _resolve_network_magic(metadata, network_magic)
    required_ready_nodes = int(minimum_ready_nodes or len(resolved_node_ids))
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    samples_by_node: dict[str, list[dict]] = {node_id: [] for node_id in resolved_node_ids}
    latest_tips: dict[str, dict] = {}

    while True:
        attempts += 1
        for node_id in resolved_node_ids:
            node = nodes[node_id]
            observation = _observe_tip_state(
                runtime_metadata_path=runtime_metadata_path,
                node=node,
                cardano_cli=cardano_cli,
                network_magic=resolved_network_magic,
                observation_window_seconds=0,
                sample_interval_seconds=sample_interval_seconds,
            )
            sample = dict((observation.get("latest_tip") or {}))
            samples_by_node[node_id].append(sample)
            latest_tips[node_id] = sample

        ready_nodes = [
            node_id
            for node_id, tip in latest_tips.items()
            if _tip_has_real_chain_progress(tip) and int(tip.get("slot", 0) or 0) >= minimum_slot
        ]
        if len(ready_nodes) >= required_ready_nodes:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(sample_interval_seconds)

    timed_out = len(
        [
            node_id
            for node_id, tip in latest_tips.items()
            if _tip_has_real_chain_progress(tip) and int(tip.get("slot", 0) or 0) >= minimum_slot
        ]
    ) < required_ready_nodes
    ready_nodes = [
        node_id
        for node_id, tip in latest_tips.items()
        if _tip_has_real_chain_progress(tip) and int(tip.get("slot", 0) or 0) >= minimum_slot
    ]
    result = {
        "runtime_metadata_path": str(runtime_metadata_path),
        "node_ids": resolved_node_ids,
        "network_magic": resolved_network_magic,
        "timeout_seconds": timeout_seconds,
        "sample_interval_seconds": sample_interval_seconds,
        "minimum_ready_nodes": required_ready_nodes,
        "minimum_slot": minimum_slot,
        "attempt_count": attempts,
        "timed_out": timed_out,
        "ready": not timed_out,
        "ready_node_count": len(ready_nodes),
        "ready_nodes": ready_nodes,
        "latest_tips": latest_tips,
        "sample_history": samples_by_node,
        "finished_at": _utc_now(),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "warmup-summary.json", result)
    return result


def _default_output_dir() -> Path:
    run_dir = Path(str(Path.cwd()))
    return run_dir / "outputs" / DEFAULT_OUTPUT_NAME


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Wait for real non-zero chain tips on a composed substrate")
    parser.add_argument("--runtime-metadata-path", required=True)
    parser.add_argument("--node-id", action="append", dest="node_ids", default=[])
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--sample-interval-seconds", type=float, default=2.0)
    parser.add_argument("--minimum-ready-nodes", type=int, default=None)
    parser.add_argument("--minimum-slot", type=int, default=1)
    parser.add_argument("--network-magic", type=int, default=None)
    parser.add_argument("--cardano-cli", default="cardano-cli")
    args = parser.parse_args(argv[1:])

    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()
    emit_target_event(
        primitive="runtime_substrate_tip_warmup",
        event="substrate_tip_warmup_started",
        payload={
            "runtime_metadata_path": args.runtime_metadata_path,
            "node_ids": args.node_ids,
            "output_dir": str(output_dir),
            "timeout_seconds": args.timeout_seconds,
            "sample_interval_seconds": args.sample_interval_seconds,
            "minimum_ready_nodes": args.minimum_ready_nodes,
            "minimum_slot": args.minimum_slot,
        },
    )
    result = run_substrate_tip_warmup(
        runtime_metadata_path=Path(args.runtime_metadata_path),
        node_ids=args.node_ids,
        output_dir=output_dir,
        timeout_seconds=args.timeout_seconds,
        sample_interval_seconds=args.sample_interval_seconds,
        minimum_ready_nodes=args.minimum_ready_nodes,
        minimum_slot=args.minimum_slot,
        network_magic=args.network_magic,
        cardano_cli=args.cardano_cli,
    )
    emit_target_event(
        primitive="runtime_substrate_tip_warmup",
        event="substrate_tip_warmup_completed",
        payload={
            "output_dir": str(output_dir),
            "ready": result.get("ready"),
            "timed_out": result.get("timed_out"),
            "ready_node_count": result.get("ready_node_count"),
            "minimum_ready_nodes": result.get("minimum_ready_nodes"),
        },
    )
    return 0 if result.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
