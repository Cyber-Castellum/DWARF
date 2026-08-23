#!/usr/bin/env python3
"""Condition-driven local proof for the runtime Cardano/Amaru/DWARF package."""
import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path


ADOPT_RE = re.compile(
    r"tip\.adopt slot=(?P<slot>\d+) header_hash=(?P<hash>[0-9a-f]+).*?block_height=(?P<height>\d+)"
    r"|adopted tip tip\.slot=(?P<slot2>\d+) tip\.hash=(?P<hash2>[0-9a-f]+).*?"
    r"(?:tip\.)?block_height=(?P<height2>\d+)"
)
DECODE_RE = re.compile(r"failed to decode message from network|invalid cbor|decode error", re.I)
REWARD_RE = re.compile(r"discrepancy between expected total rewards.*actual total rewards", re.I)
SCHEMA_RE = re.compile(r"IncompatibleChainStoreVersions|cannot be migrated to version 5|schema mismatch", re.I)
PANIC_RE = re.compile(r"Whoops! The Amaru process panicked|thread .* panicked|amaru::fatal", re.I)
CONSENSUS_RE = re.compile(
    r"header validation failed:.*(?:Invalid VRF proof|VRF proof verification failed|VerificationFailed)",
    re.I,
)


@dataclass(frozen=True)
class Tip:
    block: int
    block_hash: str
    slot: int


@dataclass(frozen=True)
class RelayObservation:
    first_height: int
    max_height: int
    first_slot: int
    max_slot: int
    decoder_rejections: int
    fatal_signatures: tuple
    consensus_rejections: int = 0


@dataclass(frozen=True)
class ProofSample:
    bootstrap_exit: int
    target: RelayObservation
    control: RelayObservation
    consumer: Tip
    producers: tuple
    restarts: dict


def parse_json_rows(text):
    text = text.strip()
    if not text:
        return []
    try:
        value = json.loads(text)
        return value if isinstance(value, list) else [value]
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def parse_tip(text):
    value = json.loads(text)
    block_hash = value.get("hash")
    if not isinstance(block_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", block_hash):
        raise ValueError("tip JSON has no valid block hash")
    try:
        return Tip(int(value["block"]), block_hash, int(value["slot"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"invalid tip JSON: {error}") from error


def parse_relay_log(text):
    adoptions = []
    for match in ADOPT_RE.finditer(text):
        adoptions.append(
            (
                int(match.group("height") or match.group("height2")),
                int(match.group("slot") or match.group("slot2")),
            )
        )
    signatures = []
    if REWARD_RE.search(text):
        signatures.append("reward discrepancy")
    if SCHEMA_RE.search(text):
        signatures.append("schema mismatch")
    if PANIC_RE.search(text):
        signatures.append("panic")
    if not adoptions:
        return RelayObservation(
            0,
            0,
            0,
            0,
            len(DECODE_RE.findall(text)),
            tuple(signatures),
            len(CONSENSUS_RE.findall(text)),
        )
    return RelayObservation(
        first_height=adoptions[0][0],
        max_height=max(height for height, _ in adoptions),
        first_slot=adoptions[0][1],
        max_slot=max(slot for _, slot in adoptions),
        decoder_rejections=len(DECODE_RE.findall(text)),
        fatal_signatures=tuple(signatures),
        consensus_rejections=len(CONSENSUS_RE.findall(text)),
    )


def proof_conditions(baseline, current, epoch_length):
    consumer_matches = any(
        current.consumer.block == producer.block
        and current.consumer.block_hash == producer.block_hash
        for producer in current.producers
    )
    return {
        "bootstrap_exit_zero": current.bootstrap_exit == 0,
        "target_advanced": current.target.max_height > baseline.target.max_height,
        "control_advanced": current.control.max_height > baseline.control.max_height,
        "epoch_crossed": (
            current.target.max_slot // epoch_length > baseline.target.max_slot // epoch_length
            and current.control.max_slot // epoch_length > baseline.control.max_slot // epoch_length
        ),
        "decoder_rejection_observed": current.target.decoder_rejections > 0,
        "consumer_advanced": current.consumer.block > baseline.consumer.block,
        "consumer_matches_producer": consumer_matches,
        "no_fatal_signatures": not current.target.fatal_signatures and not current.control.fatal_signatures,
        "control_consensus_clean": current.control.consensus_rejections == 0,
        "relay_restart_counts_stable": current.restarts == baseline.restarts,
    }


def wait_until(
    probe,
    predicate,
    *,
    timeout,
    interval,
    description,
    clock=time.monotonic,
    sleeper=time.sleep,
):
    deadline = clock() + timeout
    last = None
    while True:
        last = probe()
        if predicate(last):
            return last
        if clock() >= deadline:
            raise TimeoutError(f"timed out waiting for {description}; last={last!r}")
        sleeper(interval)


class ComposeRuntime:
    def __init__(self, project, compose_file):
        self.base = ["docker", "compose", "-p", project, "-f", str(compose_file)]

    @staticmethod
    def command(argv, *, check=True):
        return subprocess.run(argv, check=check, capture_output=True, text=True)

    def compose(self, *args, check=True):
        return self.command([*self.base, *args], check=check)

    def bootstrap_exit(self):
        rows = parse_json_rows(
            self.compose("ps", "--all", "--format", "json", "bootstrap-producer").stdout
        )
        if not rows:
            return None
        row = rows[0]
        if str(row.get("State", "")).lower() != "exited":
            return None
        try:
            return int(row.get("ExitCode"))
        except (TypeError, ValueError):
            return None

    def relay(self, service):
        text = self.compose("logs", "--no-color", "--tail", "5000", service).stdout
        return parse_relay_log(text)

    def tip(self, service):
        result = self.compose(
            "exec",
            "-T",
            service,
            "cardano-cli",
            "query",
            "tip",
            "--socket-path",
            "/state/node.socket",
            "--testnet-magic",
            "42",
        )
        return parse_tip(result.stdout)

    def restart_count(self, service):
        container_id = self.compose("ps", "-q", service).stdout.strip()
        if not container_id:
            raise RuntimeError(f"no container for {service}")
        result = self.command(
            ["docker", "inspect", "--format", "{{.RestartCount}}", container_id]
        )
        return int(result.stdout.strip())

    def sample(self):
        bootstrap_exit = self.bootstrap_exit()
        if bootstrap_exit is None:
            raise RuntimeError("bootstrap producer has not exited")
        return ProofSample(
            bootstrap_exit=bootstrap_exit,
            target=self.relay("amaru-relay-1"),
            control=self.relay("amaru-relay-2"),
            consumer=self.tip("amaru-consumer"),
            producers=tuple(self.tip(service) for service in ("p1", "p2", "p3")),
            restarts={
                service: self.restart_count(service)
                for service in ("amaru-relay-1", "amaru-relay-2")
            },
        )

    def images(self):
        result = {}
        for service in ("bootstrap-producer", "amaru-relay-1", "amaru-relay-2", "dwarf-adversary"):
            container_id = self.compose("ps", "-q", service).stdout.strip()
            if not container_id:
                continue
            inspected = self.command(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.Config.Image}}\t{{.Image}}",
                    container_id,
                ]
            ).stdout.strip()
            image_name, image_sha = inspected.split("\t", 1)
            result[service] = {"name": image_name, "id": image_sha}
        return result


