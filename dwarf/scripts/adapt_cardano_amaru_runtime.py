#!/usr/bin/env python3
"""Emit a DWARF ``runtime.json`` for the *upstream* cardano_amaru topology.

The upstream ``cardano-node-antithesis`` / ``testnets/cardano_amaru`` topology is
brought up by its own docker compose (not DWARF's substrate primitives). This
adapter inspects the running containers and writes the ``runtime.json`` that
DWARF's observation / assertion primitives consume, so the node-agnostic tip
differential (chain_select_differential) can attach to a topology where Amaru
genuinely shares the producers' chain.

- cardano-node nodes (p1/p2/p3/relay1/relay2): observed via
  ``docker exec <name> cardano-cli query tip --socket-path /state/node.socket``.
- amaru nodes (amaru-relay-1/-2, amaru-consumer): observed via ``docker logs``
  (upstream Amaru logs to stderr, no host log file) — see _observe_amaru_tip_state.

Usage:
  python3 scripts/adapt_cardano_amaru_runtime.py --output <runtime.json> \
      [--network-magic 42] [--cardano-node-version 10.7.1] [--amaru-version 0.1.2]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# cardano-node services, queryable via `cardano-cli query tip`. amaru-consumer is
# itself a cardano-node that catches up THROUGH the Amaru relays, so its tip is the
# observable proxy for "what Amaru selected/served" — the target of the differential.
CARDANO_NODES = ["p1", "p2", "p3", "relay1", "relay2", "amaru-consumer"]
# Pure-Amaru relays: the system under test. Their tip is not directly cardano-cli
# queryable (AMARU_LOG=warn suppresses tip lines); Amaru's selection is observed via
# amaru-consumer above. Kept here for completeness / connection-state observation.
AMARU_NODES = ["amaru-relay-1", "amaru-relay-2"]
CONTAINER_SOCKET_PATH = "/state/node.socket"
NODE_PORT = 3001


def _running_containers() -> set[str]:
    proc = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=False, timeout=20,
    )
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def build_runtime(*, network_magic: int, cardano_node_version: str, amaru_version: str,
                  runtime_root: str, require_all: bool) -> dict:
    running = _running_containers()
    haskell_nodes = []
    for name in CARDANO_NODES:
        if name not in running:
            if require_all:
                raise SystemExit(f"expected cardano-node container not running: {name}")
            continue
        haskell_nodes.append({
            "id": name, "name": name, "impl": "cardano-node",
            "version": cardano_node_version, "role": "honest",
            "container_name": name, "container_socket_path": CONTAINER_SOCKET_PATH,
            "port": NODE_PORT,
        })
    amaru_nodes = []
    for name in AMARU_NODES:
        if name not in running:
            continue  # amaru-consumer is optional; relays may still be baking
        amaru_nodes.append({
            "id": name, "name": name, "impl": "amaru",
            "version": amaru_version, "role": "honest",
            "container_name": name, "log_tail_lines": 6000,
        })
    if not haskell_nodes:
        raise SystemExit("no cardano_amaru cardano-node containers are running")
    return {
        "runtime_root": runtime_root,
        "compose_project": "cardano_amaru",
        "compose_mode": "docker",
        "network": f"testnet_{network_magic}",
        "network_magic": network_magic,
        "haskell_nodes": haskell_nodes,
        "amaru_nodes": amaru_nodes,
        "nodes": haskell_nodes + amaru_nodes,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", required=True)
    ap.add_argument("--network-magic", type=int, default=42)
    ap.add_argument("--cardano-node-version", default="10.7.1")
    ap.add_argument("--amaru-version", default="0.1.2")
    ap.add_argument("--runtime-root", default="/home/nigel/cardano-node-antithesis/testnets/cardano_amaru")
    ap.add_argument("--require-all", action="store_true",
                    help="fail if any of p1/p2/p3/relay1/relay2 is not running")
    args = ap.parse_args(argv)
    runtime = build_runtime(
        network_magic=args.network_magic,
        cardano_node_version=args.cardano_node_version,
        amaru_version=args.amaru_version,
        runtime_root=args.runtime_root,
        require_all=args.require_all,
    )
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}: {len(runtime['haskell_nodes'])} cardano-node + "
          f"{len(runtime['amaru_nodes'])} amaru nodes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
