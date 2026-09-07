"""Fresh fail-closed DWARF proof for the mixed hot-KES security package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import types

from cardano_amaru_relay_bootstrap_probe import (
    EPOCH_LENGTH,
    inspect_state,
    parse_relay_log,
    query_cardano_tip,
    relay_crossed_post_bootstrap_epoch,
    relay_log,
    run,
)


RELAYS = ("amaru-relay-1", "amaru-relay-2")
DRIVER_COMMANDS = (
    "parallel_driver_observe_kes.py",
    "anytime_kes_non_adoption.py",
    "eventually_kes_recovery.py",
)


def command_semantics_ok(command_name: str, stdout: str) -> bool:
    """Fail closed unless the command's final JSON line proves its property."""
    try:
        payload = json.loads(next(line for line in reversed(stdout.splitlines()) if line.strip()))
    except (StopIteration, json.JSONDecodeError):
        return False
    if command_name == "eventually_kes_recovery.py":
        return payload.get("recovered") is True
    evaluation = payload.get("evaluation")
    return (
        isinstance(evaluation, dict)
        and evaluation.get("classifiable") is True
        and evaluation.get("safe") is True
    )


def load_observer(package: Path):
    path = package / "workload" / "kes_observer.py"
    module = types.ModuleType("kes_security_observer")
    source = path.read_text(encoding="utf-8")
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def container_text(container: str, path: str) -> str:
    result = run(
        ["docker", "exec", container, "/bin/sh", "-c", f"cat {path} 2>/dev/null || true"],
        timeout=20,
        check=False,
    )
    return result.stdout


def count_event_kind(text: str, kind: str) -> int:
    count = 0
    for line in text.splitlines():
        try:
            if json.loads(line).get("kind") == kind:
                count += 1
        except json.JSONDecodeError:
            pass
    return count


