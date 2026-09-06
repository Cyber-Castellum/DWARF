"""Fail-closed local proof for the additive Cardano/Amaru control package.

The script is invoked by DWARF on cardano-box. It gives the normalized Compose
model a unique project identity, starts fresh project-scoped volumes, and stops
only that project. Containers and volumes are retained for review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time


EPOCH_LENGTH = 400
RELAYS = ("amaru-relay-1", "amaru-relay-2")
CARDANO_TIP_NODES = ("p1", "p2", "p3", "amaru-consumer")
CARDANO_PRODUCERS = ("p1", "p2", "p3")
FATAL_PATTERNS = {
    "panic": re.compile(r"\bpanicked at\b", re.IGNORECASE),
    "reward-discrepancy": re.compile(
        r"discrepancy between expected total rewards", re.IGNORECASE
    ),
    "consensus-died": re.compile(r"\bConsensus died\b", re.IGNORECASE),
    "future-rollback": re.compile(r"attempted roll back in the future", re.IGNORECASE),
    "vrf-bad-proof": re.compile(r"VRFKeyBadProof", re.IGNORECASE),
}


def run(args, *, timeout=30, check=True, env=None):
    return subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=check,
        env=env,
    )


def parse_relay_log(text: str) -> dict:
    target_slots = []
    target_matches = re.findall(r"snapshot_slots=([0-9]+) ([0-9]+) ([0-9]+)", text)
    if target_matches:
        target_slots = [int(value) for value in target_matches[-1]]

    exec_marker = "exec'ing amaru run"
    exec_started = exec_marker in text
    runtime = text.rsplit(exec_marker, 1)[-1] if exec_started else ""
    runtime_slots = []
    for pattern in (
        r"slot:\s*Slot\(([0-9]+)\)",
        r'"slot"\s*:\s*([0-9]+)',
        r"\bslot=([0-9]+)\b",
    ):
        runtime_slots.extend(int(value) for value in re.findall(pattern, runtime))
    runtime_slots = sorted(set(runtime_slots))

    fatal_signatures = [
        name for name, pattern in FATAL_PATTERNS.items() if pattern.search(text)
    ]
    return {
        "target_slots": target_slots,
        "committed": "committed bundle to" in text,
        "exec_started": exec_started,
        "runtime_slots": runtime_slots,
        "max_runtime_slot": max(runtime_slots) if runtime_slots else None,
        "fatal_signatures": fatal_signatures,
    }


def relay_crossed_post_bootstrap_epoch(parsed: dict, *, epoch_length: int) -> bool:
    targets = parsed.get("target_slots") or []
    maximum = parsed.get("max_runtime_slot")
    if len(targets) != 3 or maximum is None:
        return False
    anchor_epoch = max(targets) // epoch_length
    # Bootstrap anchors in completed epoch K-1. Merely entering K is normal
    # startup; require observed progress into K+1 to prove a later transition.
    return maximum // epoch_length >= anchor_epoch + 2


def query_cardano_tip(container: str) -> dict | None:
    result = run(
        [
            "docker",
            "exec",
            container,
            "cardano-cli",
            "query",
            "tip",
            "--socket-path",
            "/state/node.socket",
            "--testnet-magic",
            "42",
        ],
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        tip = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not tip.get("hash") or not isinstance(tip.get("slot"), int):
        return None
    return tip


def query_genesis_start(container: str) -> dict | None:
    result = run(
        [
            "docker",
            "exec",
            container,
            "/bin/sh",
            "-c",
            "cat /configs/configs/shelley-genesis.json; "
            "printf '\\n---DWARF-GENESIS---\\n'; "
            "cat /configs/configs/byron-genesis.json",
        ],
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        return None
    parts = result.stdout.split("\n---DWARF-GENESIS---\n")
    if len(parts) != 2:
        return None
    try:
        shelley, byron = (json.loads(part) for part in parts)
    except json.JSONDecodeError:
        return None
    return {
        "shelley_system_start": shelley.get("systemStart"),
        "byron_start_time": byron.get("startTime"),
    }


def inspect_state(container: str) -> dict | None:
    result = run(["docker", "inspect", container], check=False)
    if result.returncode != 0:
        return None
    obj = json.loads(result.stdout)[0]
    return {
        "State": obj["State"],
        "RestartCount": obj["RestartCount"],
        "Image": obj["Image"],
        "Networks": obj["NetworkSettings"]["Networks"],
    }


def relay_log(container: str) -> str:
    result = run(["docker", "logs", container], timeout=30, check=False)
    return result.stdout + result.stderr


def render_isolated_compose(source: Path, out: Path, project: str) -> tuple[Path, dict]:
    env = dict(os.environ)
    env["INTERNAL_NETWORK"] = "false"
    rendered = json.loads(
        run(
            ["docker", "compose", "-f", str(source), "config", "--format", "json"],
            env=env,
        ).stdout
    )
    rendered["name"] = project
    for name, service in rendered["services"].items():
        service["container_name"] = f"{project}-{name}"
        if name not in {"configurator", "amaru-consumer-seed"}:
            service["restart"] = "no"
    for name, network in rendered.get("networks", {}).items():
        network["name"] = f"{project}-{name}"
    for name, volume in rendered.get("volumes", {}).items():
        if volume.get("external"):
            raise RuntimeError(f"control refuses external volume: {name}")
        volume["name"] = f"{project}-{name}"

    path = out / "compose.json"
    path.write_text(json.dumps(rendered, indent=2) + "\n", encoding="utf-8")
    run(["docker", "compose", "-p", project, "-f", str(path), "config", "--quiet"])
    return path, rendered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--sample-seconds", type=int, default=10)
    args = parser.parse_args()

    package = args.package.resolve()
    source = package / "docker-compose.yaml"
    if not source.is_file():
        raise SystemExit(f"missing Compose package: {source}")
    run_root = Path(os.environ["ADA2_DWARF_RUN_DIR"])
    out = run_root / "outputs" / "cardano-amaru-relay-bootstrap-control"
    out.mkdir(parents=True, exist_ok=False)
    project = "dwarf-relay-control-" + time.strftime("%Y%m%d%H%M%S", time.gmtime())
    compose_path, rendered = render_isolated_compose(source, out, project)
    containers = {
        name: f"{project}-{name}" for name in rendered["services"]
    }
    compose = ["docker", "compose", "-p", project, "-f", str(compose_path)]

    previous = run(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
        check=False,
    ).stdout.strip()
    if previous:
        raise SystemExit(f"fresh-project invariant failed for {project}: {previous}")

    (out / "provenance.json").write_text(
        json.dumps(
            {
                "source": str(source),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "project": project,
                "fresh_project": True,
                "security_workload": False,
                "images": {
                    name: spec["image"] for name, spec in rendered["services"].items()
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    started = time.monotonic()
    initial_consumer_slot = None
    sustained_matches = 0
    result = {
        "passed": False,
        "reason": "deadline before all mixed-control gates passed",
        "project": project,
    }
    launch = None
    print(f"control_project={project}", flush=True)
    print(f"evidence={out}", flush=True)

    try:
        up_log = (out / "compose-up.log").open("w", encoding="utf-8")
        launch = subprocess.Popen(
            compose + ["up", "-d"], stdout=up_log, stderr=subprocess.STDOUT, text=True
        )
        while time.monotonic() - started < args.timeout_seconds:
            elapsed = round(time.monotonic() - started)
            relay_evidence = {
                name: parse_relay_log(relay_log(containers[name])) for name in RELAYS
            }
            relay_states = {
                name: inspect_state(containers[name]) for name in RELAYS
            }
            tips = {
                name: tip
                for name in CARDANO_TIP_NODES
                if (tip := query_cardano_tip(containers[name])) is not None
            }
            genesis_starts = {
                name: start
                for name in CARDANO_PRODUCERS
                if (start := query_genesis_start(containers[name])) is not None
            }
            genesis_start_consistent = len(genesis_starts) == len(CARDANO_PRODUCERS) and len(
                {
                    (value["shelley_system_start"], value["byron_start_time"])
                    for value in genesis_starts.values()
                }
            ) == 1
            consumer = tips.get("amaru-consumer")
            if consumer is not None and initial_consumer_slot is None:
                initial_consumer_slot = consumer["slot"]

            consumer_advanced = bool(
                consumer is not None
                and initial_consumer_slot is not None
                and consumer["slot"] > initial_consumer_slot
            )
            producer_hashes = {
                tip["hash"] for name, tip in tips.items() if name in {"p1", "p2", "p3"}
            }
            consumer_matches = bool(
                consumer_advanced and consumer["hash"] in producer_hashes
            )
            if consumer_matches:
                sustained_matches += 1
            else:
                sustained_matches = 0

            relays_crossed = {
                name: relay_crossed_post_bootstrap_epoch(
                    evidence, epoch_length=EPOCH_LENGTH
                )
                for name, evidence in relay_evidence.items()
            }
            fatal = {
                name: evidence["fatal_signatures"]
                for name, evidence in relay_evidence.items()
                if evidence["fatal_signatures"]
            }
            unhealthy = {
                name: state
                for name, state in relay_states.items()
                if state is not None
                and (not state["State"]["Running"] or state["RestartCount"] != 0)
            }

            sample = {
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "elapsed_seconds": elapsed,
                "relay_evidence": relay_evidence,
                "relay_states": relay_states,
                "tips": tips,
                "genesis_starts": genesis_starts,
                "genesis_start_consistent": genesis_start_consistent,
                "initial_consumer_slot": initial_consumer_slot,
                "consumer_advanced": consumer_advanced,
                "consumer_matches_producer": consumer_matches,
                "sustained_matching_samples": sustained_matches,
                "relays_crossed_post_bootstrap_epoch": relays_crossed,
            }
            with (out / "samples.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(sample, sort_keys=True) + "\n")
            print(
                json.dumps(
                    {
                        "elapsed": elapsed,
                        "targets": {
                            name: evidence["target_slots"]
                            for name, evidence in relay_evidence.items()
                        },
                        "relay_max_slots": {
                            name: evidence["max_runtime_slot"]
                            for name, evidence in relay_evidence.items()
                        },
                        "relays_crossed": relays_crossed,
                        "tip_slots": {name: tip["slot"] for name, tip in tips.items()},
                        "consumer_matches": sustained_matches,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

            if fatal:
                result["reason"] = f"fatal Amaru signatures: {fatal}"
                break
            if len(genesis_starts) == len(CARDANO_PRODUCERS) and not genesis_start_consistent:
                result["reason"] = f"producer genesis start mismatch: {genesis_starts}"
                break
            if unhealthy:
                result["reason"] = f"Amaru relay stopped or restarted: {unhealthy}"
                break
            if launch.poll() is not None and launch.returncode != 0:
                result["reason"] = "docker compose up failed; see compose-up.log"
                break
            if all(relays_crossed.values()) and sustained_matches >= 3:
                result = {
                    "passed": True,
                    "reason": "all bootstrap, relay epoch, and consumer gates passed",
                    "project": project,
                    "initial_consumer_slot": initial_consumer_slot,
                    "final_consumer_tip": consumer,
                    "relay_evidence": relay_evidence,
                    "sustained_matching_samples": sustained_matches,
                    "scope": "benign mixed control; no adversarial security workload",
                }
                break
            time.sleep(args.sample_seconds)
    finally:
        if launch is not None and launch.poll() is None:
            launch.terminate()
            try:
                launch.wait(timeout=15)
            except subprocess.TimeoutExpired:
                launch.kill()
        try:
            up_log.close()
        except (NameError, UnboundLocalError):
            pass
        for name, container in containers.items():
            text = relay_log(container) if name in RELAYS else run(
                ["docker", "logs", container], timeout=30, check=False
            ).stdout
            (out / f"{name}.log").write_text(text, encoding="utf-8")
            state = inspect_state(container)
            if state is not None:
                (out / f"{name}-state.json").write_text(
                    json.dumps(state, indent=2) + "\n", encoding="utf-8"
                )
        all_logs = run(compose + ["logs", "--no-color"], timeout=120, check=False)
        (out / "containers.log").write_text(
            all_logs.stdout + all_logs.stderr, encoding="utf-8"
        )
        stopped = run(compose + ["stop"], timeout=180, check=False)
        (out / "stop.log").write_text(
            stopped.stdout + stopped.stderr, encoding="utf-8"
        )
        (out / "result.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )

    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
