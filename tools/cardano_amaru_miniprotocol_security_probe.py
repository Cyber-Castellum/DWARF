"""Fresh fail-closed DWARF proof for mixed mini-protocol security."""

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
SECURITY_SERVICES = (
    "sm-cardano-victim",
    "sm-amaru-victim",
    "sm-cardano-fuzzer",
    "sm-amaru-fuzzer",
    "sm-workload",
)
DRIVER_COMMANDS = (
    "parallel_driver_fuzz_observe.py",
    "anytime_containment.py",
    "eventually_recovery.py",
)
SEED = "0x20260907"


def load_observer(package: Path):
    path = package / "workload" / "miniprotocol_observer.py"
    module = types.ModuleType("mixed_miniprotocol_observer")
    source = path.read_text(encoding="utf-8")
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def container_text(container: str, path: str) -> str:
    completed = run(
        ["docker", "exec", container, "/bin/sh", "-c", f"cat {path} 2>/dev/null || true"],
        timeout=30,
        check=False,
    )
    return completed.stdout


def workload_observation(container: str) -> dict:
    code = (
        "import json; from miniprotocol_observer import observe_runtime; "
        "print(json.dumps(observe_runtime(), sort_keys=True))"
    )
    completed = run(
        ["docker", "exec", container, "python3", "-c", code],
        timeout=60,
        check=False,
    )
    if completed.returncode:
        return {"classifiable": False, "safe": None, "error": completed.stderr[-1000:]}
    try:
        return json.loads(next(line for line in reversed(completed.stdout.splitlines()) if line.strip()))
    except (StopIteration, json.JSONDecodeError):
        return {"classifiable": False, "safe": None, "error": "observer output was not JSON"}


def command_semantics_ok(command_name: str, stdout: str) -> bool:
    try:
        payload = json.loads(next(line for line in reversed(stdout.splitlines()) if line.strip()))
    except (StopIteration, json.JSONDecodeError):
        return False
    if command_name == "eventually_recovery.py":
        return payload.get("recovered") is True
    return payload.get("classifiable") is True and payload.get("safe") is True


def build_local_workload(package: Path, out: Path) -> None:
    completed = run(
        [
            "docker",
            "build",
            "-t",
            "dwarf-miniprotocol-workload:local",
            "-f",
            str(package / "workload" / "Dockerfile"),
            str(package),
        ],
        timeout=900,
        check=False,
    )
    (out / "workload-build.log").write_text(
        completed.stdout + completed.stderr, encoding="utf-8"
    )
    if completed.returncode:
        raise RuntimeError(f"workload image build failed with {completed.returncode}")


def render_compose(package: Path, out: Path, project: str) -> tuple[Path, dict]:
    sources = [package / "docker-compose.yaml", package / "docker-compose.local.yaml"]
    command = ["docker", "compose"]
    for source in sources:
        command.extend(["-f", str(source)])
    rendered = json.loads(
        run(
            command + ["config", "--format", "json"],
            env={**os.environ, "INTERNAL_NETWORK": "false"},
            timeout=90,
        ).stdout
    )
    rendered["name"] = project
    for name, service in rendered["services"].items():
        service["container_name"] = f"{project}-{name}"
        if name not in {"configurator", "amaru-consumer-seed", "sm-cardano-seed"}:
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


def image_identity(image: str) -> dict:
    completed = run(
        [
            "docker",
            "image",
            "inspect",
            image,
            "--format",
            "{{json .RepoDigests}}|{{.Id}}",
        ],
        timeout=30,
        check=False,
    )
    return {"declared": image, "inspect": completed.stdout.strip(), "returncode": completed.returncode}


