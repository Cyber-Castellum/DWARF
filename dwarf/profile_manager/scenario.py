"""Scenario loader + minimal v1 schema validator.

Scenarios are YAML files that, for v1, must hold JSON-structured content (no
YAML-only syntax), per spec/v1/README. This keeps the framework zero-dependency
without precluding a full YAML parser later.

The validator enforces the shape defined by spec/v1/schema.json. It is
hand-written to avoid a jsonschema dependency; it covers the v1 surface.
"""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from profile_manager import primitives as primitives_module


class ScenarioValidationError(ValueError):
    """Raised when a scenario fails schema validation."""


VALID_RUNTIMES = ("library", "single-node", "devnet")
VALID_IMPLEMENTATIONS = ("cardano-node", "amaru")
VALID_EVIDENCE_INTENTS = ("candidate", "regression", "finding-validation", "risk-support")
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
HOST_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
SUBSTRATE_NETWORK_PATTERN = re.compile(r"^(mainnet|preprod|preview|testnet_[1-9][0-9]*)$")
VALID_SUBSTRATE_COMPOSE_MODES = ("host", "docker")
DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "primitives" / "registry.json"
COMPARE_DIFF_METRICS = (
    "preview_chain_bytes_delta",
    "preview_log_bytes_delta",
    "preview_progress_ok",
    "preview_fault_window_chain_bytes_delta",
    "preview_fault_window_log_bytes_delta",
    "preview_fault_window_progress_ok",
    "preview_postfault_window_chain_bytes_delta",
    "preview_postfault_window_log_bytes_delta",
    "preview_postfault_window_progress_ok",
    "preview_adopted_tip_count",
    "preview_tip_slot_delta",
    "preview_peer_connected_count",
    "preview_peer_connection_died_count",
    "preview_amaru_adopted_tip_count",
    "preview_amaru_tip_slot_delta",
    "preview_amaru_peer_connected_count",
    "preview_amaru_peer_connection_died_count",
    "preview_fault_window_adopted_tip_count",
    "preview_fault_window_tip_slot_delta",
    "preview_fault_window_peer_connected_count",
    "preview_fault_window_peer_connection_died_count",
    "preview_fault_window_amaru_adopted_tip_count",
    "preview_fault_window_amaru_tip_slot_delta",
    "preview_fault_window_amaru_peer_connected_count",
    "preview_fault_window_amaru_peer_connection_died_count",
    "preview_postfault_window_adopted_tip_count",
    "preview_postfault_window_tip_slot_delta",
    "preview_postfault_window_peer_connected_count",
    "preview_postfault_window_peer_connection_died_count",
    "preview_postfault_window_amaru_adopted_tip_count",
    "preview_postfault_window_amaru_tip_slot_delta",
    "preview_postfault_window_amaru_peer_connected_count",
    "preview_postfault_window_amaru_peer_connection_died_count",
)
AMARU_ONLY_COMPARE_METRICS = {
    "preview_amaru_adopted_tip_count",
    "preview_amaru_tip_slot_delta",
    "preview_amaru_peer_connected_count",
    "preview_amaru_peer_connection_died_count",
    "preview_fault_window_amaru_adopted_tip_count",
    "preview_fault_window_amaru_tip_slot_delta",
    "preview_fault_window_amaru_peer_connected_count",
    "preview_fault_window_amaru_peer_connection_died_count",
    "preview_postfault_window_amaru_adopted_tip_count",
    "preview_postfault_window_amaru_tip_slot_delta",
    "preview_postfault_window_amaru_peer_connected_count",
    "preview_postfault_window_amaru_peer_connection_died_count",
}
TOPOLOGY_NORMALIZED_COMPARE_METRICS = {
    "preview_fault_window_chain_bytes_delta",
    "preview_postfault_window_chain_bytes_delta",
    "preview_fault_window_log_bytes_delta",
    "preview_postfault_window_log_bytes_delta",
    "preview_fault_window_peer_connection_died_count",
    "preview_postfault_window_peer_connection_died_count",
}

SECTION_FAMILY_MAP = {
    "setup": "setup",
    "load": "load",
    "faults": "fault",
    "probes": "probe",
    "assertions": "assertion",
    "teardown": "teardown",
}

ASSERTION_PRODUCER_MAP = {
    "all_nodes_responsive": {"runtime_multi_node_observation"},
    "peer_connectivity_observed": {"runtime_multi_node_observation"},
    "tip_convergence_clean": {"runtime_multi_node_observation"},
    "chain_select_consistent": {"runtime_multi_node_observation"},
    "substrate_quorum_observed": {"runtime_multi_node_observation"},
    "mode_switch_genesis_observed": {"runtime_multi_node_observation"},
    "era_summary_recorded_clean": {"runtime_cardano_lsq_extract", "runtime_multi_node_observation"},
    "txsubmission_window_enforced": {"runtime_txsubmission_window_pressure"},
    "txsubmission_batch_enforced": {"runtime_txsubmission_batch_pressure"},
    "txsubmission_unexpected_body_rejected": {"runtime_txsubmission_unexpected_body"},
    "mempool_failure_contained": {"runtime_mempool_failure_probe"},
    "chainsync_parent_discontinuity_rejected": {"runtime_chainsync_parent_discontinuity"},
    "chainsync_height_monotonicity_enforced": {"runtime_chainsync_nonincrementing_height"},
    "chainsync_slot_monotonicity_enforced": {"runtime_chainsync_nonmonotonic_slot"},
    "blockfetch_invalid_range_rejected": {"runtime_blockfetch_invalid_range"},
    "blockfetch_range_pressure_bounded": {"runtime_blockfetch_range_pressure"},
    "blockfetch_invalid_block_rejected": {"runtime_blockfetch_invalid_block_cbor"},
    "blockfetch_response_range_strict": {"runtime_blockfetch_range_mismatch"},
    "blockfetch_continuity_failure_rejected": {"runtime_blockfetch_continuity_failure"},
    "chainsync_responder_rollback_then_forward_clean": {"runtime_chainsync_responder_fork_switch"},
    "execution_trace_amaru_cardano_node_equivalent": {"runtime_execution_trace_differential"},
    "credential_ceremony_recorded_clean": {"runtime_credential_ceremony"},
    "amaru_proptest_oracle_recorded_clean": {"runtime_amaru_proptest_oracle"},
}

NODE_ID_VALUE_KEYS = {
    "node_id",
    "target_node",
    "target_node_id",
    "observe_node",
    "observe_node_id",
    "byzantine_node",
    "byzantine_node_id",
    "restart_node",
    "restart_node_id",
    "kill_node",
    "kill_node_id",
}

NODE_ID_LIST_KEYS = {
    "node_ids",
    "healthy_nodes",
    "honest_nodes",
    "byzantine_nodes",
    "observed_nodes",
    "peer_nodes",
}

LEGACY_SEMANTIC_WARNING_PRIMITIVES = {
    "runtime_compose_substrate",
    "shim_peer_malformed_blockfetch",
}


