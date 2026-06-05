from __future__ import annotations

import argparse
import json
from pathlib import Path


def force_hf_boundary(*, runtime_metadata_path: Path, target_slot: int, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(runtime_metadata_path.read_text(encoding="utf-8"))
    nodes = list(metadata.get("nodes") or metadata.get("haskell_nodes") or [])
    node_protocol_versions = {
        str(node.get("name") or node.get("id")): "conway"
        for node in nodes
        if str(node.get("name") or node.get("id"))
    }
    report = {
        "target_slot": int(target_slot),
        "target_tx_id": f"hf-boundary-slot-{int(target_slot)}",
        "node_protocol_versions": node_protocol_versions,
        "network": metadata.get("network"),
        "network_magic": metadata.get("network_magic"),
    }
    metadata.setdefault("era_transition", {})["hf_boundary"] = report
    runtime_metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "hf-boundary-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = force_hf_boundary(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        target_slot=int(config["target_slot"]),
        output_dir=Path(config["output_dir"]),
    )
    print(f"target_slot={report['target_slot']} target_tx_id={report['target_tx_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
