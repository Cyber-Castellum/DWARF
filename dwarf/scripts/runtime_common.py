#!/usr/bin/env python3

import json
import re
import shutil
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeConfig:
    runtime_root: Path
    env_root: Path
    session: str
    cardano_cli: Path
    cardano_node: Path
    network_magic: str
    blockfetch_bin: str
    chainsync_bin: str
    log_paths: tuple[Path, ...]
    fallback_range: tuple[str, str]


PROFILE_A_CONFIG = RuntimeConfig(
    runtime_root=Path("/opt/dwarf/cardano-profiles/profile-a-haskell-peersharing-disabled"),
    env_root=Path("/opt/dwarf/cardano-profiles/profile-a-haskell-peersharing-disabled/env"),
    session="cardano-profile-a",
    cardano_cli=Path("/home/nigel/.local/bin/cardano-cli"),
    cardano_node=Path("/home/nigel/.local/bin/cardano-node"),
    network_magic="42",
    blockfetch_bin="/home/nigel/dwarf-fw/targets/amaru/target/release/dwarf-amaru-runtime-blockfetch",
    chainsync_bin="/home/nigel/amaru-verification/target/debug/amaru",
    log_paths=(
        Path("/opt/dwarf/cardano-profiles/profile-a-haskell-peersharing-disabled/logs/node3/stdout.log"),
        Path("/opt/dwarf/cardano-profiles/profile-a-haskell-peersharing-disabled/logs/node2/stdout.log"),
        Path("/opt/dwarf/cardano-profiles/profile-a-haskell-peersharing-disabled/logs/node1/stdout.log"),
    ),
    fallback_range=(
        "4318100.9166aa0afdf7fc09ff1504b987371f62b24022b81726a7cff09165d78069e50b",
        "4318130.f71200f374e97287835e573fadfa9a051bbb7dd40f85f746973f28bed5d43fb0",
    ),
)


def run(args, *, timeout=60, check=True, text=True, capture_output=True, env=None):
    return subprocess.run(
        args,
        timeout=timeout,
        check=check,
        text=text,
        capture_output=capture_output,
        env=env,
    )


def directory_size_bytes(path: Path) -> int:
    total = 0
    for item in Path(path).rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def session_exists(config: RuntimeConfig) -> bool:
    result = subprocess.run(["tmux", "has-session", "-t", config.session], text=True, capture_output=True)
    return result.returncode == 0


def matching_pids(pattern: str):
    result = subprocess.run(["pgrep", "-f", pattern], text=True, capture_output=True)
    if result.returncode != 0:
        return []
    return [int(line.strip()) for line in result.stdout.splitlines() if line.strip()]


def startup_command(config: RuntimeConfig) -> str:
    start_log = config.runtime_root / "logs" / "start-devnet.log"
    return (
        f"cd {config.runtime_root}; "
        f"export PATH={Path.home() / '.local' / 'bin'}:$PATH; "
        f"export CARDANO_CLI={config.cardano_cli}; "
        f"export CARDANO_NODE={config.cardano_node}; "
        f"cardano-testnet cardano --node-env {config.env_root} --num-pool-nodes 3 --update-time 2>&1 | tee {start_log}"
    )


def stop_session(config: RuntimeConfig) -> None:
    details = stop_session_with_details(config)
    if details["remaining_pids"]:
        raise RuntimeError("profile processes did not stop within 20 seconds")


