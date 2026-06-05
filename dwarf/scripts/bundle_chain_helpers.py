#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import runtime_bundle_attestation  # noqa: E402


def read_bundle_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def bundle_scenario_id(run_dir: Path) -> str:
    payload = read_bundle_json(run_dir / "scenario.yaml")
    if isinstance(payload, dict):
        return str(payload.get("id") or "")
    return ""


def bundle_attestation_summary(run_dir: Path) -> dict:
    artifact = run_dir / "outputs" / "attestation" / "attestation.json"
    payload = read_bundle_json(artifact)
    if not payload:
        scenario_path = run_dir / "scenario.yaml"
        scenario_sha256 = None
        if scenario_path.is_file():
            scenario_sha256 = hashlib.sha256(scenario_path.read_bytes()).hexdigest()
        return {
            "attestation_present": False,
            "attestation_verdict": "unsigned",
            "scenario_yaml_sha256": scenario_sha256,
            "dwarf_source_sha256": None,
            "tooling_versions": None,
            "signing_key_fingerprint": None,
        }
    verification = payload.get("verification") or runtime_bundle_attestation.verify_attestation(artifact)
    statement = payload.get("statement") or {}
    signature = payload.get("signature") or {}
    verdict = verification.get("verdict") or ("unsigned" if signature.get("signing_unavailable") else "tampered")
    return {
        "attestation_present": True,
        "attestation_verdict": verdict,
        "scenario_yaml_sha256": statement.get("scenario_yaml_sha256"),
        "dwarf_source_sha256": statement.get("dwarf_source_sha256"),
        "tooling_versions": statement.get("tooling_versions"),
        "signing_key_fingerprint": signature.get("signing_key_fingerprint"),
    }


def bundle_parent_run_id(run_dir: Path) -> str | None:
    replay = read_bundle_json(run_dir / "outputs" / "replay" / "result.json") or {}
    target_run_id = replay.get("target_run_id")
    if target_run_id:
        return str(target_run_id)
    return None


def bundle_audit_node(runs_dir: Path, run_id: str) -> dict:
    run_dir = runs_dir / run_id
    attestation = bundle_attestation_summary(run_dir)
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "scenario_id": bundle_scenario_id(run_dir),
        "parent_run_id": bundle_parent_run_id(run_dir),
        **attestation,
    }


def walk_bundle_audit_trail(runs_dir: Path, run_id: str) -> dict:
    chain = []
    visited = set()
    current = run_id
    cycle_at = None
    while current:
        if current in visited:
            cycle_at = current
            break
        visited.add(current)
        run_dir = runs_dir / current
        if not run_dir.is_dir():
            chain.append(
                {
                    "run_id": current,
                    "run_dir": str(run_dir),
                    "scenario_id": "",
                    "parent_run_id": None,
                    "attestation_present": False,
                    "attestation_verdict": "missing_bundle",
                    "scenario_yaml_sha256": None,
                    "dwarf_source_sha256": None,
                    "tooling_versions": None,
                    "signing_key_fingerprint": None,
                }
            )
            break
        node = bundle_audit_node(runs_dir, current)
        chain.append(node)
        current = node["parent_run_id"]

    chain_root_first = list(reversed(chain))
    broken_node = None
    for node in chain_root_first:
        if node["attestation_verdict"] != "verified":
            broken_node = node["run_id"]
            break
    if cycle_at is not None:
        chain_verdict = f"chain-broken-at-{cycle_at}"
    elif broken_node is not None:
        chain_verdict = f"chain-broken-at-{broken_node}"
    else:
        chain_verdict = "all-verified"
    return {
        "root_run_id": chain_root_first[0]["run_id"] if chain_root_first else run_id,
        "leaf_run_id": run_id,
        "chain_length": len(chain_root_first),
        "chain_verdict": chain_verdict,
        "bundles": chain_root_first,
    }


def format_bundle_audit_trail(payload: dict) -> str:
    lines = [
        f"bundle audit trail: {payload['root_run_id']} -> {payload['leaf_run_id']}",
        f"chain_length: {payload['chain_length']}",
        f"chain_verdict: {payload['chain_verdict']}",
        "tree:",
    ]
    bundles = payload.get("bundles") or []
    for index, node in enumerate(bundles):
        prefix = "└─" if index == len(bundles) - 1 else "├─"
        lines.append(
            f"{prefix} {node['run_id']} scenario={node['scenario_id'] or 'unknown'} "
            f"attestation={node['attestation_verdict']}"
        )
        lines.append(f"   scenario_yaml_sha256={node.get('scenario_yaml_sha256') or 'none'}")
        lines.append(f"   dwarf_source_sha256={node.get('dwarf_source_sha256') or 'none'}")
        lines.append(f"   signing_key_fingerprint={node.get('signing_key_fingerprint') or 'none'}")
        tooling_versions = node.get("tooling_versions") or {}
        if tooling_versions:
            lines.append(
                "   tooling_versions="
                + ", ".join(f"{key}={value}" for key, value in sorted(tooling_versions.items()) if value)
            )
        if node.get("parent_run_id"):
            lines.append(f"   parent_run_id={node['parent_run_id']}")
    return "\n".join(lines)