def git_head(path: Path) -> str | None:
    completed = run(["git", "-C", str(path), "rev-parse", "HEAD"], timeout=20, check=False)
    return completed.stdout.strip() or None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--sample-seconds", type=int, default=10)
    args = parser.parse_args()

    package = args.package.resolve()
    observer = load_observer(package)
    run_root = Path(os.environ["ADA2_DWARF_RUN_DIR"])
    out = run_root / "outputs" / "cardano-amaru-miniprotocol-security"
    out.mkdir(parents=True, exist_ok=False)
    project = "dwarf-mixed-sm-" + time.strftime("%Y%m%d%H%M%S", time.gmtime())
    build_local_workload(package, out)
    compose_path, rendered = render_compose(package, out, project)
    containers = {name: f"{project}-{name}" for name in rendered["services"]}
    compose = ["docker", "compose", "-p", project, "-f", str(compose_path)]

    provenance = {
        "package": str(package),
        "project": project,
        "fresh_project": True,
        "compose_sha256": hashlib.sha256((package / "docker-compose.yaml").read_bytes()).hexdigest(),
        "seed": SEED,
        "source_revisions": {
            "dwarf_checkout": git_head(package.parents[1]),
            "cardano_node_antithesis": "fe039ac5582081297b38709a6862083cc2fb6c00",
            "amaru_audited_main": "835e32c62321571fc6571de87ca548eebfc0d43a",
            "amaru_runtime": "ea1f34e42c7a1806d8ee60b3f512e58daae7ccc1",
        },
        "resolved_images": {
            name: image_identity(spec["image"])
            for name, spec in rendered["services"].items()
        },
        "antithesis_submitted": False,
    }
    (out / "provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    result = {
        "passed": False,
        "reason": "deadline before every runtime security gate passed",
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
            up = run(compose + ["up", "-d"], timeout=bootstrap_timeout, check=False)
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
            stderr = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
            (out / "compose-up.log").write_text(stdout + stderr, encoding="utf-8")
            result["reason"] = f"compose bootstrap exceeded {bootstrap_timeout} seconds"
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
                for name in ("p1", "p2", "p3", "amaru-consumer", "sm-cardano-victim")
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
            observation = workload_observation(containers["sm-workload"])
            container_states = {
                name: inspect_state(containers[name]) for name in SECURITY_SERVICES
            }
            unhealthy = {
                name: state
                for name, state in container_states.items()
                if state is not None
                and (
                    not state["State"]["Running"]
                    or state.get("RestartCount", 0) != 0
                    or state["State"].get("OOMKilled", False)
                )
            }
            sample_counts = observation.get("sample_counts", {})
            sample = {
                "elapsed_seconds": elapsed,
                "relays_ready": relays_ready,
                "consumer_matching_samples": matching_samples,
                "tip_slots": {name: tip["slot"] for name, tip in tips.items()},
                "observation": observation,
                "sample_counts": sample_counts,
                "container_states": container_states,
            }
            with (out / "samples.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(sample, sort_keys=True) + "\n")
            print(
                json.dumps(
                    {
                        "elapsed": elapsed,
                        "relays_ready": relays_ready,
                        "consumer_matches": matching_samples,
                        "classifiable": observation.get("classifiable"),
                        "safe": observation.get("safe"),
                        "post_setup_counts": observation.get("inputs", {}).get("post_setup_counts", {}),
                        "unhealthy": sorted(unhealthy),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            if unhealthy:
                result["reason"] = f"security service stopped/restarted unexpectedly: {sorted(unhealthy)}"
                break
            inputs = observation.get("inputs", {})
            prefault = inputs.get("prefault", {})
            if (
                relays_ready
                and matching_samples >= 3
                and observation.get("classifiable") is True
                and observation.get("safe") is True
                and prefault.get("complete_coverage") is True
                and min(inputs.get("post_setup_counts", {"missing": 0}).values()) >= 24
                and inputs.get("control_progress") is True
                and inputs.get("unrelated_peers_usable") is True
                and not any(inputs.get("fatal", {"missing": True}).values())
                and all(inputs.get("recovered", {}).values())
                and inputs.get("converged") is True
            ):
                command_results = []
                for command_name in DRIVER_COMMANDS:
                    command_path = f"/opt/antithesis/test/v1/mixed-miniprotocol-security/{command_name}"
                    completed = run(
                        [
                            "docker",
                            "exec",
                            "-e",
                            "ANTITHESIS_SDK_LOCAL_OUTPUT=/tmp/sm-sdk-local.jsonl",
                            containers["sm-workload"],
                            command_path,
                        ],
                        timeout=240,
                        check=False,
                    )
                    command_results.append(
                        {
                            "command": command_name,
                            "returncode": completed.returncode,
                            "semantics_ok": command_semantics_ok(command_name, completed.stdout),
                            "stdout": completed.stdout[-12000:],
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
                        "reason": "paired 24-cell SP4 delivery, containment, progress, recovery, and convergence proved",
                        "project": project,
                        "final_sample": sample,
                    }
                else:
                    result["reason"] = "one or more local test-template commands failed semantically"
                break
            time.sleep(args.sample_seconds)
    finally:
        workload = containers.get("sm-workload")
        if workload:
            for output_name, container_path in (
                ("raw-cardano-fuzzer.log", "/cardano-evidence/fuzzer.log"),
                ("raw-amaru-fuzzer.log", "/amaru-evidence/fuzzer.log"),
                ("raw-amaru-victim.log", "/amaru-runtime-evidence/amaru.log"),
                ("raw-cardano-victim.log", "/cardano-runtime-evidence/cardano.log"),
                ("prefault.json", "/status/prefault.json"),
                ("prefault-failed.json", "/status/prefault-failed.json"),
            ):
                (out / output_name).write_text(
                    container_text(workload, container_path), encoding="utf-8"
                )
        final_states = {}
        for name, container in containers.items():
            final_states[name] = inspect_state(container)
            logs = run(["docker", "logs", "--tail", "1000", container], timeout=45, check=False)
            (out / f"{name}.log").write_text(logs.stdout + logs.stderr, encoding="utf-8")
        (out / "container-states.json").write_text(
            json.dumps(final_states, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        run(compose + ["stop", "-t", "20"], timeout=240, check=False)
        result["elapsed_seconds"] = round(time.monotonic() - started)
        (out / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