def stop_session_with_details(config: RuntimeConfig) -> dict:
    started_at = time.monotonic()
    details = {
        "session_existed": session_exists(config),
        "killed_pids": [],
        "forced_kills": [],
        "remaining_pids": [],
    }
    if session_exists(config):
        result = subprocess.run(
            ["tmux", "kill-session", "-t", config.session],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0 and session_exists(config):
            raise RuntimeError(result.stderr.strip() or f"failed to kill tmux session {config.session}")
    patterns = [
        f"cardano-testnet cardano --node-env {config.env_root}",
        f"cardano-node run --config {config.env_root / 'configuration.yaml'}",
    ]
    pids = []
    for pattern in patterns:
        pids.extend(matching_pids(pattern))
    for pid in sorted(set(pids)):
        subprocess.run(["kill", str(pid)], check=False, text=True, capture_output=True)
        details["killed_pids"].append(pid)
    deadline = time.time() + 20
    remaining = []
    while time.time() < deadline:
        remaining = []
        for pattern in patterns:
            remaining.extend(matching_pids(pattern))
        if not remaining:
            details["duration_seconds"] = round(time.monotonic() - started_at, 6)
            return details
        time.sleep(1)
    for pid in sorted(set(remaining)):
        subprocess.run(["kill", "-9", str(pid)], check=False, text=True, capture_output=True)
        details["forced_kills"].append(pid)
    time.sleep(1)
    remaining = []
    for pattern in patterns:
        remaining.extend(matching_pids(pattern))
    details["remaining_pids"] = sorted(set(remaining))
    details["duration_seconds"] = round(time.monotonic() - started_at, 6)
    return details


def start_session(config: RuntimeConfig) -> None:
    details = start_session_with_details(config)
    if details.get("start_failed"):
        raise RuntimeError(f"failed to start tmux session {config.session}")


def start_session_with_details(config: RuntimeConfig) -> dict:
    started_at = time.monotonic()
    details = {"already_running": session_exists(config)}
    if session_exists(config):
        details["duration_seconds"] = round(time.monotonic() - started_at, 6)
        return details
    result = subprocess.run(
        ["tmux", "new-session", "-d", "-s", config.session, "bash", "-lc", startup_command(config)],
        check=True,
        text=True,
        capture_output=True,
    )
    details["started"] = result.returncode == 0
    details["duration_seconds"] = round(time.monotonic() - started_at, 6)
    return details


def query_tip(config: RuntimeConfig, node: str):
    socket_candidates = (
        config.runtime_root / "socket" / f"{node}.sock",
        config.runtime_root / "socket" / node / "sock",
        config.env_root / "socket" / node / "sock",
    )
    existing_candidates = []
    seen = set()
    for path in socket_candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists():
            existing_candidates.append(path)
    if not existing_candidates:
        existing_candidates = [socket_candidates[0]]

    last_error = None
    for socket_path in existing_candidates:
        env = {"CARDANO_NODE_SOCKET_PATH": str(socket_path)}
        result = run(
            [str(config.cardano_cli), "query", "tip", "--testnet-magic", config.network_magic],
            timeout=15,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            last_error = result.stderr.strip() or f"tip query failed for {node}"
            continue
        data = json.loads(result.stdout)
        return {
            "slot": int(data.get("slot", 0)),
            "block": int(data.get("block", 0)),
            "syncProgress": str(data.get("syncProgress", "")),
            "hash": data.get("hash", ""),
        }
    raise RuntimeError(last_error or f"tip query failed for {node}")


def _runtime_metadata_entry(config: RuntimeConfig, node: str):
    metadata_path = config.runtime_root / "runtime.json"
    if not metadata_path.exists():
        return None
    try:
        body = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    for entry in body.get("haskell_nodes", []):
        if entry.get("name") == node:
            return entry
    return None


def _running_process_port(entry: dict) -> int | None:
    pid_file = entry.get("pid_file")
    if not pid_file:
        return None
    try:
        pid = Path(pid_file).read_text(encoding="utf-8").strip()
        if not pid:
            return None
        result = subprocess.run(
            ["ps", "-p", pid, "-o", "args="],
            check=False,
            text=True,
            capture_output=True,
        )
    except Exception:  # noqa: BLE001
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"(?:^|\s)--port\s+(\d+)(?:\s|$)", result.stdout)
    if not match:
        return None
    return int(match.group(1))


def _runtime_metadata_port(config: RuntimeConfig, node: str):
    entry = _runtime_metadata_entry(config, node)
    if entry is None:
        return None
    live_port = _running_process_port(entry)
    if live_port is not None:
        return live_port
    if "port" in entry:
        return int(entry["port"])
    return None


def target_port(config: RuntimeConfig, node: str) -> int:
    runtime_port = _runtime_metadata_port(config, node)
    if runtime_port is not None:
        return runtime_port
    return int((config.env_root / "node-data" / node / "port").read_text(encoding="utf-8").strip())


def wait_for_all_tips(config: RuntimeConfig, nodes, *, min_slot=None, timeout_seconds=120):
    tips, details = wait_for_all_tips_with_details(
        config,
        nodes,
        min_slot=min_slot,
        timeout_seconds=timeout_seconds,
    )
    if details.get("timed_out"):
        raise RuntimeError(f"timed out waiting for all tips: {details.get('last_error')}")
    return tips


def wait_for_all_tips_with_details(config: RuntimeConfig, nodes, *, min_slot=None, timeout_seconds=120):
    started_at = time.monotonic()
    deadline = time.time() + timeout_seconds
    last_error = None
    attempts = 0
    while time.time() < deadline:
        attempts += 1
        try:
            tips = {node: query_tip(config, node) for node in nodes}
            if min_slot is not None and any(info["slot"] < min_slot for info in tips.values()):
                time.sleep(2)
                continue
            return tips, {
                "attempts": attempts,
                "duration_seconds": round(time.monotonic() - started_at, 6),
                "timed_out": False,
                "min_slot": min_slot,
            }
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(2)
    return None, {
        "attempts": attempts,
        "duration_seconds": round(time.monotonic() - started_at, 6),
        "timed_out": True,
        "last_error": str(last_error) if last_error else None,
        "min_slot": min_slot,
    }


def wait_for_node_slots(config: RuntimeConfig, required_slots: dict[str, int], *, timeout_seconds=120):
    tips, details = wait_for_node_slots_with_details(
        config,
        required_slots,
        timeout_seconds=timeout_seconds,
    )
    if details.get("timed_out"):
        raise RuntimeError(f"timed out waiting for node slots {required_slots}: {details.get('last_error')}")
    return tips


def wait_for_node_slots_with_details(config: RuntimeConfig, required_slots: dict[str, int], *, timeout_seconds=120):
    started_at = time.monotonic()
    deadline = time.time() + timeout_seconds
    last_error = None
    attempts = 0
    while time.time() < deadline:
        attempts += 1
        try:
            tips = {node: query_tip(config, node) for node in required_slots}
            if any(tips[node]["slot"] < min_slot for node, min_slot in required_slots.items()):
                time.sleep(2)
                continue
            return tips, {
                "attempts": attempts,
                "duration_seconds": round(time.monotonic() - started_at, 6),
                "timed_out": False,
                "required_slots": dict(required_slots),
            }
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(2)
    return None, {
        "attempts": attempts,
        "duration_seconds": round(time.monotonic() - started_at, 6),
        "timed_out": True,
        "last_error": str(last_error) if last_error else None,
        "required_slots": dict(required_slots),
    }


def extract_range_from_tail(tail_text: str) -> tuple[str, str] | None:
    tips = deque(maxlen=8)
    for line in tail_text.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("ns") != "ChainDB.AddBlockEvent.AddedToCurrentChain":
            continue
        newtip = obj.get("data", {}).get("newtip")
        if not isinstance(newtip, str) or "@" not in newtip:
            continue
        hash_value, slot_value = newtip.split("@", 1)
        tips.append((int(slot_value), hash_value))
    if len(tips) < 8:
        return None
    from_slot, from_hash = tips[-8]
    to_slot, to_hash = tips[-7]
    return f"{from_slot}.{from_hash}", f"{to_slot}.{to_hash}"


def derive_range(config: RuntimeConfig) -> tuple[str, str]:
    for log_path in config.log_paths:
        try:
            tail = run(["tail", "-n", "20000", str(log_path)], timeout=15, check=True)
        except subprocess.CalledProcessError:
            continue
        result = extract_range_from_tail(tail.stdout)
        if result is not None:
            return result
    return config.fallback_range


def derive_chainsync_point(config: RuntimeConfig) -> str:
    for log_path in config.log_paths:
        try:
            tail = run(["tail", "-n", "20000", str(log_path)], timeout=15, check=True)
        except subprocess.CalledProcessError:
            continue
        tips = deque(maxlen=6)
        for line in tail.stdout.splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("ns") != "ChainDB.AddBlockEvent.AddedToCurrentChain":
                continue
            newtip = obj.get("data", {}).get("newtip")
            if not isinstance(newtip, str) or "@" not in newtip:
                continue
            hash_value, slot_value = newtip.split("@", 1)
            tips.append((int(slot_value), hash_value))
        if len(tips) >= 6:
            slot_value, hash_value = tips[0]
            return f"{slot_value}.{hash_value}"
    from_point, _ = config.fallback_range
    return from_point


def point_slot(point: str) -> int | None:
    if not isinstance(point, str) or "." not in point:
        return None
    slot_text, _hash = point.split(".", 1)
    try:
        return int(slot_text)
    except ValueError:
        return None


def point_span(from_point: str, to_point: str) -> int | None:
    from_slot = point_slot(from_point)
    to_slot = point_slot(to_point)
    if from_slot is None or to_slot is None:
        return None
    return max(0, to_slot - from_slot)


def run_blockfetch(config: RuntimeConfig, peer_port: int, from_point: str, to_point: str, *, timeout_seconds=6):
    return run(
        [
            "timeout",
            f"{timeout_seconds}s",
            config.blockfetch_bin,
            f"127.0.0.1:{peer_port}",
            config.network_magic,
            from_point,
            to_point,
            "2",
        ],
        timeout=timeout_seconds + 5,
        check=False,
    )


def run_chainsync_fetch(config: RuntimeConfig, peer_port: int, point: str, output_dir: Path, *, timeout_seconds=6):
    return run(
        [
            "timeout",
            f"{timeout_seconds}s",
            config.chainsync_bin,
            "fetch-chain-headers",
            "--network",
            f"testnet_{config.network_magic}",
            "--peer-address",
            f"127.0.0.1:{peer_port}",
            "--headers-dir",
            str(output_dir),
            "--parent",
            point,
        ],
        timeout=timeout_seconds + 5,
    )


def replace_node_db(config: RuntimeConfig, target_node: str, source_db: Path) -> None:
    target_db = config.env_root / "node-data" / target_node / "db"
    shutil.rmtree(target_db)
    shutil.copytree(source_db, target_db)
