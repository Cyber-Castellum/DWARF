from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_multi_node_observation import _query_tip_once, _resolve_socket_path  # noqa: E402
from runtime_substrate_common import write_json  # noqa: E402
from runtime_telemetry import emit_target_event  # noqa: E402


def _load_runtime_metadata(path: Path) -> tuple[Path, dict]:
    body = json.loads(path.read_text(encoding="utf-8"))
    runtime_metadata_path = body.get("runtime_metadata_path")
    if runtime_metadata_path:
        resolved = Path(str(runtime_metadata_path))
        resolved_body = json.loads(resolved.read_text(encoding="utf-8"))
        merged = dict(body)
        merged.update(resolved_body)
        return resolved, merged
    return path, body


def _find_node(metadata: dict, node_id: str) -> dict:
    for node in list(metadata.get("haskell_nodes") or []) + list(metadata.get("amaru_nodes") or []) + list(
        metadata.get("nodes") or []
    ):
        if node.get("id") == node_id or node.get("name") == node_id:
            return dict(node)
    raise ValueError(f"unknown substrate node: {node_id}")


def _resolve_cardano_cli(metadata: dict, node: dict) -> str:
    support_binaries = metadata.get("support_binaries") or {}
    if support_binaries.get("cardano-cli"):
        return str(support_binaries["cardano-cli"])
    resolved_binary = node.get("resolved_binary")
    if resolved_binary:
        return str(Path(str(resolved_binary)).with_name("cardano-cli"))
    return "cardano-cli"


def _run_subprocess(cmd: list[str], *, stdin_bytes: bytes | None = None, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=stdin_bytes, capture_output=True, timeout=timeout, check=False)


