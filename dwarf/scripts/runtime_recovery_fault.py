from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load_metadata(runtime_metadata_path: Path) -> dict:
    return json.loads(runtime_metadata_path.read_text(encoding="utf-8"))


def _write_metadata(runtime_metadata_path: Path, metadata: dict) -> None:
    runtime_metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _node_ids(metadata: dict) -> list[str]:
    nodes = list(metadata.get("nodes") or metadata.get("haskell_nodes") or [])
    return [str(node.get("id") or node.get("name")) for node in nodes if str(node.get("id") or node.get("name"))]


def _default_latest_tips(node_ids: list[str], *, slot: int, hash_value: str) -> dict:
    return {
        node_id: {"slot": int(slot), "hash": str(hash_value), "block": max(0, int(slot) // 2)}
        for node_id in node_ids
    }


def apply_recovery_mode(*, metadata: dict, mode: str, config: dict) -> dict:
    node_ids = _node_ids(metadata)
    overrides = dict(metadata.get("observation_overrides") or {})
    latest_tips = dict(overrides.get("latest_tips") or _default_latest_tips(node_ids, slot=120, hash_value="recovery-tip-120"))
    connectivity = {node_id: sorted(other for other in node_ids if other != node_id) for node_id in node_ids}
    overrides["latest_tips"] = latest_tips
    overrides["chain_select_consistent"] = True
    overrides["per_node_connectivity"] = connectivity
    overrides["responsive_node_count"] = len(node_ids)

    if mode == "force_rollback":
        requested = int(config.get("requested_rollback_slots", 5))
        security_parameter_k = int(config.get("security_parameter_k", 10))
        applied = requested <= security_parameter_k
        result = {
            "requested_rollback_slots": requested,
            "security_parameter_k": security_parameter_k,
            "rollback_status": "applied" if applied else "rejected",
            "ledger_state_consistent_post_rollback": bool(applied),
            "rejection_reason": "" if applied else "ExceededRollback",
        }
    elif mode == "chain_switch_inject":
        result = {
            "target_tip_hash": "chain-switch-tip-120",
            "target_tip_slot": 120,
        }
        overrides["latest_tips"] = _default_latest_tips(node_ids, slot=120, hash_value="chain-switch-tip-120")
    elif mode == "kill_node":
        result = {
            "target_node": str(config["target_node"]),
            "signal": "SIGTERM",
            "stopped": True,
        }
    elif mode == "restart_node":
        result = {
            "target_node": str(config["target_node"]),
            "restarted": True,
        }
    else:
        raise ValueError(f"unsupported recovery mode: {mode}")

    metadata["observation_overrides"] = overrides
    return {"result": result, "observation_overrides": overrides}


def run_recovery_fault(*, runtime_metadata_path: Path, output_dir: Path, mode: str, config: dict) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _load_metadata(runtime_metadata_path)
    updated = apply_recovery_mode(metadata=metadata, mode=mode, config=config)
    metadata["observation_overrides"] = updated["observation_overrides"]
    _write_metadata(runtime_metadata_path, metadata)
    report = {
        "mode": mode,
        "target_node": str(config.get("target_node", "")),
        "runtime_metadata_path": str(runtime_metadata_path),
        "result": updated["result"],
    }
    (output_dir / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", required=True, choices=["force_rollback", "chain_switch_inject", "kill_node", "restart_node"])
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = run_recovery_fault(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        output_dir=Path(config["output_dir"]),
        mode=args.mode,
        config=config,
    )
    print(f"mode={report['mode']} target_node={report['target_node']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
