#!/usr/bin/env python3

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_telemetry import emit_target_event  # noqa: E402


DEFAULT_REPO_ROOT = Path("${HOME}/amaru-verification")
DEFAULT_MANIFEST_PATH = DEFAULT_REPO_ROOT / "crates/amaru/config/bootstrap/preview/snapshots.json"
DEFAULT_NETWORK = "preview"
DEFAULT_PEER = "preview-node.play.dev.cardano.org:3001"
DEFAULT_LISTEN = "127.0.0.1:39000"
DEFAULT_OUTPUT_NAME = "runtime-amaru-preview-proof-of-life"
ADOPTED_TIP_PATTERN = re.compile(
    r"adopted tip tip\.slot=(?P<slot>\d+) tip\.hash=(?P<hash>[0-9a-f]+) tip\.block_height=(?P<block_height>\d+)"
)
HEADER_PATTERN = re.compile(r"(?P<hash>[0-9a-f]{64}): 828a1a(?P<epoch>[0-9a-f]{8})1a(?P<slot>[0-9a-f]{8})")


def load_snapshot_sources(manifest_path: Path) -> list[dict[str, object]]:
    rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = []
    for row in rows:
        sources.append(
            {
                "epoch": int(row["epoch"]),
                "url": str(row.get("url", row.get("location"))),
                "sha256": row.get("digest"),
                "point": row.get("point"),
            }
        )
    return sources


def extract_latest_adopted_tip(log_text: str) -> dict[str, object] | None:
    latest = None
    for match in ADOPTED_TIP_PATTERN.finditer(log_text):
        latest = {
            "slot": int(match.group("slot")),
            "hash": match.group("hash"),
            "block_height": int(match.group("block_height")),
        }
    return latest


def extract_highest_header_tip(dump_text: str) -> dict[str, object] | None:
    tips = []
    for line in dump_text.splitlines():
        match = HEADER_PATTERN.match(line)
        if match:
            tips.append(
                {
                    "slot": int(match.group("slot"), 16),
                    "hash": match.group("hash"),
                }
            )
    if not tips:
        return None
    return max(tips, key=lambda item: item["slot"])


