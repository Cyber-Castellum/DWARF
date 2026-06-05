from __future__ import annotations

import argparse
import json
from pathlib import Path


def simulate_genesis_mode(*, runtime_metadata_path: Path, target_node: str, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(runtime_metadata_path.read_text(encoding="utf-8"))
    nodes = list(metadata.get("nodes") or metadata.get("haskell_nodes") or [])
    known_ids = {str(node.get("name") or node.get("id")) for node in nodes}
    if str(target_node) not in known_ids:
        raise ValueError(f"unknown target_node {target_node!r}")
    report = {
        "target_node": str(target_node),
        "mode_path": ["sync", "caught-up"],
        "final_mode": "caught-up",
        "peer_set_capture_detected": False,
        "network": metadata.get("network"),
        "network_magic": metadata.get("network_magic"),
    }
    metadata.setdefault("era_transition", {})["genesis_mode"] = report
    runtime_metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "genesis-mode-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = simulate_genesis_mode(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        target_node=str(config["target_node"]),
        output_dir=Path(config["output_dir"]),
    )
    print(f"target_node={report['target_node']} final_mode={report['final_mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