@dataclass(frozen=True)
class PrimitiveRef:
    primitive: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScenarioPhase:
    id: str
    title: Optional[str]
    setup: List[PrimitiveRef]
    load: List[PrimitiveRef]
    faults: List[PrimitiveRef]
    probes: List[PrimitiveRef]
    assertions: List[PrimitiveRef]
    teardown: List[PrimitiveRef]


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    target: Dict[str, str]
    runtime: str
    profile: Optional[str]
    substrate: Optional[Dict[str, Any]]
    seed: Any
    iterations: Optional[int]
    shrink: bool
    related_milestones: List[str]
    m1_trace: Dict[str, List[str]]
    evidence_intent: Optional[str]
    promotion_blockers: List[str]
    testcase_candidate: Optional[Dict[str, str]]
    setup: List[PrimitiveRef]
    load: List[PrimitiveRef]
    faults: List[PrimitiveRef]
    probes: List[PrimitiveRef]
    assertions: List[PrimitiveRef]
    teardown: List[PrimitiveRef]
    phases: List[ScenarioPhase]
    path: Path
    raw_bytes: bytes
    schedule: Optional[str] = None


def _require(obj, key, context):
    if key not in obj:
        raise ScenarioValidationError(f"missing required field: {context}.{key}")
    return obj[key]


def _primitive_refs(obj, key):
    items = obj.get(key, []) or []
    if not isinstance(items, list):
        raise ScenarioValidationError(f"{key} must be a list")
    refs = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ScenarioValidationError(f"{key}[{i}] must be a mapping")
        name = item.get("primitive")
        if not isinstance(name, str) or not re.match(r"^[a-z][a-z0-9_]*$", name):
            raise ScenarioValidationError(f"{key}[{i}].primitive must be a lowercase-snake-case string")
        params = {k: v for k, v in item.items() if k != "primitive"}
        refs.append(PrimitiveRef(primitive=name, params=params))
    return refs


def _phase_refs(body):
    phases = body.get("phases", []) or []
    if not isinstance(phases, list):
        raise ScenarioValidationError("phases must be a list")
    refs = []
    for i, item in enumerate(phases):
        if not isinstance(item, dict):
            raise ScenarioValidationError(f"phases[{i}] must be a mapping")
        phase_id = item.get("id")
        if not isinstance(phase_id, str) or not ID_PATTERN.match(phase_id):
            raise ScenarioValidationError(f"phases[{i}].id must match {ID_PATTERN.pattern}, got {phase_id!r}")
        title = item.get("title")
        if title is not None and (not isinstance(title, str) or not title.strip()):
            raise ScenarioValidationError(f"phases[{i}].title must be a non-empty string when present")
        refs.append(
            ScenarioPhase(
                id=phase_id,
                title=title,
                setup=_primitive_refs(item, "setup"),
                load=_primitive_refs(item, "load"),
                faults=_primitive_refs(item, "faults"),
                probes=_primitive_refs(item, "probes"),
                assertions=_primitive_refs(item, "assertions"),
                teardown=_primitive_refs(item, "teardown"),
            )
        )
    return refs


def _validate_top(body):
    if body.get("spec_version") != "v1":
        raise ScenarioValidationError(f"spec_version must be 'v1', got {body.get('spec_version')!r}")
    scenario_id = _require(body, "id", "scenario")
    if not isinstance(scenario_id, str) or not ID_PATTERN.match(scenario_id):
        raise ScenarioValidationError(f"id must match {ID_PATTERN.pattern}, got {scenario_id!r}")
    title = _require(body, "title", "scenario")
    if not isinstance(title, str) or not title.strip():
        raise ScenarioValidationError("title must be a non-empty string")
    target = _require(body, "target", "scenario")
    if not isinstance(target, dict):
        raise ScenarioValidationError("target must be a mapping")
    impl = _require(target, "implementation", "target")
    if impl not in VALID_IMPLEMENTATIONS:
        raise ScenarioValidationError(f"target.implementation must be one of {VALID_IMPLEMENTATIONS}, got {impl!r}")
    version = _require(target, "version", "target")
    if not isinstance(version, str) or not version:
        raise ScenarioValidationError("target.version must be a non-empty string")
    runtime = _require(body, "runtime", "scenario")
    if runtime not in VALID_RUNTIMES:
        raise ScenarioValidationError(f"runtime must be one of {VALID_RUNTIMES}, got {runtime!r}")
    profile = body.get("profile")
    substrate = body.get("substrate")
    if runtime == "devnet":
        if profile is not None and substrate is not None:
            raise ScenarioValidationError("runtime=devnet requires either profile or substrate, not both")
        if substrate is not None:
            substrate = _validate_substrate(substrate)
            profile = None
        elif isinstance(profile, str) and profile:
            substrate = None
        else:
            raise ScenarioValidationError("runtime=devnet requires a non-empty profile id or a substrate block")
    else:
        if profile is not None:
            raise ScenarioValidationError(f"profile must be null when runtime={runtime}; got {profile!r}")
        if substrate is not None:
            raise ScenarioValidationError(f"substrate must be null when runtime={runtime}; got {substrate!r}")
        substrate = None
    iterations = body.get("iterations")
    if iterations is not None and (not isinstance(iterations, int) or iterations < 1):
        raise ScenarioValidationError("iterations must be a positive integer when present")
    shrink = body.get("shrink", True)
    if not isinstance(shrink, bool):
        raise ScenarioValidationError("shrink must be a boolean")
    return scenario_id, title, target, runtime, profile, substrate, iterations, shrink


