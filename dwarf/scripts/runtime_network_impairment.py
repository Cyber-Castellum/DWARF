from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime_resource_abuse_fault import apply_network_impairment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = apply_network_impairment(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        output_dir=Path(config["output_dir"]),
        from_node=str(config["from_node"]),
        to_node=str(config["to_node"]),
        latency_ms=int(config.get("latency_ms", 0)),
        jitter_ms=int(config.get("jitter_ms", 0)),
        loss_percent=int(config.get("loss_percent", 0)),
        partition=bool(config.get("partition", False)),
        healthy_timeout_seconds=float(config.get("healthy_timeout_seconds", 90)),
    )
    print(f"mode={report['mode']} target_node={config.get('from_node', '')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