def render_compose(package: Path, out: Path, project: str) -> tuple[Path, dict]:
    env = {
        **os.environ,
        "INTERNAL_NETWORK": "false",
        "DWARF_KES_IMAGE": "dwarf-kes-proxy:local",
        "DWARF_KES_WORKLOAD_IMAGE": "dwarf-kes-workload:local",
        "DWARF_KES_SEED": "20260906",
    }
    sources = [package / "docker-compose.yaml", package / "docker-compose.local.yaml"]
    command = ["docker", "compose"]
    for source in sources:
        command.extend(["-f", str(source)])
    rendered = json.loads(
        run(command + ["config", "--format", "json"], env=env, timeout=60).stdout
    )
    rendered["name"] = project
    for name, service in rendered["services"].items():
        service["container_name"] = f"{project}-{name}"
        if name not in {"configurator", "amaru-consumer-seed", "kes-cardano-seed"}:
            service["restart"] = "no"
    for name, network in rendered.get("networks", {}).items():
        network["name"] = f"{project}-{name}"
    for name, volume in rendered.get("volumes", {}).items():
        if volume.get("external"):
            raise RuntimeError(f"external volume forbidden in fresh proof: {name}")
        volume["name"] = f"{project}-{name}"
    compose_path = out / "compose.json"
    compose_path.write_text(json.dumps(rendered, indent=2) + "\n", encoding="utf-8")
    run(["docker", "compose", "-p", project, "-f", str(compose_path), "config", "--quiet"])
    return compose_path, rendered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--sample-seconds", type=int, default=10)
    args = parser.parse_args()

    package = args.package.resolve()
    observer = load_observer(package)
    run_root = Path(os.environ["ADA2_DWARF_RUN_DIR"])
    out = run_root / "outputs" / "cardano-amaru-kes-security"
    out.mkdir(parents=True, exist_ok=False)
    project = "dwarf-kes-security-" + time.strftime("%Y%m%d%H%M%S", time.gmtime())
    compose_path, rendered = render_compose(package, out, project)
    containers = {name: f"{project}-{name}" for name in rendered["services"]}
    compose = ["docker", "compose", "-p", project, "-f", str(compose_path)]

    provenance = {
        "package": str(package),
        "compose_sha256": hashlib.sha256((package / "docker-compose.yaml").read_bytes()).hexdigest(),
        "project": project,
        "fresh_project": True,
        "images": {name: spec["image"] for name, spec in rendered["services"].items()},
        "antithesis_submitted": False,
    }
    (out / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )

    result = {
        "passed": False,
        "reason": "deadline before all mixed security gates passed",
        "project": project,
    }
    started = time.monotonic()
    initial_consumer_slot = None
    matching_samples = 0
    print(f"security_project={project}", flush=True)
    print(f"evidence={out}", flush=True)

    try:
        bootstrap_timeout = min(args.timeout_seconds, 1800)
        try:
            up = run(
                compose + ["up", "-d"], timeout=bootstrap_timeout, check=False
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            (out / "compose-up.log").write_text(
                stdout + stderr, encoding="utf-8"
            )
            result["reason"] = (
                f"compose bootstrap exceeded {bootstrap_timeout} seconds"
            )
            return 1
        (out / "compose-up.log").write_text(up.stdout + up.stderr, encoding="utf-8")
        if up.returncode:
            result["reason"] = f"compose up failed with exit {up.returncode}"
            return 1

        while time.monotonic() - started < args.timeout_seconds:
            elapsed = round(time.monotonic() - started)
            relay_evidence = {
                name: parse_relay_log(relay_log(containers[name])) for name in RELAYS
            }
            relays_ready = all(
                relay_crossed_post_bootstrap_epoch(value, epoch_length=EPOCH_LENGTH)
                and not value["fatal_signatures"]
                for value in relay_evidence.values()
            )
            tips = {
                name: tip
                for name in ("p1", "p2", "p3", "amaru-consumer")
                if (tip := query_cardano_tip(containers[name])) is not None
            }
            consumer = tips.get("amaru-consumer")
            if consumer is not None and initial_consumer_slot is None:
                initial_consumer_slot = consumer["slot"]
            producer_hashes = {
                tips[name]["hash"] for name in ("p1", "p2", "p3") if name in tips
            }
            consumer_matches = bool(
                consumer
                and initial_consumer_slot is not None
                and consumer["slot"] > initial_consumer_slot
                and consumer["hash"] in producer_hashes
            )
            matching_samples = matching_samples + 1 if consumer_matches else 0

            cardano_events_text = container_text(
                containers["kes-workload"], "/cardano-proxy/events.jsonl"
            )
            amaru_events_text = container_text(
                containers["kes-workload"], "/amaru-proxy/events.jsonl"
            )
            amaru_log_text = container_text(
                containers["kes-workload"], "/amaru-evidence/amaru.log"
            )
            paired = observer.latest_paired_mutation(
                observer.parse_proxy_events(cardano_events_text),
                observer.parse_proxy_events(amaru_events_text),
            )
            cardano_tip = query_cardano_tip(containers["kes-cardano-victim"])
            cardano_observation = (
                {"status": "ok", **cardano_tip}
                if cardano_tip is not None
                else {"status": "unavailable"}
            )
            amaru_observation = observer.parse_amaru_log(amaru_log_text)
            security = observer.evaluate_observation(
                mutation=paired,
                cardano_tip=cardano_observation,
                amaru=amaru_observation,
            )
            honest = {
                "cardano": count_event_kind(cardano_events_text, "honest_header"),
                "amaru": count_event_kind(amaru_events_text, "honest_header"),
            }
            states = {
                name: inspect_state(containers[name])
                for name in (
                    "kes-cardano-proxy",
                    "kes-amaru-proxy",
                    "kes-cardano-victim",
                    "kes-amaru-victim",
                    "sidecar",
                )
            }
            unhealthy = {
                name: state
                for name, state in states.items()
                if state is not None and not state["State"]["Running"]
            }
            sample = {
                "elapsed_seconds": elapsed,
                "relays_ready": relays_ready,
                "tip_slots": {name: tip["slot"] for name, tip in tips.items()},
                "consumer_matching_samples": matching_samples,
                "honest_headers": honest,
                "paired_mutation": paired,
                "security": security,
                "states": states,
            }
            with (out / "samples.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(sample, sort_keys=True) + "\n")
            print(
                json.dumps(
                    {
                        "elapsed": elapsed,
                        "relays_ready": relays_ready,
                        "consumer_matches": matching_samples,
                        "honest": honest,
                        "paired": paired is not None,
                        "classifiable": security["classifiable"],
                        "safe": security["safe"],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

            if unhealthy:
                result["reason"] = f"security service stopped unexpectedly: {sorted(unhealthy)}"
                break
            if (
                relays_ready
                and matching_samples >= 3
                and min(honest.values()) >= 1
                and security["classifiable"]
                and security["safe"]
            ):
                command_results = []
                for command_name in DRIVER_COMMANDS:
                    command_path = f"/opt/antithesis/test/v1/mixed-kes-security/{command_name}"
                    completed = run(
                        [
                            "docker",
                            "exec",
                            "-e",
                            "ANTITHESIS_SDK_LOCAL_OUTPUT=/tmp/kes-sdk-local.jsonl",
                            containers["kes-workload"],
                            command_path,
                        ],
                        timeout=300,
                        check=False,
                    )
                    command_results.append(
                        {
                            "command": command_name,
                            "returncode": completed.returncode,
                            "semantics_ok": command_semantics_ok(
                                command_name, completed.stdout
                            ),
                            "stdout": completed.stdout[-8000:],
                            "stderr": completed.stderr[-4000:],
                        }
                    )
                    if completed.returncode:
                        break
                (out / "test-commands.json").write_text(
                    json.dumps(command_results, indent=2) + "\n", encoding="utf-8"
                )
                if len(command_results) == len(DRIVER_COMMANDS) and all(
                    item["returncode"] == 0 and item["semantics_ok"]
                    for item in command_results
                ):
                    result = {
                        "passed": True,
                        "reason": "baseline convergence, live paired KES rejection, and all test commands passed",
                        "project": project,
                        "final_sample": sample,
                    }
                else:
                    result["reason"] = "one or more local test-template commands failed"
                break
            time.sleep(args.sample_seconds)
    finally:
        raw_evidence = (
            (
                "raw-cardano-proxy-events.jsonl",
                "/cardano-proxy/events.jsonl",
            ),
            (
                "raw-amaru-proxy-events.jsonl",
                "/amaru-proxy/events.jsonl",
            ),
            ("raw-amaru-victim.log", "/amaru-evidence/amaru.log"),
        )
        workload = containers.get("kes-workload")
        if workload:
            for output_name, container_path in raw_evidence:
                (out / output_name).write_text(
                    container_text(workload, container_path), encoding="utf-8"
                )
        for name, container in containers.items():
            logs = run(["docker", "logs", "--tail", "500", container], timeout=30, check=False)
            (out / f"{name}.log").write_text(logs.stdout + logs.stderr, encoding="utf-8")
        run(compose + ["stop", "-t", "20"], timeout=180, check=False)
        result["elapsed_seconds"] = round(time.monotonic() - started)
        (out / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(result, sort_keys=True), flush=True)

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
