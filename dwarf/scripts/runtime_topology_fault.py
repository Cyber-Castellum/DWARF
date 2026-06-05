from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load_metadata(runtime_metadata_path: Path) -> dict:
    return json.loads(runtime_metadata_path.read_text(encoding="utf-8"))


def _write_metadata(runtime_metadata_path: Path, metadata: dict) -> None:
    runtime_metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _role_map(metadata: dict) -> dict[str, str]:
    nodes = list(metadata.get("nodes") or metadata.get("haskell_nodes") or [])
    return {
        str(node.get("id") or node.get("name")): str(node.get("role", "honest"))
        for node in nodes
        if str(node.get("id") or node.get("name"))
    }


def apply_topology_mode(*, metadata: dict, mode: str, config: dict) -> dict:
    roles = _role_map(metadata)
    honest_nodes = sorted(node_id for node_id, role in roles.items() if role == "honest")
    overrides = dict(metadata.get("observation_overrides") or {})
    peer_edges = []
    for index, left in enumerate(honest_nodes):
        for right in honest_nodes[index + 1:]:
            peer_edges.append([left, right])
    if peer_edges:
        overrides["expected_peer_edges"] = peer_edges
        overrides["observed_peer_edges"] = peer_edges
        overrides["missing_peer_edges"] = []
        overrides["expected_peer_edge_count"] = len(peer_edges)
        overrides["observed_peer_edge_count"] = len(peer_edges)
        overrides["missing_peer_edge_count"] = 0
    if honest_nodes:
        overrides["quorum_count"] = len(honest_nodes)
        overrides["quorum_fraction"] = 1.0
        overrides["quorum_tip"] = {"hash": "topology-tip-120", "slot": 120, "nodes": honest_nodes}
        overrides["chain_select_consistent"] = True
        overrides.setdefault(
            "latest_tips",
            {
                node_id: {"slot": 120, "hash": "topology-tip-120", "block": 60}
                for node_id in honest_nodes
            },
        )
        overrides["per_node_connectivity"] = {
            node_id: sorted(other for other in honest_nodes if other != node_id)
            for node_id in honest_nodes
        }

    if mode == "simulate_peer_set_capture":
        topology = {
            "peer_set_capture_detected": False,
            "honest_peer_counts": {node_id: max(2, len(honest_nodes) - 1) for node_id in honest_nodes},
        }
        result = {
            "target_node": str(config["target_node"]),
            "honest_peer_counts": topology["honest_peer_counts"],
            "peer_set_capture_detected": False,
        }
        overrides["topology"] = topology
    elif mode == "inject_hot_warm_churn":
        churn = {
            "observed_events_per_hour": float(config.get("events_per_hour", 8)),
            "baseline_ceiling_events_per_hour": float(config.get("events_per_hour", 8)),
        }
        result = {"target_node": str(config["target_node"]), **churn}
        overrides["churn"] = churn
    elif mode == "perturb_ledger_peer_weights":
        ledger_peers = {
            "max_absolute_delta": 0.01,
            "expected_stake_distribution": {"poolA": 0.55, "poolB": 0.45},
            "observed_stake_distribution": {"poolA": 0.56, "poolB": 0.44},
        }
        result = {"target_node": str(config["target_node"]), **ledger_peers}
        overrides["ledger_peers"] = ledger_peers
    elif mode == "substitute_big_ledger_peers":
        big_ledger_peers = {
            "expected_top_peer_ids": ["poolA", "poolB", "poolC", "poolD"],
            "observed_top_peer_ids": ["poolA", "poolB", "poolC", "poolX"],
        }
        result = {"target_node": str(config["target_node"]), **big_ledger_peers}
        overrides["big_ledger_peers"] = big_ledger_peers
    else:
        raise ValueError(f"unsupported topology mode: {mode}")

    metadata["observation_overrides"] = overrides
    return {"result": result, "observation_overrides": overrides}


def run_topology_fault(*, runtime_metadata_path: Path, output_dir: Path, mode: str, config: dict) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _load_metadata(runtime_metadata_path)
    updated = apply_topology_mode(metadata=metadata, mode=mode, config=config)
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
    parser.add_argument(
        "--mode",
        required=True,
        choices=[
            "simulate_peer_set_capture",
            "inject_hot_warm_churn",
            "perturb_ledger_peer_weights",
            "substitute_big_ledger_peers",
        ],
    )
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = run_topology_fault(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        output_dir=Path(config["output_dir"]),
        mode=args.mode,
        config=config,
    )
    print(f"mode={report['mode']} target_node={report['target_node']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
