#!/usr/bin/env python3
"""Build an atomic, native Amaru v5 bootstrap bundle from a live Cardano ChainDB."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

from resolve_snapshot_points import parse_rows, select_points


RETRY_EXIT = 75
MAX_DIAGNOSTIC_CHARS = 65_536
READY_CONTENT = "schema_version=5\namaru_source=493bffba0cc4db2291643cdd6698197c374958b3\n"
CHAIN_DB_ENTRIES = ("immutable", "ledger", "volatile", "protocolMagicId", "lock")
GLOBAL_ENV = {
    "consensus_security_param": "AMARU_GLOBAL_CONSENSUS_SECURITY_PARAM",
    "epoch_length_scale_factor": "AMARU_GLOBAL_EPOCH_LENGTH_SCALE_FACTOR",
    "active_slot_coeff_inverse": "AMARU_GLOBAL_ACTIVE_SLOT_COEFF_INVERSE",
    "max_lovelace_supply": "AMARU_GLOBAL_MAX_LOVELACE_SUPPLY",
    "slots_per_kes_period": "AMARU_GLOBAL_SLOTS_PER_KES_PERIOD",
    "max_kes_evolution": "AMARU_GLOBAL_MAX_KES_EVOLUTION",
    "system_start": "AMARU_GLOBAL_SYSTEM_START",
}


class RetryableBootstrapError(Exception):
    pass


class FatalBootstrapError(Exception):
    pass


def bounded(value):
    value = value or ""
    if len(value) <= MAX_DIAGNOSTIC_CHARS:
        return value
    return value[-MAX_DIAGNOSTIC_CHARS:]


def bundle_complete(final):
    final = Path(final)
    ready = final / "READY.v5"
    snapshots = final / "snapshots"
    try:
        archives = sorted(snapshots.glob("*.tar.zst"))
        return (
            ready.is_file()
            and ready.read_text(encoding="utf-8") == READY_CONTENT
            and (final / "era-history.json").is_file()
            and (final / "global-parameters.json").is_file()
            and (final / "chain.testnet_42.db" / "CURRENT").is_file()
            and (final / "ledger.testnet_42.db" / "live").is_dir()
            and len(archives) == 3
            and all(archive.is_file() and archive.stat().st_size > 0 for archive in archives)
        )
    except OSError:
        return False


def shelley_system_start(config_dir):
    try:
        config = json.loads((Path(config_dir) / "config.json").read_text(encoding="utf-8"))
        genesis_path = Path(config["ShelleyGenesisFile"])
        if not genesis_path.is_absolute():
            genesis_path = Path(config_dir) / genesis_path
        genesis = json.loads(genesis_path.read_text(encoding="utf-8"))
        timestamp = str(genesis["systemStart"])
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("systemStart has no timezone")
        return int(parsed.timestamp() * 1000)
    except (KeyError, OSError, json.JSONDecodeError, ValueError) as error:
        raise FatalBootstrapError(f"invalid Shelley systemStart: {error}") from error


def load_runtime_parameters(era_path, global_path, epoch_length, config_dir):
    try:
        era_history = json.loads(Path(era_path).read_text(encoding="utf-8"))
        global_parameters = json.loads(Path(global_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FatalBootstrapError(f"invalid runtime configuration: {error}") from error

    try:
        era_lengths = {int(era["params"]["epoch_size_slots"]) for era in era_history["eras"]}
    except (KeyError, TypeError, ValueError) as error:
        raise FatalBootstrapError(f"invalid era history: {error}") from error
    if era_lengths != {epoch_length}:
        raise FatalBootstrapError(
            f"era history epoch lengths {sorted(era_lengths)} do not match {epoch_length}"
        )

    missing = sorted(set(GLOBAL_ENV) - set(global_parameters))
    if missing:
        raise FatalBootstrapError(f"missing global parameters: {', '.join(missing)}")
    try:
        normalized = {
            key: int(global_parameters[key])
            for key in GLOBAL_ENV
            if key != "system_start"
        }
    except (TypeError, ValueError) as error:
        raise FatalBootstrapError(f"global parameters must be integers: {error}") from error
    derived_system_start = shelley_system_start(config_dir)
    configured_system_start = global_parameters["system_start"]
    if configured_system_start != "from_shelley_genesis":
        try:
            if int(configured_system_start) != derived_system_start:
                raise FatalBootstrapError(
                    "configured system_start does not match Shelley genesis"
                )
        except (TypeError, ValueError) as error:
            raise FatalBootstrapError(
                "system_start must be an integer or from_shelley_genesis"
            ) from error
    normalized["system_start"] = derived_system_start
    derived_epoch_length = (
        normalized["consensus_security_param"]
        * normalized["epoch_length_scale_factor"]
        * normalized["active_slot_coeff_inverse"]
    )
    if derived_epoch_length != epoch_length:
        raise FatalBootstrapError(
            f"global parameters derive epoch length {derived_epoch_length}, expected {epoch_length}"
        )
    return normalized


def runtime_environment(global_parameters, era_history):
    env = os.environ.copy()
    env["AMARU_ERA_HISTORY"] = str(Path(era_history).resolve())
    for key, env_name in GLOBAL_ENV.items():
        env[env_name] = str(global_parameters[key])
    return env


def run_checked(argv, *, cwd=None, env=None, timeout=1800, echo=False):
    result = subprocess.run(
        [str(part) for part in argv],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if echo:
        if result.stdout:
            print(bounded(result.stdout), end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(
                bounded(result.stderr),
                file=sys.stderr,
                end="" if result.stderr.endswith("\n") else "\n",
            )
    return result


def live_target_epoch_nonce(cardano_cli, socket_path, network, target_epoch, epoch_length, timeout):
    try:
        network_magic = int(network.removeprefix("testnet_"))
    except ValueError as error:
        raise FatalBootstrapError(f"cannot derive network magic from {network}") from error
    result = run_checked(
        [
            cardano_cli,
            "conway",
            "query",
            "protocol-state",
            "--socket-path",
            socket_path,
            "--testnet-magic",
            str(network_magic),
        ],
        timeout=timeout,
    )
    try:
        state = json.loads(result.stdout)
        last_slot = int(state["lastSlot"])
        epoch_nonce = str(state["epochNonce"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise FatalBootstrapError(f"invalid live protocol state: {error}") from error
    if last_slot < target_epoch * epoch_length:
        raise RetryableBootstrapError(
            f"live node has not entered target epoch {target_epoch}: last slot {last_slot}"
        )
    if last_slot >= (target_epoch + 1) * epoch_length:
        raise FatalBootstrapError(
            f"live node advanced beyond target epoch {target_epoch}: last slot {last_slot}"
        )
    try:
        nonce_bytes = bytes.fromhex(epoch_nonce)
    except ValueError as error:
        raise FatalBootstrapError(f"invalid live epoch nonce: {error}") from error
    if len(nonce_bytes) != 32:
        raise FatalBootstrapError(
            f"invalid live epoch nonce: expected 32 bytes, got {len(nonce_bytes)}"
        )
    return epoch_nonce.lower()


def copy_live_chain_db(live, destination):
    live = Path(live)
    destination = Path(destination)
    try:
        destination.mkdir(parents=True)
        for name in CHAIN_DB_ENTRIES:
            source = live / name
            if not source.exists():
                raise FileNotFoundError(source)
            target = destination / name
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
    except OSError as error:
        raise RetryableBootstrapError(f"live ChainDB copy raced or is not ready: {error}") from error


def create_bundle(args):
    final = Path(args.output_root) / args.network
    if bundle_complete(final):
        print(f"bootstrap bundle already complete: {final}")
        return
    if final.exists():
        raise FatalBootstrapError(f"refusing incomplete existing output: {final}")

    config_dir = Path(args.config_dir).resolve()
    config_json = config_dir / "config.json"
    if not config_json.is_file():
        raise FatalBootstrapError(f"missing cardano-node config: {config_json}")
    global_parameters = load_runtime_parameters(
        args.era_history, args.global_parameters, args.epoch_length, config_dir
    )
    env = runtime_environment(global_parameters, args.era_history)

    token = f"{os.getpid()}-{uuid.uuid4().hex[:12]}"
    output_root = Path(args.output_root)
    scratch_root = Path(args.scratch_root)
    output_root.mkdir(parents=True, exist_ok=True)
    scratch_root.mkdir(parents=True, exist_ok=True)
    stage = output_root / f".{args.network}.tmp-{token}"
    copied_db = scratch_root / f".chain-copy-{token}"
    work = scratch_root / f".bootstrap-work-{token}"

    try:
        copy_live_chain_db(args.live_db, copied_db)
        analyser = run_checked(
            [
                args.db_analyser,
                "--db",
                copied_db,
                "--show-slot-block-no",
                "--in-mem",
                "--config",
                config_json,
            ],
            timeout=args.command_timeout,
        )
        try:
            analyser_rows = f"{analyser.stdout}\n{analyser.stderr}"
            points = select_points(parse_rows(analyser_rows), args.epoch_length, count=3)
        except ValueError as error:
            if str(error) == "no block rows in db-analyser output" or "fewer than three completed epochs" in str(error):
                raise RetryableBootstrapError(str(error)) from error
            raise FatalBootstrapError(str(error)) from error

        target_epoch = 3
        live_nonce = live_target_epoch_nonce(
            args.cardano_cli,
            args.cardano_socket,
            args.network,
            target_epoch,
            args.epoch_length,
            args.command_timeout,
        )
        env["AMARU_BOOTSTRAP_ACTIVE_NONCE"] = live_nonce
        env["AMARU_BOOTSTRAP_ACTIVE_EPOCH"] = str(target_epoch)
        snapshot_dir = work / "snapshots" / args.network
        dist_dir = work / "dist"
        snapshot_dir.mkdir(parents=True)
        snapshot_command = [
            args.amaru,
            "snapshot",
            "create",
            "--network",
            args.network,
            "--epoch",
            str(target_epoch),
            "--dist-dir",
            dist_dir,
            "--snapshot-dir",
            snapshot_dir,
            "--cardano-node-config-dir",
            config_dir,
            "--cardano-node-db",
            copied_db,
        ]
        for point in points:
            snapshot_command.extend(("--snapshot", point))
        run_checked(
            snapshot_command,
            env=env,
            timeout=args.command_timeout,
            echo=True,
        )

        archives = sorted(snapshot_dir.glob("*.tar.zst"))
        if len(archives) != 3 or any(archive.stat().st_size == 0 for archive in archives):
            raise FatalBootstrapError(f"snapshot create produced {len(archives)} valid archives, expected 3")

        stage.mkdir()
        ledger_dir = stage / f"ledger.{args.network}.db"
        chain_dir = stage / f"chain.{args.network}.db"
        run_checked(
            [
                args.amaru,
                "node",
                "bootstrap",
                "--network",
                args.network,
                "--epoch",
                str(target_epoch),
                "--ledger-dir",
                ledger_dir,
                "--chain-dir",
                chain_dir,
            ],
            cwd=work,
            env=env,
            timeout=args.command_timeout,
            echo=True,
        )
        run_checked(
            [
                args.amaru,
                "dev",
                "chain",
                "migrate",
                "--network",
                args.network,
                "--chain-dir",
                chain_dir,
            ],
            env=env,
            timeout=args.command_timeout,
            echo=True,
        )

        if not (ledger_dir / "live").is_dir() or not (chain_dir / "CURRENT").is_file():
            raise FatalBootstrapError("native Amaru stores are incomplete after bootstrap")
        shutil.copy2(args.era_history, stage / "era-history.json")
        (stage / "global-parameters.json").write_text(
            json.dumps(global_parameters, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        stage_snapshots = stage / "snapshots"
        stage_snapshots.mkdir()
        for archive in archives:
            shutil.copy2(archive, stage_snapshots / archive.name)
        (stage / "READY.v5").write_text(READY_CONTENT, encoding="utf-8")
        if not bundle_complete(stage):
            raise FatalBootstrapError("refusing to commit incomplete native v5 bundle")
        os.replace(stage, final)
        print(f"bootstrap bundle committed: {final}")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(copied_db, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-db", required=True)
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--scratch-root", required=True)
    parser.add_argument("--era-history", required=True)
    parser.add_argument("--global-parameters", required=True)
    parser.add_argument("--network", default=os.environ.get("AMARU_NETWORK", "testnet_42"))
    parser.add_argument("--epoch-length", required=True, type=int)
    parser.add_argument("--db-analyser", default="/usr/local/bin/db-analyser")
    parser.add_argument("--amaru", default="/usr/local/bin/amaru")
    parser.add_argument("--cardano-cli", default="/usr/local/bin/cardano-cli")
    parser.add_argument("--cardano-socket", default="/live/node.socket")
    parser.add_argument("--command-timeout", default=1800, type=int)
    return parser.parse_args(argv)


def main(argv=None):
    try:
        create_bundle(parse_args(argv))
        return 0
    except RetryableBootstrapError as error:
        print(f"bootstrap retryable: {error}", file=sys.stderr)
        return RETRY_EXIT
    except subprocess.CalledProcessError as error:
        if error.stdout:
            print(bounded(error.stdout), file=sys.stderr, end="" if error.stdout.endswith("\n") else "\n")
        if error.stderr:
            print(bounded(error.stderr), file=sys.stderr, end="" if error.stderr.endswith("\n") else "\n")
        return error.returncode if 0 < error.returncode < 256 and error.returncode != RETRY_EXIT else 1
    except subprocess.TimeoutExpired as error:
        print(f"bootstrap command timed out: {error}", file=sys.stderr)
        return 1
    except (FatalBootstrapError, OSError) as error:
        print(f"bootstrap fatal: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