def _validate_substrate(value):
    if not isinstance(value, dict):
        raise ScenarioValidationError("substrate must be a mapping")
    host_strategy = value.get("host_strategy", "single-host")
    if host_strategy not in {"single-host", "explicit"}:
        raise ScenarioValidationError("substrate.host_strategy must be 'single-host' or 'explicit'")
    host_values = value.get("hosts") or []
    normalized_hosts = []
    host_ids = set()
    if host_strategy == "explicit":
        if not isinstance(host_values, list) or not host_values:
            raise ScenarioValidationError("substrate.hosts must be a non-empty list when host_strategy='explicit'")
        for index, host in enumerate(host_values):
            if not isinstance(host, dict):
                raise ScenarioValidationError(f"substrate.hosts[{index}] must be a mapping")
            host_id = host.get("id")
            ssh_target = host.get("ssh_target")
            remote_runtime_base = host.get("remote_runtime_base")
            published_host = host.get("published_host")
            if not isinstance(host_id, str) or not HOST_ID_PATTERN.match(host_id):
                raise ScenarioValidationError(f"substrate.hosts[{index}].id must match {HOST_ID_PATTERN.pattern}")
            if host_id in host_ids:
                raise ScenarioValidationError(f"substrate.hosts contains duplicate id {host_id!r}")
            host_ids.add(host_id)
            if not isinstance(ssh_target, str) or not ssh_target:
                raise ScenarioValidationError(f"substrate.hosts[{index}].ssh_target must be a non-empty string")
            if not isinstance(remote_runtime_base, str) or not remote_runtime_base:
                raise ScenarioValidationError(f"substrate.hosts[{index}].remote_runtime_base must be a non-empty string")
            if not isinstance(published_host, str) or not published_host:
                raise ScenarioValidationError(f"substrate.hosts[{index}].published_host must be a non-empty string")
            ssh_key_path = host.get("ssh_key_path")
            if ssh_key_path is not None and (not isinstance(ssh_key_path, str) or not ssh_key_path):
                raise ScenarioValidationError(f"substrate.hosts[{index}].ssh_key_path must be a non-empty string when present")
            normalized_hosts.append(
                {
                    "id": host_id,
                    "ssh_target": ssh_target,
                    "ssh_key_path": ssh_key_path,
                    "remote_runtime_base": remote_runtime_base,
                    "published_host": published_host,
                }
            )
    elif host_values:
        raise ScenarioValidationError("substrate.hosts requires substrate.host_strategy='explicit'")
    nodes = value.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ScenarioValidationError("substrate.nodes must be a non-empty list")
    seen_ids = set()
    normalized_nodes = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ScenarioValidationError(f"substrate.nodes[{index}] must be a mapping")
        node_id = node.get("id")
        impl = node.get("impl")
        version = node.get("version")
        role = node.get("role")
        if not isinstance(node_id, str) or not ID_PATTERN.match(node_id):
            raise ScenarioValidationError(f"substrate.nodes[{index}].id must match {ID_PATTERN.pattern}")
        if node_id in seen_ids:
            raise ScenarioValidationError(f"substrate.nodes contains duplicate id {node_id!r}")
        seen_ids.add(node_id)
        if impl not in VALID_IMPLEMENTATIONS:
            raise ScenarioValidationError(
                f"substrate.nodes[{index}].impl must be one of {VALID_IMPLEMENTATIONS}, got {impl!r}"
            )
        if not isinstance(version, str) or not version:
            raise ScenarioValidationError(f"substrate.nodes[{index}].version must be a non-empty string")
        if not isinstance(role, str) or not role:
            raise ScenarioValidationError(f"substrate.nodes[{index}].role must be a non-empty string")
        node_host = node.get("host")
        if host_strategy == "explicit":
            if node_host not in host_ids:
                raise ScenarioValidationError(f"substrate.nodes[{index}].host must reference a declared substrate.hosts id")
        elif node_host is not None:
            raise ScenarioValidationError("substrate.nodes[*].host requires substrate.host_strategy='explicit'")
        normalized_nodes.append({"id": node_id, "impl": impl, "version": version, "role": role, "host": node_host})
    topology = value.get("topology") or {}
    if not isinstance(topology, dict):
        raise ScenarioValidationError("substrate.topology must be a mapping")
    edges = topology.get("edges", [])
    if not isinstance(edges, list):
        raise ScenarioValidationError("substrate.topology.edges must be a list")
    normalized_edges = []
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise ScenarioValidationError(f"substrate.topology.edges[{index}] must be a mapping")
        from_id = edge.get("from")
        to_id = edge.get("to")
        if from_id not in seen_ids or to_id not in seen_ids:
            raise ScenarioValidationError(f"substrate.topology.edges[{index}] references unknown node ids")
        if from_id == to_id:
            raise ScenarioValidationError(f"substrate.topology.edges[{index}] must not self-reference")
        normalized_edges.append({"from": from_id, "to": to_id})
    network = value.get("network")
    if network is None:
        network_magic = value.get("network_magic", 42)
        if not isinstance(network_magic, int) or network_magic < 1:
            raise ScenarioValidationError("substrate.network_magic must be a positive integer when present")
        network = f"testnet_{network_magic}"
    else:
        if not isinstance(network, str) or not SUBSTRATE_NETWORK_PATTERN.match(network):
            raise ScenarioValidationError(
                "substrate.network must be one of mainnet, preprod, preview, or testnet_<positive-int>"
            )
        network_magic = value.get("network_magic")
        if network_magic is not None:
            if not isinstance(network_magic, int) or network_magic < 1:
                raise ScenarioValidationError("substrate.network_magic must be a positive integer when present")
            if network.startswith("testnet_") and network != f"testnet_{network_magic}":
                raise ScenarioValidationError(
                    "substrate.network and substrate.network_magic must describe the same testnet"
                )
            if not network.startswith("testnet_"):
                raise ScenarioValidationError(
                    "substrate.network_magic must be omitted when substrate.network is mainnet, preprod, or preview"
                )
        elif network.startswith("testnet_"):
            network_magic = int(network.split("_", 1)[1])
    compose_mode = value.get("compose_mode", "host")
    if compose_mode not in VALID_SUBSTRATE_COMPOSE_MODES:
        raise ScenarioValidationError(
            f"substrate.compose_mode must be one of {VALID_SUBSTRATE_COMPOSE_MODES}, got {compose_mode!r}"
        )
    return {
        "compose_mode": compose_mode,
        "host_strategy": host_strategy,
        "hosts": normalized_hosts,
        "network": network,
        "network_magic": network_magic,
        "nodes": normalized_nodes,
        "topology": {"edges": normalized_edges},
    }


def _optional_string_list(body, key):
    value = body.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ScenarioValidationError(f"{key} must be a list of non-empty strings")
    return list(value)


