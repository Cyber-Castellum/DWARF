from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_substrate_common import CommandResult, normalize_substrate, resolve_binary_for_node, run_command, write_json


def resolve_requested_versions(
    *,
    substrate: dict,
    output_dir: Path,
    runner=run_command,
    which=shutil.which,
) -> dict:
    normalized = normalize_substrate(substrate)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_lines = []
    nodes = {}
    satisfied = True
    compose_mode = str(substrate.get("compose_mode", "host"))
    for node in normalized["nodes"]:
        if compose_mode == "docker":
            resolved = resolve_binary_for_node(node, runner=runner, which=which)
            if not resolved["satisfied"]:
                image_ref = f"docker-image:dwarf/{'cardano-node' if node['impl'] == 'cardano-node' else 'amaru'}:{node['version']}"
                resolved = {
                    "status": "image-present",
                    "satisfied": True,
                    "resolved_binary": image_ref,
                    "resolved_version": node["version"],
                    "version_output": image_ref,
                }
        else:
            resolved = resolve_binary_for_node(node, runner=runner, which=which)
        nodes[node["id"]] = {
            **resolved,
            "impl": node["impl"],
            "requested_version": node["version"],
            "role": node["role"],
        }
        satisfied = satisfied and bool(resolved["satisfied"])
        log_lines.append(
            f"{node['id']}\t{node['impl']}\trequested={node['version']}\tstatus={resolved['status']}\tbinary={resolved['resolved_binary'] or ''}"
        )
    report = {
        "satisfied": satisfied,
        "node_count": len(normalized["nodes"]),
        "network": normalized["network"],
        "network_magic": normalized["network_magic"],
        "nodes": nodes,
    }
    write_json(output_dir / "install-report.json", report)
    (output_dir / "install-log.txt").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    output_dir = Path(config["output_dir"])
    report = resolve_requested_versions(substrate=config["substrate"], output_dir=output_dir)
    print(f"node_count={report['node_count']} satisfied={'true' if report['satisfied'] else 'false'}")
    return 0 if report["satisfied"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