def _run_byzantine_cycle(*, config: dict, output_dir: Path, timeout_seconds: float) -> dict:
    proxy_dir = output_dir / "proxy"
    proxy_dir.mkdir(parents=True, exist_ok=True)
    config_body = {
        "runtime_metadata_path": config["runtime_metadata_path"],
        "output_dir": str(proxy_dir),
        "target_node_id": config["target_node"],
        "upstream_node_id": config.get("reference_node"),
        "mutation_mode": "flip_payload_byte",
        "mutation_direction": "inbound",
        "mutation_protocol": "any",
        "mutate_after_segments": 1,
        "healthy_timeout_seconds": float(config.get("healthy_timeout_seconds", 90)),
    }
    config_path = output_dir / "runtime-peersharing-fault-config.json"
    config_path.write_text(json.dumps(config_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    script = SCRIPT_DIR / "runtime_byzantine_peer.py"
    apply_proc = _run_subprocess(["python3", str(script), "--config", str(config_path), "--mode", "apply"], timeout=timeout_seconds)
    time.sleep(float(config.get("activity_window_seconds", 5.0)))
    remove_proc = _run_subprocess(["python3", str(script), "--config", str(config_path), "--mode", "remove"], timeout=timeout_seconds)
    apply_report = {}
    remove_report = {}
    if (proxy_dir / "apply-report.json").is_file():
        apply_report = json.loads((proxy_dir / "apply-report.json").read_text(encoding="utf-8"))
    if (proxy_dir / "remove-report.json").is_file():
        remove_report = json.loads((proxy_dir / "remove-report.json").read_text(encoding="utf-8"))
    return {
        "apply_exit_code": apply_proc.returncode,
        "remove_exit_code": remove_proc.returncode,
        "apply_report": apply_report,
        "remove_report": remove_report,
    }


def _sequence_allowed(state_corpus: Path, *, initial_state: str, message_hex: str) -> bool:
    body = json.loads(state_corpus.read_text(encoding="utf-8"))
    for sequence in body.get("sequences", []):
        if sequence.get("initial_state") != initial_state:
            continue
        for transition in sequence.get("transitions", []):
            if transition.get("from") != initial_state:
                continue
            message = transition.get("message") or {}
            if str(message.get("hex", "")).lower() == message_hex.lower():
                return True
    return False


def _load_sequence_messages(state_corpus: Path, *, sequence_id: str) -> list[dict]:
    body = json.loads(state_corpus.read_text(encoding="utf-8"))
    for sequence in body.get("sequences", []):
        if sequence.get("id") == sequence_id:
            return list(sequence.get("messages", []))
    raise ValueError(f"sequence {sequence_id!r} not found in {state_corpus}")


def run_protocol_fault(
    *,
    runtime_metadata_path: Path,
    output_dir: Path,
    mode: str,
    target_node: str,
    reference_node: str | None = None,
    network_magic: int | None = None,
    timeout_seconds: float = 120.0,
    peersharing_decoder_path: str = "/home/nigel/dwarf-fw/targets/cardano-node/bin/cardano-node-mini-protocol-decode-peersharing",
    peersharing_state_corpus: str = "/home/nigel/dwarf-fw/corpora/m3/peersharing-sequences.json",
    localtxmonitor_decoder_path: str = "/home/nigel/dwarf-fw/targets/cardano-node/bin/cardano-node-mini-protocol-decode-localtxmonitor",
    localtxmonitor_state_corpus: str = "/home/nigel/dwarf-fw/corpora/m3/localtxmonitor-state-machine.json",
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_metadata_path, metadata = _load_runtime_metadata(runtime_metadata_path)
    node = _find_node(metadata, target_node)
    cardano_cli = _resolve_cardano_cli(metadata, node)
    effective_network_magic = int(metadata.get("network_magic") or network_magic or 42)

    if mode == "peersharing_fault":
        malformed_messages = _load_sequence_messages(Path(peersharing_state_corpus), sequence_id="malformed-addresses")
        decoder_checks = []
        for message in malformed_messages:
            proc = _run_subprocess(
                ["/bin/sh", "-c", f"cat | {peersharing_decoder_path}"],
                stdin_bytes=bytes.fromhex(str(message["hex"])),
                timeout=timeout_seconds,
            )
            stdout = proc.stdout.decode("utf-8", errors="replace")
            stderr = proc.stderr.decode("utf-8", errors="replace")
            decoder_checks.append(
                {
                    "name": str(message.get("name", "")),
                    "expect": str(message.get("expect", "")),
                    "exit_code": proc.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "rejected": proc.returncode == 1 and stdout.startswith("ERR "),
                }
            )
        cycle = _run_byzantine_cycle(
            config={
                "runtime_metadata_path": str(runtime_metadata_path),
                "target_node": target_node,
                "reference_node": reference_node,
                "healthy_timeout_seconds": 90,
                "activity_window_seconds": 5.0,
            },
            output_dir=output_dir,
            timeout_seconds=timeout_seconds,
        )
        remove_report = cycle.get("remove_report") or {}
        intercepted = int(remove_report.get("intercepted_segments", 0) or 0)
        connections = int(remove_report.get("connections_seen", 0) or 0)
        healthy = bool(remove_report.get("healthy", False))
        decoder_rejections = sum(1 for check in decoder_checks if check["rejected"])
        result = {
            "invalid_peer_addresses_rejected": any(
                check["rejected"] and "address" in check["name"] for check in decoder_checks
            ),
            "malformed_share_replies_rejected": decoder_rejections == len(decoder_checks) and len(decoder_checks) >= 1,
            "connections_seen": connections,
            "intercepted_segments": intercepted,
            "decoder_rejections": decoder_rejections,
            "traffic_observed": connections >= 1 or intercepted >= 1,
        }
        report = {
            "mode": mode,
            "runtime_metadata_path": str(runtime_metadata_path),
            "target_node": target_node,
            "reference_node": reference_node,
            "result": result,
            "decoder_checks": decoder_checks,
            "proxy_cycle": cycle,
            "node_stayed_up": healthy,
        }
    elif mode == "localtxmonitor_fault":
        # This fault path is currently cardano-node-only. If a future scenario
        # targets an Amaru-bearing substrate here, thread the F-027 impl-aware
        # tip observer through this liveness check instead of calling the
        # socket-only query path directly.
        socket_path = _resolve_socket_path(runtime_metadata_path, node)
        tip = _query_tip_once(
            cardano_cli=cardano_cli,
            socket_path=socket_path,
            network_magic=effective_network_magic,
            container_name=str(node.get("container_name") or "") or None,
            container_socket_path=str(node.get("container_socket_path") or "") or None,
        )
        decoder_proc = _run_subprocess(["/bin/sh", "-c", f"cat | {localtxmonitor_decoder_path}"], stdin_bytes=bytes.fromhex("820bff"), timeout=timeout_seconds)
        stdout = decoder_proc.stdout.decode("utf-8", errors="replace")
        stderr = decoder_proc.stderr.decode("utf-8", errors="replace")
        invalid_sequence_allowed = _sequence_allowed(Path(localtxmonitor_state_corpus), initial_state="idle", message_hex="8103")
        result = {
            "malformed_mempool_snapshot_reply_rejected": decoder_proc.returncode == 1 and stdout.startswith("ERR "),
            "invalid_acquire_release_sequence_rejected": not invalid_sequence_allowed,
            "node_stayed_up": bool(tip.get("ok", False)),
        }
        report = {
            "mode": mode,
            "runtime_metadata_path": str(runtime_metadata_path),
            "target_node": target_node,
            "network_magic": effective_network_magic,
            "decoder_exit_code": decoder_proc.returncode,
            "decoder_stdout": stdout,
            "decoder_stderr": stderr,
            "tip": tip,
            "result": result,
        }
    else:
        raise ValueError(f"unsupported mode: {mode}")

    write_json(output_dir / "result.json", report)
    emit_target_event(primitive=f"runtime_{mode}", event="protocol_fault_result", payload=report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", required=True, choices=["peersharing_fault", "localtxmonitor_fault"])
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = run_protocol_fault(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        output_dir=Path(config["output_dir"]),
        mode=str(args.mode),
        target_node=str(config["target_node"]),
        reference_node=(str(config["reference_node"]) if config.get("reference_node") else None),
        network_magic=(int(config["network_magic"]) if "network_magic" in config else None),
        timeout_seconds=float(config.get("timeout_seconds", 120.0)),
        peersharing_decoder_path=str(config.get("peersharing_decoder_path", "/home/nigel/dwarf-fw/targets/cardano-node/bin/cardano-node-mini-protocol-decode-peersharing")),
        peersharing_state_corpus=str(config.get("peersharing_state_corpus", "/home/nigel/dwarf-fw/corpora/m3/peersharing-sequences.json")),
        localtxmonitor_decoder_path=str(config.get("localtxmonitor_decoder_path", "/home/nigel/dwarf-fw/targets/cardano-node/bin/cardano-node-mini-protocol-decode-localtxmonitor")),
        localtxmonitor_state_corpus=str(config.get("localtxmonitor_state_corpus", "/home/nigel/dwarf-fw/corpora/m3/localtxmonitor-state-machine.json")),
    )
    print(
        " ".join(
            [
                f"mode={report['mode']}",
                f"target_node={report['target_node']}",
                f"result_keys={','.join(sorted((report.get('result') or {}).keys()))}",
            ]
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