def _validate_m1_trace(body):
    value = body.get("m1_trace", {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ScenarioValidationError("m1_trace must be a mapping")
    trace = {}
    for key, items in value.items():
        if not isinstance(key, str) or not key:
            raise ScenarioValidationError("m1_trace keys must be non-empty strings")
        if not isinstance(items, list) or not all(isinstance(item, str) and item for item in items):
            raise ScenarioValidationError(f"m1_trace.{key} must be a list of non-empty strings")
        trace[key] = list(items)
    return trace


def _validate_evidence_intent(body):
    value = body.get("evidence_intent")
    if value is None:
        return None
    if value not in VALID_EVIDENCE_INTENTS:
        raise ScenarioValidationError(
            f"evidence_intent must be one of {VALID_EVIDENCE_INTENTS}, got {value!r}"
        )
    return value


def _validate_schedule(body):
    value = body.get("schedule")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ScenarioValidationError("schedule must be a non-empty string when present")
    value = value.strip()
    if value in {"hourly", "daily"}:
        return value
    if value.startswith("cron ") and value[5:].strip():
        return value
    raise ScenarioValidationError("schedule must be one of 'hourly', 'daily', or 'cron <expr>'")


def _validate_testcase_candidate(body):
    value = body.get("testcase_candidate")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ScenarioValidationError("testcase_candidate must be a mapping")
    classification = value.get("classification")
    triage_reason = value.get("triage_reason")
    if not isinstance(classification, str) or not classification:
        raise ScenarioValidationError("testcase_candidate.classification must be a non-empty string")
    if not isinstance(triage_reason, str) or not triage_reason:
        raise ScenarioValidationError("testcase_candidate.triage_reason must be a non-empty string")
    producer = value.get("producer", "scenario")
    if not isinstance(producer, str) or not producer:
        raise ScenarioValidationError("testcase_candidate.producer must be a non-empty string when present")
    source_artifact_path = value.get("source_artifact_path", "manifest.json")
    if not isinstance(source_artifact_path, str) or not source_artifact_path:
        raise ScenarioValidationError("testcase_candidate.source_artifact_path must be a non-empty string when present")
    return {
        "classification": classification,
        "triage_reason": triage_reason,
        "producer": producer,
        "source_artifact_path": source_artifact_path,
    }


DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "primitives" / "registry.json"
TARGET_MANIFEST_PRIMITIVES = ("cbor_fuzz_target", "mini_protocol_sequence_target")


def run_scenario(path, *, runs_dir, state_dir, registry_path=None,
                 framework_version="0.1.0", framework_commit="unknown", actor="shared:dwarf"):
    """Execute a scenario end-to-end, producing a forensic bundle.

    v1 runs setup → load → per-iteration probe fan-out → assertions → teardown.
    Faults and continuous probes are deferred to later slices.
    """
    import random
    from profile_manager import forensic, primitives, telemetry, testcase_lifecycle

    scen = load_scenario(path)
    registry = primitives.load_registry(registry_path or DEFAULT_REGISTRY_PATH)
    seed = scen.seed if scen.seed is not None else 0
    # Derive an int seed for random.Random.
    if isinstance(seed, str) and seed.lower().startswith("0x"):
        rng_seed = int(seed, 16)
    else:
        rng_seed = int(seed)
    rng = random.Random(rng_seed)

    profile_resolved = {"id": scen.profile} if scen.runtime == "devnet" and scen.profile else None

    handle = forensic.start_run(
        scenario_id=scen.id,
        scenario_yaml=scen.raw_bytes,
        target=dict(scen.target),
        runtime=scen.runtime,
        profile_id=scen.profile,
        profile_resolved=profile_resolved,
        framework_version=framework_version,
        framework_commit=framework_commit,
        seed=seed,
        actor=actor,
        runs_dir=runs_dir,
        state_dir=state_dir,
    )
    handle.set_start_resource_snapshot(forensic.capture_local_resource_snapshot(pid=os.getpid(), data_dir=handle.run_dir))
    observer = telemetry.ObserverCollector(metrics_dir=handle.run_dir / "metrics", pid=os.getpid())
    observer.start()

    overall = "pass"
    shared_state = {}

    active_faults = []  # list of instances in apply order, for LIFO removal
    phases = (
        scen.phases
        if scen.phases
        else [
            ScenarioPhase(
                id="default",
                title=scen.title,
                setup=scen.setup,
                load=scen.load,
                faults=scen.faults,
                probes=scen.probes,
                assertions=scen.assertions,
                teardown=scen.teardown,
            )
        ]
    )

    def _phase_step(phase_id, step):
        return f"phase:{phase_id}:{step}"

    def _run_faults_for_phase(phase_obj):
        from profile_manager import primitives as _primitives_module
        applied = []
        for ref in phase_obj.faults:
            prim = _primitives_module.instantiate(
                registry, name=ref.primitive, params=ref.params,
                runtime=scen.runtime, target_implementation=scen.target["implementation"],
            )
            if hasattr(prim, "apply"):
                try:
                    if shared_state is not None and hasattr(prim, "state"):
                        prim.state = shared_state
                    if shared_state is not None and hasattr(prim, "bound_state"):
                        prim.bound_state = shared_state
                    if hasattr(prim, "bound_substrate"):
                        prim.bound_substrate = scen.substrate
                    if hasattr(prim, "bound_schedule"):
                        prim.bound_schedule = scen.schedule
                    prim.apply(handle)
                    applied.append(prim)
                except Exception as exc:
                    handle.log(
                        phase=_phase_step(phase_obj.id, "fault"),
                        primitive=ref.primitive,
                        level="error",
                        event="apply_error",
                        payload={"message": str(exc), "type": type(exc).__name__, "phase_id": phase_obj.id},
                    )
                    raise
        return applied

    def _remove_faults_for_phase(phase_obj, faults):
        for prim in reversed(faults):
            try:
                prim.remove(handle)
            except Exception as exc:
                handle.log(
                    phase=_phase_step(phase_obj.id, "fault"),
                    primitive=type(prim).__name__,
                    level="warn",
                    event="remove_error",
                    payload={"message": str(exc), "type": type(exc).__name__, "phase_id": phase_obj.id},
                )

    def _run_phase_assertions(phase_obj, outcomes):
        nonlocal overall
        for ref in phase_obj.assertions:
            prim = primitives.instantiate(
                registry, name=ref.primitive, params=ref.params,
                runtime=scen.runtime, target_implementation=scen.target["implementation"],
            )
            if hasattr(prim, "evaluate_outcomes"):
                result = prim.evaluate_outcomes(outcomes)
            else:
                result = prim.evaluate(handle)
            handle.assertion_result(**result)
            if result.get("result") == "fail":
                overall = "fail"

    try:
        for index, phase_obj in enumerate(phases, start=1):
            handle.log(
                phase="framework",
                primitive="framework",
                level="info",
                event="phase_started",
                payload={"phase_id": phase_obj.id, "phase_index": index, "phase_title": phase_obj.title},
            )
            phase_ok = False
            try:
                _run_phase(
                    handle, rng, registry, scen, phase_obj.setup, _phase_step(phase_obj.id, "setup"),
                    shared_state=shared_state,
                )
                active_faults = _run_faults_for_phase(phase_obj)
                _run_phase(
                    handle, rng, registry, scen, phase_obj.load, _phase_step(phase_obj.id, "load"),
                    shared_state=shared_state,
                )
                _remove_faults_for_phase(phase_obj, active_faults)
                active_faults = []

                outcomes = _extract_outcomes(handle)

                for ref in phase_obj.probes:
                    prim = primitives.instantiate(
                        registry, name=ref.primitive, params=ref.params,
                        runtime=scen.runtime, target_implementation=scen.target["implementation"],
                    )
                    if hasattr(prim, "sample_for_input"):
                        for outcome in outcomes:
                            try:
                                prim.sample_for_input(handle, input_id=outcome.get("i"), outcome=outcome)
                            except NotImplementedError:
                                break

                _run_phase_assertions(phase_obj, outcomes)
                phase_ok = True
            finally:
                if active_faults:
                    _remove_faults_for_phase(phase_obj, active_faults)
                    active_faults = []
                _run_phase(
                    handle, rng, registry, scen, phase_obj.teardown, _phase_step(phase_obj.id, "teardown"),
                    ignore_errors=True, shared_state=shared_state,
                )
                if phase_ok:
                    handle.log(
                        phase="framework",
                        primitive="framework",
                        level="info",
                        event="phase_completed",
                        payload={"phase_id": phase_obj.id, "phase_index": index, "phase_title": phase_obj.title},
                    )

    except Exception as exc:
        overall = "error"
        handle.log(phase="framework", primitive="framework", level="error",
                   event="runner_exception", payload={"message": str(exc), "type": type(exc).__name__})
        # Remove any faults that were applied before the exception.
        for prim in reversed(active_faults):
            try:
                prim.remove(handle)
            except Exception:
                pass
        try:
            _run_phase(handle, rng, registry, scen, scen.teardown, "teardown", ignore_errors=True, shared_state=shared_state)
        except Exception:
            pass
    finally:
        observer.stop()
        handle.set_telemetry_summary(observer.summarize())

    handle.end(
        exit_status=overall,
        end_resource_snapshot=forensic.capture_local_resource_snapshot(pid=os.getpid(), data_dir=handle.run_dir),
    )
    if scen.testcase_candidate:
        testcase_lifecycle.ingest_run_issue(
            runs_dir=Path(runs_dir),
            state_dir=Path(state_dir),
            run_id=handle.run_id,
            classification=scen.testcase_candidate["classification"],
            triage_reason=scen.testcase_candidate["triage_reason"],
            producer=scen.testcase_candidate["producer"],
            source_artifact_path=scen.testcase_candidate["source_artifact_path"],
        )
    return handle


@dataclass
class CompareResult:
    runs: dict  # {"amaru": RunHandle, "cardano-node": RunHandle}
    agreed: bool
    comparison_path: "Path"
    comparison_json_path: "Path | None" = None
    run_outcomes: dict | None = None
    behavior_summaries: dict | None = None
    resource_summaries: dict | None = None


def compare_run(scenario_path, *, runs_dir, state_dir, registry_path=None,
                implementation_manifest_dirs=None):
    """Run a scenario against both implementations (amaru, cardano-node) and diff.

    The scenario's `target.implementation` is overridden per run. For scenarios
    that reference target shims (cbor_fuzz_target), callers can pass
    `implementation_manifest_dirs={"amaru": "...", "cardano-node": "..."}` to
    point each run at its implementation's shim manifest directory. Without
    overrides, the scenario's declared manifests_dir is used for both.
    """
    import copy
    import tempfile
    from pathlib import Path as _P

    original = load_scenario(scenario_path)
    implementations = ("amaru", "cardano-node")
    runs = {}

    original_impl = original.target.get("implementation")
    for impl in implementations:
        # Build a per-impl scenario with target.implementation overridden and
        # (optionally) manifests_dir redirected. Write to a temp file so
        # run_scenario's forensic bundle captures exactly what ran.
        body = json.loads(original.raw_bytes.decode("utf-8"))
        body["target"]["implementation"] = impl
        # Keep the id distinct per implementation so forensic bundles are clearly labeled.
        body["id"] = f"{body['id']}-{impl}"
        if implementation_manifest_dirs and impl in implementation_manifest_dirs:
            new_md = implementation_manifest_dirs[impl]
            for ref in body.get("load", []):
                if ref.get("primitive") in TARGET_MANIFEST_PRIMITIVES:
                    ref["manifests_dir"] = new_md
        # Rewrite target_id to point at this implementation's manifest. Without
        # this, both runs would invoke the binary named in the original
        # scenario's target_id, defeating the cross-impl comparison.
        if original_impl and original_impl != impl:
            for ref in body.get("load", []):
                if ref.get("primitive") in TARGET_MANIFEST_PRIMITIVES:
                    tid = ref.get("target_id")
                    if isinstance(tid, str):
                        ref["target_id"] = _rewrite_target_id_for_impl(tid, original_impl, impl)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(json.dumps(body))
            tmp_path = _P(f.name)
        try:
            handle = run_scenario(
                tmp_path, runs_dir=runs_dir, state_dir=state_dir,
                registry_path=registry_path,
            )
            runs[impl] = handle
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    from profile_manager import testcase_lifecycle

    # Compare the two runs.
    a_manifest = json.loads((runs["amaru"].run_dir / "manifest.json").read_text())
    c_manifest = json.loads((runs["cardano-node"].run_dir / "manifest.json").read_text())
    run_outcomes = {
        "amaru": a_manifest.get("exit_status", "unknown"),
        "cardano-node": c_manifest.get("exit_status", "unknown"),
    }
    behavior_summaries = {
        "amaru": testcase_lifecycle.summarize_run_behavior(run_dir=runs["amaru"].run_dir),
        "cardano-node": testcase_lifecycle.summarize_run_behavior(run_dir=runs["cardano-node"].run_dir),
    }
    resource_summaries = {
        "amaru": testcase_lifecycle.summarize_run_resources(run_dir=runs["amaru"].run_dir),
        "cardano-node": testcase_lifecycle.summarize_run_resources(run_dir=runs["cardano-node"].run_dir),
    }
    agreed = (
        a_manifest.get("exit_status") == c_manifest.get("exit_status")
        and a_manifest.get("assertion_summary") == c_manifest.get("assertion_summary")
    )

    # Per-iteration outcome comparison for cbor_fuzz_target scenarios.
    a_outcomes = _extract_outcomes_from_dir(runs["amaru"].run_dir)
    c_outcomes = _extract_outcomes_from_dir(runs["cardano-node"].run_dir)
    iteration_diff_rows = []
    for i, (ao, co) in enumerate(zip(a_outcomes, c_outcomes)):
        a_out = ao.get("outcome")
        c_out = co.get("outcome")
        if a_out != c_out:
            iteration_diff_rows.append((i, a_out, c_out))
    if iteration_diff_rows:
        agreed = False

    # Emit comparison markdown into the second run's directory (cardano-node),
    # since compare is conceptually a follow-up to the amaru run. Content is
    # self-contained and links to both.
    cmp_dir = runs["cardano-node"].run_dir
    cmp_path = cmp_dir / "cross-impl-comparison.md"
    cmp_json_path = cmp_dir / "cross-impl-comparison.json"
    status_label = "AGREED" if agreed else "DIVERGED"
    iter_section = ""
    if iteration_diff_rows:
        iter_section = (
            "\n## Iteration-level divergence\n\n"
            "| Iteration | amaru | cardano-node |\n"
            "|---|---|---|\n"
            + "\n".join(f"| {i} | `{a}` | `{c}` |" for i, a, c in iteration_diff_rows)
            + "\n"
        )
    lines = [
        "# Cross-impl comparison",
        "",
        f"Result: **{status_label}**",
        "",
        f"Scenario: `{original.id}`",
        f"Seed:     `{original.seed}`",
        "",
        "## Per-implementation runs",
        "",
        f"- amaru:        `{runs['amaru'].run_id}` → exit `{a_manifest.get('exit_status')}`, "
        f"assertions {a_manifest.get('assertion_summary')}",
        f"- cardano-node: `{runs['cardano-node'].run_id}` → exit `{c_manifest.get('exit_status')}`, "
        f"assertions {c_manifest.get('assertion_summary')}",
        "",
        "## Normalized outcomes",
        "",
        f"- amaru: `{run_outcomes['amaru']}`",
        f"- cardano-node: `{run_outcomes['cardano-node']}`",
        "",
        "## Normalized behavior signatures",
        "",
        f"- amaru: `{behavior_summaries['amaru'].get('signature', 'none')}`",
        f"- cardano-node: `{behavior_summaries['cardano-node'].get('signature', 'none')}`",
        "",
        "## Normalized resource signatures",
        "",
        f"- amaru: `{resource_summaries['amaru'].get('signature', 'none')}`",
        f"- cardano-node: `{resource_summaries['cardano-node'].get('signature', 'none')}`",
        iter_section,
    ]
    cmp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    compare_diff = _build_compare_diff(
        {
            "amaru": runs["amaru"].run_dir,
            "cardano-node": runs["cardano-node"].run_dir,
        }
    )
    compare_payload = {
        "result": status_label,
        "scenario_id": original.id,
        "seed": original.seed,
        "agreed": agreed,
        "runs": {
            "amaru": {
                "run_id": runs["amaru"].run_id,
                "exit_status": a_manifest.get("exit_status"),
                "assertion_summary": a_manifest.get("assertion_summary"),
            },
            "cardano-node": {
                "run_id": runs["cardano-node"].run_id,
                "exit_status": c_manifest.get("exit_status"),
                "assertion_summary": c_manifest.get("assertion_summary"),
            },
        },
        "run_outcomes": run_outcomes,
        "behavior_summaries": behavior_summaries,
        "resource_summaries": resource_summaries,
        "iteration_differences": [
            {"iteration": i, "amaru": a, "cardano-node": c}
            for i, a, c in iteration_diff_rows
        ],
        "compare_diff": compare_diff,
    }
    cmp_json_path.write_text(json.dumps(compare_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return CompareResult(
        runs=runs,
        agreed=agreed,
        comparison_path=cmp_path,
        comparison_json_path=cmp_json_path,
        run_outcomes=run_outcomes,
        behavior_summaries=behavior_summaries,
        resource_summaries=resource_summaries,
    )


def _rewrite_target_id_for_impl(target_id, from_impl, to_impl):
    """Swap an implementation prefix in a shim target_id.

    Examples (from_impl='amaru', to_impl='cardano-node'):
      amaru-cbor-decode-tx-body  ->  cardano-node-cbor-decode-tx-body
      t-amaru                    ->  t-cardano-node
      something-else             ->  something-else  (no prefix match — left as-is;
                                                       runner will surface a manifest-not-found
                                                       error instead of silently mis-routing)
    """
    if not target_id or not isinstance(target_id, str):
        return target_id
    prefix = from_impl + "-"
    if target_id.startswith(prefix):
        return to_impl + "-" + target_id[len(prefix):]
    suffix = "-" + from_impl
    if target_id.endswith(suffix):
        return target_id[:-len(suffix)] + "-" + to_impl
    return target_id


def _build_compare_diff(run_dirs):
    compare_diff = {}
    active_peer_counts = {
        "amaru": _active_upstream_peer_count(run_dirs["amaru"]),
        "cardano-node": _active_upstream_peer_count(run_dirs["cardano-node"]),
    }
    for metric_name in COMPARE_DIFF_METRICS:
        amaru_metric = _latest_runtime_metric_value(run_dirs["amaru"], metric_name)
        cardano_metric = _latest_runtime_metric_value(run_dirs["cardano-node"], metric_name)
        if amaru_metric is None and cardano_metric is None:
            continue
        diff = {
            "amaru": amaru_metric,
            "cardano-node": cardano_metric,
            "divergence": amaru_metric != cardano_metric,
        }
        if metric_name in AMARU_ONLY_COMPARE_METRICS and cardano_metric is None:
            diff["metadata"] = {"asymmetry": "amaru_only"}
        if isinstance(amaru_metric, (int, float)) and isinstance(cardano_metric, (int, float)):
            baseline = float(cardano_metric)
            if baseline != 0.0:
                diff["divergence_pct"] = ((float(amaru_metric) - baseline) / abs(baseline)) * 100.0
            elif float(amaru_metric) != 0.0:
                diff["divergence_pct"] = None
        if metric_name in TOPOLOGY_NORMALIZED_COMPARE_METRICS:
            amaru_active_peers = active_peer_counts["amaru"]
            cardano_active_peers = active_peer_counts["cardano-node"]
            if (
                isinstance(amaru_metric, (int, float))
                and isinstance(cardano_metric, (int, float))
                and isinstance(amaru_active_peers, int)
                and isinstance(cardano_active_peers, int)
                and amaru_active_peers > 0
                and cardano_active_peers > 0
            ):
                amaru_norm = float(amaru_metric) / amaru_active_peers
                cardano_norm = float(cardano_metric) / cardano_active_peers
                diff["amaru_per_active_peer"] = amaru_norm
                diff["cardano-node_per_active_peer"] = cardano_norm
                diff["raw_divergence"] = diff["divergence"]
                diff["divergence"] = amaru_norm != cardano_norm
                diff["normalization"] = {
                    "basis": "active_upstream_peer_count",
                    "amaru_active_peers": amaru_active_peers,
                    "cardano-node_active_peers": cardano_active_peers,
                }
                baseline = float(cardano_norm)
                if baseline != 0.0:
                    diff["divergence_pct"] = ((float(amaru_norm) - baseline) / abs(baseline)) * 100.0
                elif float(amaru_norm) != 0.0:
                    diff["divergence_pct"] = None
        compare_diff[metric_name] = diff
    return compare_diff


def _active_upstream_peer_count(run_dir):
    target_events_path = Path(run_dir) / "events" / "target.ndjson"
    if not target_events_path.exists():
        return None
    latest_count = None
    for raw in target_events_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload") or {}
        active_upstream_ips = payload.get("active_upstream_ips")
        if isinstance(active_upstream_ips, list):
            latest_count = len(active_upstream_ips)
    return latest_count


def _extract_outcomes_from_dir(run_dir):
    """Read log.ndjson from an arbitrary run dir and return iteration outcomes."""
    outcomes = []
    log_path = run_dir / "log.ndjson"
    if not log_path.exists():
        return outcomes
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("event") == "iteration" and entry.get("phase") == "load":
            outcomes.append(dict(entry.get("payload") or {}))
    return outcomes


def _latest_runtime_metric_value(run_dir, metric_name):
    metrics_dir = run_dir / "metrics" / "runtime"
    if not metrics_dir.exists():
        return None
    candidates = sorted(metrics_dir.glob(f"{metric_name}*.ndjson"))
    if not candidates:
        return None
    latest_value = None
    for path in candidates:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "value" in entry:
                latest_value = entry["value"]
    return latest_value


def replay_run(run_id, *, runs_dir, state_dir, registry_path=None):
    """Re-run a scenario from its original forensic bundle.

    The replay reuses the original scenario.yaml and recorded seed, producing a
    new bundle whose directory contains a replay-comparison.md diffing the
    assertion summaries between the original and the replay.
    """
    runs_dir = Path(runs_dir)
    state_dir = Path(state_dir)
    original_dir = runs_dir / run_id
    if not original_dir.is_dir():
        raise FileNotFoundError(f"original run missing: {original_dir}")
    scenario_path = original_dir / "scenario.yaml"
    if not scenario_path.exists():
        raise FileNotFoundError(f"bundle has no scenario.yaml: {scenario_path}")

    # Stage the original scenario into a fresh path so the replay's bundle has a
    # clean copy (start_run captures whatever bytes we pass in).
    staged = original_dir / ".replay-input.yaml"
    staged.write_bytes(scenario_path.read_bytes())
    try:
        replay = run_scenario(
            staged, runs_dir=runs_dir, state_dir=state_dir, registry_path=registry_path,
        )
    finally:
        try:
            staged.unlink()
        except OSError:
            pass

    # Emit replay-comparison.md inside the replay's bundle.
    orig_manifest = json.loads((original_dir / "manifest.json").read_text(encoding="utf-8"))
    replay_manifest = json.loads((replay.run_dir / "manifest.json").read_text(encoding="utf-8"))
    lines = [
        f"# Replay comparison",
        "",
        f"Original run: `{run_id}`",
        f"Replay run:   `{replay.run_id}`",
        "",
        f"Scenario: `{orig_manifest.get('scenario', {}).get('id')}`",
        f"Seed:     `{orig_manifest.get('seed')}`",
        f"Runtime:  `{orig_manifest.get('runtime')}`",
        "",
        "## Assertion summary",
        "",
        f"- Original: `{orig_manifest.get('assertion_summary')}`",
        f"- Replay:   `{replay_manifest.get('assertion_summary')}`",
        "",
        "## Exit status",
        "",
        f"- Original: `{orig_manifest.get('exit_status')}`",
        f"- Replay:   `{replay_manifest.get('exit_status')}`",
        "",
        "## Match?",
        "",
        (
            "**Yes** — assertion summaries and exit statuses are identical."
            if (
                orig_manifest.get("assertion_summary") == replay_manifest.get("assertion_summary")
                and orig_manifest.get("exit_status") == replay_manifest.get("exit_status")
            )
            else "**No** — divergence detected. Investigate the iteration log and resource snapshot."
        ),
    ]
    (replay.run_dir / "replay-comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return replay


def _run_phase(handle, rng, registry, scen, refs, phase, ignore_errors=False, shared_state=None):
    from profile_manager import primitives
    for ref in refs:
        try:
            prim = primitives.instantiate(
                registry, name=ref.primitive, params=ref.params,
                runtime=scen.runtime, target_implementation=scen.target["implementation"],
            )
            # Bind per-run shared state so StartNodeProcess / StopNodeProcess /
            # ProcessRss all see the same node process map.
            if shared_state is not None and hasattr(prim, "state"):
                prim.state = shared_state
            if shared_state is not None and hasattr(prim, "bound_state"):
                prim.bound_state = shared_state
            if hasattr(prim, "bound_substrate"):
                prim.bound_substrate = scen.substrate
            if hasattr(prim, "bound_schedule"):
                prim.bound_schedule = scen.schedule
            if hasattr(prim, "run"):
                prim.run(handle, rng)
            else:
                raise TypeError(f"primitive {ref.primitive!r} has no run() method; cannot be used in {phase}")
        except Exception as exc:
            handle.log(phase=phase, primitive=ref.primitive, level="error",
                       event="phase_error", payload={"message": str(exc), "type": type(exc).__name__})
            if not ignore_errors:
                raise


def _extract_outcomes(handle):
    """Re-read the forensic log.ndjson to collect cbor_fuzz_target iteration events."""
    outcomes = []
    log_path = handle.run_dir / "log.ndjson"
    if not log_path.exists():
        return outcomes
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("event") == "iteration" and entry.get("phase") == "load":
            payload = entry.get("payload") or {}
            outcomes.append(dict(payload))
    return outcomes


_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")


def _safe_pending_path(scenarios_dir, scenario_id):
    """Build a path under <scenarios_dir>/pending/<id>.yaml after validating the id."""
    if not scenario_id or not _SAFE_ID.match(scenario_id):
        return None
    pending_dir = Path(scenarios_dir) / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    return pending_dir / f"{scenario_id}.yaml"


def validate_scenario_body(body_bytes):
    """Item #15 — validate a posted YAML/JSON body without writing it.

    Returns a JSON-friendly report:
      {"ok": True, "scenario_id": "...", "runtime": "...", "target": {...}}
      {"ok": False, "error": "...", "kind": "json|schema|other"}

    Reuses ``load_scenario`` indirectly: parse → ``_validate_top`` and
    every sibling validator. The bytes are not written anywhere — the
    editor's validate endpoint is read-only by design.
    """
    try:
        text = body_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return {"ok": False, "error": f"body is not utf-8: {exc}", "kind": "encoding"}
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": f"not JSON-structured YAML: {exc.msg}",
            "kind": "json",
            "line": exc.lineno,
            "column": exc.colno,
        }
    if not isinstance(body, dict):
        return {"ok": False, "error": "scenario top-level must be a mapping",
                "kind": "schema"}
    try:
        scenario_id, title, target, runtime, profile, substrate, iterations, shrink = _validate_top(body)
        related_milestones = _optional_string_list(body, "related_milestones")
        m1_trace = _validate_m1_trace(body)
        evidence_intent = _validate_evidence_intent(body)
        schedule = _validate_schedule(body)
        promotion_blockers = _optional_string_list(body, "promotion_blockers")
        testcase_candidate = _validate_testcase_candidate(body)
        phases = _phase_refs(body)
        if phases:
            for key in ("setup", "load", "faults", "probes", "assertions", "teardown"):
                if body.get(key):
                    raise ScenarioValidationError(
                        f"scenarios with phases may not also define top-level {key}"
                    )
        else:
            for key in ("setup", "load", "faults", "probes", "assertions", "teardown"):
                _primitive_refs(body, key)
    except ScenarioValidationError as exc:
        return {"ok": False, "error": str(exc), "kind": "schema",
                "scenario_id": body.get("id")}
    return {
        "ok": True,
        "scenario_id": scenario_id,
        "runtime": runtime,
        "target": dict(target),
    }


def save_scenario_body(body_bytes, *, scenarios_dir):
    """Item #15 — validate body, then atomically write
    ``<scenarios_dir>/<id>.yaml``.

    Returns a JSON-friendly report. Existing files are overwritten in
    place via ``os.replace`` of a tmp file in the same directory, so a
    half-written save can never replace a valid scenario. The body's
    own ``id`` field determines the filename — the caller's URL or
    filename is not trusted.
    """
    report = validate_scenario_body(body_bytes)
    if not report.get("ok"):
        return report
    scenarios_dir = Path(scenarios_dir)
    scenario_id = report["scenario_id"]
    if not _SAFE_ID.match(scenario_id):
        return {"ok": False, "error": f"unsafe scenario id: {scenario_id!r}",
                "kind": "schema"}
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    target = scenarios_dir / f"{scenario_id}.yaml"
    tmp = target.with_suffix(".yaml.tmp")
    tmp.write_bytes(body_bytes)
    os.replace(tmp, target)
    return {
        "ok": True,
        "scenario_id": scenario_id,
        "saved_path": str(target),
        "runtime": report["runtime"],
        "target": report["target"],
    }


def handle_paste(body_bytes, *, scenarios_dir):
    """Write pasted YAML to pending/, validate, return a JSON-friendly report.

    On invalid YAML or schema failure the file is removed so the staging area
    stays clean. The id is taken from the validated scenario; we don't trust the
    caller's filename.
    """
    scenarios_dir = Path(scenarios_dir)
    # First-pass parse to extract the id without writing anything.
    try:
        body = json.loads(body_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"not JSON-structured YAML: {exc}"}
    if not isinstance(body, dict):
        return {"ok": False, "error": "scenario top-level must be a mapping"}
    scenario_id = body.get("id")
    pending_path = _safe_pending_path(scenarios_dir, scenario_id)
    if pending_path is None:
        return {"ok": False, "error": f"invalid or missing scenario id: {scenario_id!r}"}
    pending_path.write_bytes(body_bytes)
    try:
        s = load_scenario(pending_path)
    except ScenarioValidationError as exc:
        try:
            pending_path.unlink()
        except OSError:
            pass
        return {"ok": False, "error": str(exc), "scenario_id": scenario_id}
    return {
        "ok": True,
        "scenario_id": s.id,
        "pending_path": str(pending_path),
        "runtime": s.runtime,
        "target": dict(s.target),
    }


def handle_promote(scenario_id, *, scenarios_dir):
    """Move pending/<id>.yaml to <scenarios_dir>/<id>.yaml after re-validating."""
    scenarios_dir = Path(scenarios_dir)
    if not scenario_id or not _SAFE_ID.match(scenario_id):
        return {"ok": False, "error": f"invalid scenario id: {scenario_id!r}"}
    pending = scenarios_dir / "pending" / f"{scenario_id}.yaml"
    if not pending.exists():
        return {"ok": False, "error": f"no pending scenario with id {scenario_id!r}"}
    try:
        s = load_scenario(pending)
    except ScenarioValidationError as exc:
        return {"ok": False, "error": f"scenario re-validation failed: {exc}"}
    if s.id != scenario_id:
        return {"ok": False, "error": f"id mismatch: pending file has id {s.id!r}"}
    target = scenarios_dir / f"{scenario_id}.yaml"
    pending.replace(target)
    return {"ok": True, "scenario_id": s.id, "promoted_path": str(target)}


def load_scenario(path):
    path = Path(path)
    raw = path.read_bytes()
    try:
        body = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ScenarioValidationError(f"scenario is not JSON-structured YAML: {exc}") from exc
    if not isinstance(body, dict):
        raise ScenarioValidationError("scenario top-level must be a mapping")
    scenario_id, title, target, runtime, profile, substrate, iterations, shrink = _validate_top(body)
    related_milestones = _optional_string_list(body, "related_milestones")
    m1_trace = _validate_m1_trace(body)
    evidence_intent = _validate_evidence_intent(body)
    schedule = _validate_schedule(body)
    promotion_blockers = _optional_string_list(body, "promotion_blockers")
    testcase_candidate = _validate_testcase_candidate(body)
    phases = _phase_refs(body)
    if phases:
        for key in ("setup", "load", "faults", "probes", "assertions", "teardown"):
            if body.get(key):
                raise ScenarioValidationError(f"scenarios with phases may not also define top-level {key}")
        setup = []
        load = []
        faults = []
        probes = []
        assertions = []
        teardown = []
    else:
        setup = _primitive_refs(body, "setup")
        load = _primitive_refs(body, "load")
        faults = _primitive_refs(body, "faults")
        probes = _primitive_refs(body, "probes")
        assertions = _primitive_refs(body, "assertions")
        teardown = _primitive_refs(body, "teardown")
    return Scenario(
        id=scenario_id,
        title=title,
        target=dict(target),
        runtime=runtime,
        profile=profile,
        substrate=substrate,
        seed=body.get("seed"),
        iterations=iterations,
        shrink=shrink,
        related_milestones=related_milestones,
        m1_trace=m1_trace,
        evidence_intent=evidence_intent,
        promotion_blockers=promotion_blockers,
        testcase_candidate=testcase_candidate,
        setup=setup,
        load=load,
        faults=faults,
        probes=probes,
        assertions=assertions,
        teardown=teardown,
        phases=phases,
        path=path,
        raw_bytes=raw,
        schedule=schedule,
    )


def _iter_section_refs(scenario: Scenario):
    if scenario.phases:
        for phase in scenario.phases:
            for section in ("setup", "load", "faults", "probes", "assertions", "teardown"):
                for ref in getattr(phase, section):
                    yield {
                        "phase_id": phase.id,
                        "section": section,
                        "family": SECTION_FAMILY_MAP[section],
                        "ref": ref,
                    }
    else:
        for section in ("setup", "load", "faults", "probes", "assertions", "teardown"):
            for ref in getattr(scenario, section):
                yield {
                    "phase_id": None,
                    "section": section,
                    "family": SECTION_FAMILY_MAP[section],
                    "ref": ref,
                }


def _schema_required_fields(schema_path: Path) -> list[str]:
    try:
        data = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    required = data.get("required")
    if not isinstance(required, list):
        return []
    return [field for field in required if isinstance(field, str)]


def _collect_node_id_reference_errors(ref: PrimitiveRef, *, node_ids: set[str], section: str, phase_id: str | None) -> list[str]:
    errors = []
    scope = f"phase {phase_id} {section}" if phase_id else section
    for key, value in ref.params.items():
        if key in NODE_ID_VALUE_KEYS or key.endswith("_node_id"):
            if isinstance(value, str) and value not in node_ids:
                errors.append(f"{scope}.{ref.primitive}.{key} references unknown node id {value!r}")
        if key in NODE_ID_LIST_KEYS or key.endswith("_nodes"):
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item not in node_ids:
                        errors.append(f"{scope}.{ref.primitive}.{key} references unknown node id {item!r}")
    return errors


def semantic_validate_scenario(path, *, registry_path: Path | None = None):
    scenario = load_scenario(path)
    registry = primitives_module.load_registry(registry_path or DEFAULT_REGISTRY_PATH)
    errors: list[str] = []
    warnings: list[str] = []
    active_non_assertion_by_scope: dict[str, set[str]] = {}
    node_ids = set()
    if scenario.substrate:
        node_ids = {node["id"] for node in scenario.substrate.get("nodes", [])}

    for item in _iter_section_refs(scenario):
        phase_id = item["phase_id"] or "__top__"
        family = item["family"]
        ref = item["ref"]
        active_non_assertion_by_scope.setdefault(phase_id, set())
        if ref.primitive not in registry:
            errors.append(f"{item['section']}.{ref.primitive} is not present in the primitive registry")
            continue
        entry = registry[ref.primitive]
        if entry.family != family:
            message = f"{item['section']}.{ref.primitive} is registered as family {entry.family!r}, expected {family!r}"
            if ref.primitive in LEGACY_SEMANTIC_WARNING_PRIMITIVES:
                warnings.append(message)
            else:
                errors.append(message)
        if scenario.runtime not in entry.runtimes:
            message = f"{item['section']}.{ref.primitive} does not support runtime {scenario.runtime!r}"
            if ref.primitive in LEGACY_SEMANTIC_WARNING_PRIMITIVES:
                warnings.append(message)
            else:
                errors.append(message)
        if scenario.target["implementation"] not in entry.supports:
            errors.append(
                f"{item['section']}.{ref.primitive} does not support target {scenario.target['implementation']!r}"
            )
        primitive_version = ref.params.get("primitive_version")
        if primitive_version is not None and primitive_version != entry.version:
            errors.append(
                f"{item['section']}.{ref.primitive} requested primitive_version {primitive_version!r}, registry has {entry.version!r}"
            )
        if entry.params_schema:
            schema_path = Path(__file__).resolve().parents[1] / entry.params_schema
            for required_key in _schema_required_fields(schema_path):
                if required_key not in ref.params:
                    warnings.append(
                        f"{item['section']}.{ref.primitive} is missing schema-required field {required_key!r}"
                    )
        if node_ids:
            errors.extend(
                _collect_node_id_reference_errors(
                    ref,
                    node_ids=node_ids,
                    section=item["section"],
                    phase_id=item["phase_id"],
                )
            )
        if family != "assertion":
            active_non_assertion_by_scope[phase_id].add(ref.primitive)

    for item in _iter_section_refs(scenario):
        if item["family"] != "assertion":
            continue
        phase_id = item["phase_id"] or "__top__"
        ref = item["ref"]
        required_producers = ASSERTION_PRODUCER_MAP.get(ref.primitive)
        if not required_producers:
            continue
        scoped_primitives = active_non_assertion_by_scope.get(phase_id, set())
        global_primitives = active_non_assertion_by_scope.get("__top__", set())
        if not ((required_producers & scoped_primitives) or (required_producers & global_primitives)):
            producer_list = ", ".join(sorted(required_producers))
            scope = f"phase {item['phase_id']}" if item["phase_id"] else "top-level scenario"
            errors.append(
                f"{scope} assertion {ref.primitive!r} requires one of [{producer_list}] in the same scenario scope"
            )

    return {"scenario": scenario, "errors": errors, "warnings": warnings}