def sample_or_none(runtime):
    try:
        return runtime.sample()
    except (json.JSONDecodeError, OSError, RuntimeError, subprocess.CalledProcessError, ValueError):
        return None


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--compose-file", default="docker-compose.yaml")
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--epoch-length", type=int, default=400)
    parser.add_argument("--evidence", default="proof/runtime-proof.json")
    args = parser.parse_args(argv)

    runtime = ComposeRuntime(args.project, Path(args.compose_file).resolve())
    print("waiting: native v5 bootstrap exit 0", flush=True)
    wait_until(
        runtime.bootstrap_exit,
        lambda value: value == 0,
        timeout=args.timeout,
        interval=args.interval,
        description="bootstrap exit 0",
    )
    print("waiting: relay adoption and consumer query readiness", flush=True)
    baseline = wait_until(
        lambda: sample_or_none(runtime),
        lambda sample: (
            sample is not None
            and sample.target.max_height > 0
            and sample.control.max_height > 0
            and not sample.target.fatal_signatures
            and not sample.control.fatal_signatures
        ),
        timeout=args.timeout,
        interval=args.interval,
        description="initial mixed runtime sample",
    )
    print(
        f"baseline: target={baseline.target.max_height} control={baseline.control.max_height} "
        f"consumer={baseline.consumer.block}",
        flush=True,
    )

    last_report = 0.0

    def probe():
        nonlocal last_report
        sample = sample_or_none(runtime)
        if sample is None:
            return None
        conditions = proof_conditions(baseline, sample, args.epoch_length)
        if not conditions["no_fatal_signatures"]:
            raise RuntimeError(
                f"fatal relay signature: target={sample.target.fatal_signatures} "
                f"control={sample.control.fatal_signatures}"
            )
        if not conditions["control_consensus_clean"]:
            raise RuntimeError(
                f"control relay rejected honest consensus headers: {sample.control.consensus_rejections}"
            )
        if not conditions["relay_restart_counts_stable"]:
            raise RuntimeError(
                f"relay restart count changed: {baseline.restarts} -> {sample.restarts}"
            )
        now = time.monotonic()
        if now - last_report >= 20:
            pending = [name for name, passed in conditions.items() if not passed]
            print(
                f"progress: target={sample.target.max_height}/{sample.target.max_slot} "
                f"control={sample.control.max_height}/{sample.control.max_slot} "
                f"consumer={sample.consumer.block} pending={','.join(pending)}",
                flush=True,
            )
            last_report = now
        return sample, conditions

    final, conditions = wait_until(
        probe,
        lambda value: value is not None and all(value[1].values()),
        timeout=args.timeout,
        interval=args.interval,
        description="all mixed runtime proof conditions",
    )
    evidence = {
        "project": args.project,
        "epoch_length": args.epoch_length,
        "images": runtime.images(),
        "bootstrap_exit": final.bootstrap_exit,
        "initial": {
            "target": asdict(baseline.target),
            "control": asdict(baseline.control),
            "consumer": asdict(baseline.consumer),
        },
        "final": {
            "target": asdict(final.target),
            "control": asdict(final.control),
            "consumer": asdict(final.consumer),
            "producers": [asdict(tip) for tip in final.producers],
        },
        "restart_counts": final.restarts,
        "conditions": conditions,
        "commands": [
            "python3 -m unittest discover -s tests -v",
            "python3 -m unittest discover -s oracle -p 'test_*.py' -v",
            "python3 -m unittest discover -s bootstrap-image -p 'test_*.py' -v",
            "INTERNAL_NETWORK=false docker compose config --quiet",
        ],
    }
    evidence_path = Path(args.evidence)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"proof complete: {evidence_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
