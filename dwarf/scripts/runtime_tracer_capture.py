#!/usr/bin/env python3
"""Capture cardano-tracer forensic evidence into a DWARF run bundle.

The cardano-tracer sidecar aggregates every node's machine-readable traces (forge
events, leadership checks, ChainDB add-block decisions, adoptions, fork switches)
into per-node JSON logs, and exposes a Prometheus endpoint. Cardano-node producers
forward their traces there rather than to stdout, so this is where the forensic
detail lives. This captures it as run artifacts, so any consensus scenario carries
the "every log / forge event" evidence DWARF is built for — no hand-run docker exec.

Outputs under --output-dir:
  tracer-logs/<node>.json   raw per-node ForMachine trace log
  prometheus.txt            metrics snapshot from the tracer Prometheus endpoint
  forge-summary.json        per-node event counts (forged / leader / adopted /
                            added-to-current-chain / switched-to-a-fork /
                            ignored-older-than-k / invalid) + capture metadata
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# ChainDB / consensus event markers worth counting for a forge/selection forensic
# summary. Substring counts over the ForMachine JSON — cheap and dependency-free.
EVENT_KINDS = [
    "Forge.Loop.ForgedBlock",
    "Forge.Loop.NodeIsLeader",
    "Forge.Loop.NodeNotLeader",
    "Forge.Loop.AdoptedBlock",
    "AddedToCurrentChain",
    "SwitchedToAFork",
    "IgnoreBlockOlderThanK",
    "InvalidBlock",
]


def _run(args: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout)


def _tracer_log_dirs(tracer: str) -> list[str]:
    p = _run(["docker", "exec", tracer, "sh", "-c", "ls /opt/cardano-tracer/logs 2>/dev/null"])
    return [d.strip() for d in p.stdout.split() if d.strip()]


def _capture_node_log(tracer: str, logdir: str, dest: Path) -> str:
    p = _run(
        ["docker", "exec", tracer, "sh", "-c", f"cat /opt/cardano-tracer/logs/{logdir}/*.json 2>/dev/null"],
        timeout=120,
    )
    dest.write_text(p.stdout, encoding="utf-8")
    return p.stdout


def _tracer_ip(tracer: str) -> str:
    p = _run(["docker", "inspect", tracer, "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"])
    return p.stdout.strip()


def _prometheus(tracer: str, port: int) -> tuple[str, str | None]:
    ip = _tracer_ip(tracer)
    if not ip:
        return "", "could not resolve tracer container ip"
    p = _run(["curl", "-s", "-m", "6", f"http://{ip}:{port}/metrics"], timeout=15)
    return p.stdout, (None if p.returncode == 0 else f"curl rc={p.returncode}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--tracer-container", default="tracer")
    ap.add_argument("--prometheus-port", type=int, default=4000)
    args = ap.parse_args(argv)

    out = Path(args.output_dir)
    (out / "tracer-logs").mkdir(parents=True, exist_ok=True)
    tracer = args.tracer_container

    dirs = _tracer_log_dirs(tracer)
    summary: dict = {"tracer_container": tracer, "log_dirs": dirs, "per_node": {}}
    for logdir in dirs:
        node = logdir.split(".")[0]
        text = _capture_node_log(tracer, logdir, out / "tracer-logs" / f"{node}.json")
        summary["per_node"][node] = {k: text.count(k) for k in EVENT_KINDS}

    prom, prom_err = _prometheus(tracer, args.prometheus_port)
    (out / "prometheus.txt").write_text(prom, encoding="utf-8")
    summary["prometheus_bytes"] = len(prom)
    summary["prometheus_error"] = prom_err
    summary["captured_nodes"] = len(dirs)

    (out / "forge-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"captured {len(dirs)} node logs, {len(prom)} bytes prometheus -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