def _listener_ok(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def _tail_lines(text: str, *, count: int = 200) -> str:
    lines = text.splitlines()
    if len(lines) <= count:
        return text
    return "\n".join(lines[-count:]) + "\n"


def _output_dir() -> Path | None:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if not run_dir:
        return None
    output_dir = Path(run_dir) / "outputs" / DEFAULT_OUTPUT_NAME
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def write_bundle_artifacts(
    *,
    result: dict[str, object],
    bootstrap_stdout: str,
    bootstrap_stderr: str,
    daemon_stderr: str,
    final_tip: dict[str, object] | None,
    provenance: dict[str, object],
) -> None:
    output_dir = _output_dir()
    if output_dir is None:
        return
    (output_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (output_dir / "bootstrap.stdout.log").write_text(bootstrap_stdout, encoding="utf-8")
    (output_dir / "bootstrap.stderr.log").write_text(bootstrap_stderr, encoding="utf-8")
    (output_dir / "daemon.stderr.log").write_text(daemon_stderr, encoding="utf-8")
    (output_dir / "daemon.stderr.window.log").write_text(_tail_lines(daemon_stderr), encoding="utf-8")
    (output_dir / "snapshot-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    if final_tip is not None:
        (output_dir / "final-tip.json").write_text(json.dumps(final_tip, indent=2) + "\n", encoding="utf-8")


def run_command(args: list[str], *, cwd: Path, env: dict[str, str] | None = None, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def dump_chain_tip(*, repo_root: Path, network: str) -> dict[str, object]:
    proc = run_command(
        [
            str(repo_root / "target/release/amaru"),
            "dump-chain-db",
            "--network",
            network,
            "--chain-dir",
            str(repo_root / f"chain.{network}.db"),
        ],
        cwd=repo_root,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"dump-chain-db failed: {proc.stderr.strip()}")
    tip = extract_highest_header_tip(proc.stdout)
    if tip is None:
        raise RuntimeError("dump-chain-db produced no header rows")
    return tip


def emit_phase_event(event: str, payload: dict[str, object], *, level: str = "info") -> None:
    emit_target_event(
        primitive="runtime_amaru_preview_proof",
        event=event,
        payload=payload,
        level=level,
    )


def run_proof(
    *,
    repo_root: Path,
    peer_address: str,
    listen_address: str,
    network: str,
    timeout_seconds: int,
) -> int:
    manifest_path = repo_root / "crates/amaru/config/bootstrap/preview/snapshots.json"
    if not manifest_path.exists():
        raise RuntimeError(f"missing bootstrap manifest: {manifest_path}")

    emit_phase_event(
        "amaru_preview_bootstrap_started",
        {
            "repo_root": str(repo_root),
            "network": network,
            "peer_address": peer_address,
            "listen_address": listen_address,
        },
    )

    provenance = {
        "manifest_path": str(manifest_path),
        "snapshots": load_snapshot_sources(manifest_path),
    }

    bootstrap = run_command(["make", f"AMARU_NETWORK={network}", "bootstrap"], cwd=repo_root, timeout=timeout_seconds)
    if bootstrap.returncode != 0:
        result = {
            "outcome": "bootstrap_failed",
            "bootstrap_exit_code": bootstrap.returncode,
            "daemon_exit_code": None,
            "peer_address": peer_address,
            "listen_address": listen_address,
        }
        write_bundle_artifacts(
            result=result,
            bootstrap_stdout=bootstrap.stdout,
            bootstrap_stderr=bootstrap.stderr,
            daemon_stderr="",
            final_tip=None,
            provenance=provenance,
        )
        emit_phase_event("amaru_preview_bootstrap_result", result, level="error")
        raise RuntimeError(f"preview bootstrap failed: exit {bootstrap.returncode}")

    imported_tip = dump_chain_tip(repo_root=repo_root, network=network)
    emit_phase_event(
        "amaru_preview_bootstrap_completed",
        {
            "bootstrap_exit_code": 0,
            "imported_snapshot_tip": imported_tip,
            "snapshot_count": len(provenance["snapshots"]),
        },
    )

    listen_host, listen_port_text = listen_address.rsplit(":", 1)
    listen_port = int(listen_port_text)
    pid_file = Path(tempfile.gettempdir()) / "amaru-preview-proof.pid"
    if pid_file.exists():
        pid_file.unlink()

    daemon_log = tempfile.NamedTemporaryFile(prefix="amaru-preview-proof-", suffix=".stderr.log", delete=False)
    daemon_log_path = Path(daemon_log.name)
    daemon_log.close()
    daemon_out = tempfile.NamedTemporaryFile(prefix="amaru-preview-proof-", suffix=".stdout.log", delete=False)
    daemon_out_path = Path(daemon_out.name)
    daemon_out.close()

    env = os.environ.copy()
    env.update(
        {
            "AMARU_NETWORK": network,
            "AMARU_PEER_ADDRESS": peer_address,
        }
    )
    daemon_args = [
        str(repo_root / "target/release/amaru"),
        "run",
        "--network",
        network,
        "--peer-address",
        peer_address,
        "--chain-dir",
        str(repo_root / f"chain.{network}.db"),
        "--ledger-dir",
        str(repo_root / f"ledger.{network}.db"),
        "--listen-address",
        listen_address,
        "--pid-file",
        str(pid_file),
    ]
    with daemon_out_path.open("w", encoding="utf-8") as stdout_handle, daemon_log_path.open("w", encoding="utf-8") as stderr_handle:
        proc = subprocess.Popen(
            daemon_args,
            cwd=str(repo_root),
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            start_new_session=True,
        )

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if pid_file.exists() and _listener_ok(listen_host, listen_port):
            break
        if proc.poll() is not None:
            break
        time.sleep(1)
    else:
        proc.terminate()
        raise RuntimeError("preview daemon listener did not come up before timeout")

    final_tip = None
    while time.time() < deadline:
        daemon_stderr = daemon_log_path.read_text(encoding="utf-8", errors="replace")
        final_tip = extract_latest_adopted_tip(daemon_stderr)
        if final_tip is not None and int(final_tip["slot"]) > int(imported_tip["slot"]):
            break
        if proc.poll() is not None:
            break
        time.sleep(2)

    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)

    daemon_exit_code = proc.returncode
    daemon_stderr = daemon_log_path.read_text(encoding="utf-8", errors="replace")
    if final_tip is None:
        final_tip = extract_latest_adopted_tip(daemon_stderr)

    progress_ok = final_tip is not None and int(final_tip["slot"]) > int(imported_tip["slot"])
    outcome = "ok" if progress_ok else "no_tip_advance"
    result = {
        "outcome": outcome,
        "bootstrap_exit_code": bootstrap.returncode,
        "daemon_exit_code": daemon_exit_code,
        "repo_root": str(repo_root),
        "network": network,
        "peer_address": peer_address,
        "listen_address": listen_address,
        "imported_snapshot_tip": imported_tip,
        "final_tip": final_tip,
        "snapshot_count": len(provenance["snapshots"]),
    }
    write_bundle_artifacts(
        result=result,
        bootstrap_stdout=bootstrap.stdout,
        bootstrap_stderr=bootstrap.stderr,
        daemon_stderr=daemon_stderr,
        final_tip=final_tip,
        provenance=provenance,
    )
    emit_phase_event("amaru_preview_bootstrap_result", result, level="info" if progress_ok else "error")
    if not progress_ok:
        raise RuntimeError("preview daemon did not advance tip beyond imported snapshot")
    print(
        f"imported_slot={imported_tip['slot']} "
        f"final_slot={final_tip['slot']} "
        f"peer_address={peer_address} "
        f"progress_ok=true"
    )
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["proof"])
    parser.add_argument("--repo-root", default=str(DEFAULT_REPO_ROOT))
    parser.add_argument("--peer-address", default=DEFAULT_PEER)
    parser.add_argument("--listen-address", default=DEFAULT_LISTEN)
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args(argv[1:])

    if args.mode != "proof":
        raise RuntimeError(f"unsupported mode: {args.mode}")
    return run_proof(
        repo_root=Path(args.repo_root),
        peer_address=args.peer_address,
        listen_address=args.listen_address,
        network=args.network,
        timeout_seconds=args.timeout_seconds,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv))
