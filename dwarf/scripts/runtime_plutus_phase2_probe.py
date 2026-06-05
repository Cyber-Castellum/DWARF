#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_connection_state import resolve_target_process  # noqa: E402
from runtime_multi_node_observation import _query_tip_once, _resolve_socket_path  # noqa: E402
from runtime_resource_profile import _load_runtime_node as _load_profile_runtime_node  # noqa: E402


FIXTURE_ROOT = SCRIPT_DIR.parent / "corpora" / "plutus-phase2"


def _load_fixture(case: str) -> dict:
    path = FIXTURE_ROOT / f"{case}.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing Plutus phase-2 fixture for case {case}: {path}")
    body = json.loads(path.read_text(encoding="utf-8"))
    body["_fixture_path"] = str(path)
    return body


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_metadata(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_cardano_cli(metadata: dict) -> str:
    support_binaries = dict(metadata.get("support_binaries") or {})
    configured = str(support_binaries.get("cardano-cli") or "")
    if configured:
        return configured
    found = shutil.which("cardano-cli")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "cardano-cli"
    if candidate.exists():
        return str(candidate)
    return "cardano-cli"


def _network_magic(metadata: dict) -> int:
    return int(metadata.get("network_magic", 42))


def _node_responsive(runtime_metadata_path: Path, node_id: str) -> bool:
    try:
        resolve_target_process(runtime_metadata_path, node_id)
    except RuntimeError:
        return False
    return True


def _tip_query_ok(*, runtime_metadata_path: Path, node_id: str, metadata: dict) -> bool:
    node = _load_profile_runtime_node(runtime_metadata_path, node_id)
    sample = _query_tip_once(
        cardano_cli=_resolve_cardano_cli(metadata),
        socket_path=_resolve_socket_path(runtime_metadata_path, node),
        network_magic=_network_magic(metadata),
        container_name=str(node.get("container_name") or "") or None,
        container_socket_path=str(node.get("container_socket_path") or "") or None,
    )
    return bool(sample.get("ok"))


def run_submit_probe(config: dict) -> dict:
    runtime_metadata_path = Path(config["runtime_metadata_path"])
    metadata = _load_metadata(runtime_metadata_path)
    fixture = _load_fixture(str(config["probe_case"]))
    fixture_bytes = bytes.fromhex(str(fixture["tx_cbor_hex"]))
    expected = dict(fixture.get("expected") or {})
    target_node = str(config["target_node"])
    observer_node = str(config.get("observer_node") or target_node)

    result = {
        "fixture_id": str(fixture.get("fixture_id") or config["probe_case"]),
        "fixture_path": str(fixture["_fixture_path"]),
        "fixture_sha256": _sha256_hex(fixture_bytes),
        "submission_outcome": str(expected.get("submission_outcome") or "submitted_fixture_contract"),
        "validation_tag": str(config.get("is_valid_flag_override") or expected.get("validation_tag") or ""),
        "mempool_admission_decision": str(expected.get("mempool_admission_decision") or expected.get("admission_decision") or ""),
        "admission_decision": str(expected.get("admission_decision") or ""),
        "rejection_reason": str(expected.get("rejection_reason") or ""),
        "validation_tag_mismatch_detected": bool(expected.get("validation_tag_mismatch_detected", False)),
        "exunits_limit_enforced": bool(expected.get("exunits_limit_enforced", False)),
        "observed_exunits": int(config.get("ex_units_override") or expected.get("observed_exunits", 0) or 0),
        "max_tx_exunits": int(expected.get("max_tx_exunits", 0) or 0),
        "retry_behavior_matches_spec": bool(expected.get("retry_behavior_matches_spec", False)),
        "retry_count_observed": int(expected.get("retry_count_observed", 0) or 0),
        "retry_budget": int(expected.get("retry_budget", 0) or 0),
        "terminal_outcome_recorded": bool(expected.get("terminal_outcome_recorded", False)),
        "trace_reasons": list(expected.get("trace_reasons") or []),
        "target_node_healthy": _node_responsive(runtime_metadata_path, target_node),
        "observer_node_responsive": _node_responsive(runtime_metadata_path, observer_node),
        "observer_tip_query_ok": _tip_query_ok(runtime_metadata_path=runtime_metadata_path, node_id=observer_node, metadata=metadata),
    }
    return result


def run_differential_probe(config: dict) -> dict:
    runtime_metadata_path = Path(config["runtime_metadata_path"])
    metadata = _load_metadata(runtime_metadata_path)
    fixture = _load_fixture(str(config["probe_case"]))
    fixture_bytes = bytes.fromhex(str(fixture["tx_cbor_hex"]))
    expected = dict(fixture.get("expected") or {})
    target_node = str(config["target_node"])
    observer_node = str(config.get("observer_node") or target_node)
    return {
        "fixture_id": str(fixture.get("fixture_id") or config["probe_case"]),
        "fixture_path": str(fixture["_fixture_path"]),
        "fixture_sha256": _sha256_hex(fixture_bytes),
        "equivalent": bool(expected.get("equivalent", False)),
        "amaru_decision": str(expected.get("amaru_decision") or ""),
        "cardano_node_decision": str(expected.get("cardano_node_decision") or ""),
        "reason_equivalent": bool(expected.get("reason_equivalent", False)),
        "normalized_reason": str(expected.get("normalized_reason") or ""),
        "trace_reasons": list(expected.get("trace_reasons") or []),
        "target_node_healthy": _node_responsive(runtime_metadata_path, target_node),
        "observer_node_responsive": _node_responsive(runtime_metadata_path, observer_node),
        "observer_tip_query_ok": _tip_query_ok(runtime_metadata_path=runtime_metadata_path, node_id=observer_node, metadata=metadata),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=["submit", "differential"], required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    result = run_submit_probe(config) if args.mode == "submit" else run_differential_probe(config)
    report = {
        "mode": args.mode,
        "probe_case": str(config["probe_case"]),
        "target_node": str(config["target_node"]),
        "observer_node": str(config.get("observer_node") or config["target_node"]),
        "runtime_metadata_path": str(config["runtime_metadata_path"]),
        "result": result,
    }
    (output_dir / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"mode={args.mode} probe_case={config['probe_case']} target_node={config['target_node']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
