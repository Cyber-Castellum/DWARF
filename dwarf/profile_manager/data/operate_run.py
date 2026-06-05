"""Single-run inspector data extractor for /operate/runs/<id>.

Reads everything the legacy inspector showed — manifest, chain, assertions,
log tail, probe summary, iteration outcomes, cross-impl markdown — but
returns a render-ready dict instead of building HTML inline.

Source files inside dwarf/runs/<id>/:
    manifest.json             — scenario, target, runtime, assertion summary
    chain.json                — manifest_hash, prev_hash (tamper chain)
    assertions.json           — list of assertion records (optional)
    cross-impl-comparison.md  — comparison report (optional, diff side only)
    log.ndjson                — observer events stream
    probes/<name>.ndjson      — time-series probe samples (optional)

forensic.verify is invoked to surface the tamper-check verdict; failures
on missing files yield an empty `errors` list and `ok=False`.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


_PREV_HASH_MISMATCH_RE = re.compile(
    r"^prev_hash\s+(?P<hash>[0-9a-f]{64})\s+for\s+run\s+(?P<run_id>\S+)\s+does not match any known chain entry$"
)
_MANIFEST_HASH_MISMATCH_RE = re.compile(
    r"^manifest_hash mismatch for\s+(?P<run_id>\S+):\s+expected\s+(?P<expected>[0-9a-f]{64}),\s+got\s+(?P<actual>[0-9a-f]{64})$"
)


def _structure_verify_error(message: str) -> dict[str, Any]:
    """Lift a forensic.verify error string into a structured dict the
    template renders as kind + monospaced field rows. Falls back to
    rendering the raw string when no recognised pattern matches — never
    drops information."""
    m = _PREV_HASH_MISMATCH_RE.match(message)
    if m:
        return {
            "kind": "Hash chain link missing",
            "raw": message,
            "fields": [
                {"label": "prev_hash", "value": m.group("hash"), "is_hash": True},
                {"label": "run_id", "value": m.group("run_id"), "is_hash": False},
            ],
        }
    m = _MANIFEST_HASH_MISMATCH_RE.match(message)
    if m:
        return {
            "kind": "Manifest hash mismatch",
            "raw": message,
            "fields": [
                {"label": "run_id", "value": m.group("run_id"), "is_hash": False},
                {"label": "expected", "value": m.group("expected"), "is_hash": True},
                {"label": "actual", "value": m.group("actual"), "is_hash": True},
            ],
        }
    return {"kind": "Verify error", "raw": message, "fields": []}


def _forensic_runs_dir() -> Path:
    env = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "runs"


def _state_dir(runs_dir: Path) -> Path:
    env = os.environ.get("ADA2_DWARF_STATE_DIR")
    if env:
        return Path(env)
    return runs_dir.parent / "state"


_PRETTY_PAYLOAD_LONG_THRESHOLD = 200


def _pretty_format_payload(value: Any) -> dict[str, Any]:
    """Pre-format a JSON-able payload for the inspector tables.

    Returns a dict with three views the template renders without further
    logic: ``compact`` (one-line JSON for short values), ``pretty``
    (indent=2, sort_keys=True — preserves embedded ``\\n`` as real line
    breaks because indent renders strings inside the printed JSON), and
    ``length`` of the compact form. ``is_long`` flips when the compact
    form crosses ``_PRETTY_PAYLOAD_LONG_THRESHOLD`` and tells the template
    to wrap the pretty form in a collapsible disclosure instead of a raw
    inline ``<code>`` cell.
    """
    try:
        compact = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        pretty = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        compact = str(value)
        pretty = compact
    length = len(compact)
    return {
        "compact": compact,
        "pretty": pretty,
        "length": length,
        "is_long": length > _PRETTY_PAYLOAD_LONG_THRESHOLD,
    }


_RUN_FIELD_HELP = {
    "scenario": "The named test recipe Dwarf executed.",
    "target": "The implementation and version the scenario exercised.",
    "runtime": "Where the test ran. Library runtime means a parser or helper binary was invoked directly.",
    "started": "When Dwarf began recording this run.",
    "ended": "When Dwarf finished recording this run.",
    "actor": "The operator or automation identity that created the run.",
    "seed": "The repeatable random seed used by fuzzing scenarios.",
    "profile": "The runtime profile selected for node-based scenarios, when one was needed.",
    "evidence path": "The filesystem directory that contains this run's evidence bundle.",
    "manifest hash": "A SHA-256 digest of the manifest. If the manifest changes later, the digest changes.",
    "prev hash": "The previous entry in the tamper-evident chain. This links runs into an ordered evidence history.",
    "assertions": "Checks Dwarf evaluated after the load step. Passing assertions mean the expected safety condition held for this run.",
    "tamper check": "Dwarf recomputed evidence hashes and checked the run's hash-chain links.",
    "wall time": "How long the scenario execution took from start to finish.",
    "iteration outcomes": "Counts of per-input fuzzing or edge-case events recorded by the load primitive.",
    "primitive": "The Dwarf primitive that produced or checked this part of the run.",
    "params": "Inputs passed to the assertion primitive.",
    "evaluated": "The measured value the assertion used to decide pass or fail.",
    "result": "The assertion outcome recorded in this bundle.",
    "ts": "Timestamp for the log event.",
    "phase": "Scenario phase, such as load, probe, assertion, or teardown.",
    "event": "The event name emitted by the primitive.",
    "payload": "Structured details attached to the event.",
    "probe": "One preserved probe stream. Probes are side-channel measurements such as resource usage, process state, or target telemetry captured during the run.",
    "samples": "How many timestamped measurements were preserved for this probe stream.",
    "log row": "One event emitted by a Dwarf primitive while the scenario was running.",
    "payload details": "Structured details attached to this event or assertion. Long payloads are collapsed so the page stays readable.",
}

_RUN_CONTROL_HELP = {
    "live tail": "Open the live event stream for this run. Use it while a scenario is still executing or when you want to watch appended log events.",
    "export bundle": "Download this run as a portable tar.gz evidence bundle. The archive contains the manifest, logs, assertions, hash-chain data, and related artifacts for this run.",
    "sarif export": "Static Analysis Results Interchange Format (SARIF) is a JSON format used by GitHub code scanning, Sonar, and other audit tools. This section shows whether this run has a staged SARIF artifact.",
    "download sarif": "Download this run's SARIF file for import into GitHub code scanning, Sonar, or another SARIF-compatible review tool.",
    "generate sarif": "Command to generate a SARIF artifact for this run when one has not already been staged.",
    "operator actions": "Copy-paste commands for follow-up evidence work on this bundle: replay, diff, SARIF export, and hash-chain verification.",
    "action command": "This is an operator command, not a browser action. Copy it into a shell where cardano-profile is installed.",
    "assertions section": "Recorded assertions are the checks Dwarf evaluated after running the scenario. They explain why the bundle is marked pass or fail.",
    "probes section": "Probe files are time-series measurements captured alongside the run, usually for resource usage, process state, or target telemetry.",
    "log tail section": "The last recorded observer events from this run. Use this to see what the scenario did immediately before completion.",
    "floor preview section": "Re-runs current assertion-floor logic against preserved telemetry to show whether older recorded results still match today's evaluator.",
    "substrate evidence section": "Summaries from multi-node or composed-substrate scenarios, when this bundle includes node topology, convergence, byzantine-peer, or era-transition evidence.",
}


_CBOR_SURFACES = (
    ("block-header", "block-header"),
    ("tx-body", "transaction-body"),
    ("certificate", "certificate"),
    ("auxiliary-data", "auxiliary-data"),
    ("block", "block"),
    ("submit-api-tx", "submit-API transaction"),
)


def _target_display_name(target_implementation: str) -> str:
    if target_implementation == "amaru":
        return "Amaru"
    if target_implementation == "cardano-node":
        return "cardano-node"
    return target_implementation or "the target"


def _cbor_surface_label(scenario_id: str) -> str:
    for token, label in _CBOR_SURFACES:
        if token in scenario_id:
            return label
    return "parser"


def _run_explanation(
    *,
    scenario_id: str,
    target_implementation: str,
    runtime: str,
    exit_status: str,
    assertion_summary: dict[str, Any],
    verify_ok: bool,
) -> dict[str, Any]:
    """Build a reader-facing explanation without changing source evidence."""
    target = _target_display_name(target_implementation)
    bullets: list[str] = []
    if exit_status:
        bullets.append(f"The recorded result is {exit_status}.")

    total = int(assertion_summary.get("total", 0) or 0)
    passed = int(assertion_summary.get("pass", 0) or 0)
    failed = int(assertion_summary.get("fail", 0) or 0)
    if total and failed == 0:
        bullets.append(f"All {total} recorded assertion{'s' if total != 1 else ''} passed.")
    elif total:
        bullets.append(f"{passed} of {total} recorded assertions passed; {failed} failed.")
    else:
        bullets.append("No recorded assertions were present in this bundle.")

    if verify_ok:
        bullets.append("The bundle tamper check is verified.")
    else:
        bullets.append("The bundle tamper check did not verify in this runtime; inspect the Tamper check tile for details.")

    if "cbor" in scenario_id:
        surface = _cbor_surface_label(scenario_id)
        input_kind = "fixed edge-case" if "edge-cases" in scenario_id else "randomized"
        summary = (
            "This run sent "
            f"{input_kind} Concise Binary Object Representation (CBOR) {surface} inputs "
            f"into the {target} parser and checked that parsing either succeeded cleanly or failed safely."
        )
    else:
        summary = (
            f"This run executed scenario {scenario_id or 'unknown'} against {target} "
            f"using the {runtime or 'unknown'} runtime and recorded the evidence shown below."
        )

    return {
        "title": "Plain-English summary",
        "summary": summary,
        "bullets": bullets,
        "field_help": _RUN_FIELD_HELP,
        "control_help": _RUN_CONTROL_HELP,
        "guide_steps": [
            "Start with the status banner and Assertions tile: they tell you whether Dwarf recorded the run as passing.",
            "Check Tamper check next: VERIFIED means the bundle still matches its recorded hashes.",
            "Use the Manifest section to see what scenario ran, what implementation was targeted, and what seed makes the run repeatable.",
            "Use Recorded assertions and Log tail when you need the technical details behind the pass/fail result.",
        ],
    }


def _read_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _read_ndjson(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if limit is not None and len(out) >= limit:
                break
    return out


# Item B (Phase 4.3 D-3) — accept all three per-event shapes the
# library-tier load primitives actually emit:
#   event="iteration_outcome"  → legacy (matches scenario.py:1028's
#                                "iteration"-only extractor on the same
#                                axis but a different name; some primitives
#                                emit one, some the other).
#   event="iteration"          → cbor_fuzz_structured + roundtrip family
#                                (e.g. log.ndjson rows in 8b7bf69f).
#   event="case"               → cbor_edge_cases family (e.g. 84244a77).
# All three carry the per-row outcome at payload.outcome. Sibling pattern
# to F-017 in scenario.py — same blind spot at a different layer; this
# fix is the dashboard half only. F-017 covers the evaluator side.
_ITERATION_EVENT_NAMES = frozenset({"iteration_outcome", "iteration", "case"})


def _iteration_event_counts(run_dir: Path) -> dict[str, int]:
    """Count per-row outcomes from log.ndjson across the iteration /
    iteration_outcome / case event shapes."""
    counts: dict[str, int] = {}
    log = run_dir / "log.ndjson"
    if not log.is_file():
        return counts
    with log.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") not in _ITERATION_EVENT_NAMES:
                continue
            outcome = (event.get("payload") or {}).get("outcome") or "other"
            counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def _safe_relative(path: Path | None, base: Path) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _provenance_section(run_dir: Path) -> dict[str, Any]:
    """Read outputs/attestation/attestation.json (if present) into a
    render-ready provenance dict. Slice 30 surfaces ada3's
    runtime_bundle_attestation primitive output without restating its
    schema — every field below is verbatim from the JSON, the view layer
    only formats."""
    path = run_dir / "outputs" / "attestation" / "attestation.json"
    if not path.is_file():
        return {"present": False, "artifact_relpath": None, "verdict": None,
                "signing_actor": None, "key_fingerprint": None,
                "tooling_versions": {}, "statement_sha256": None,
                "attested_at_utc": None, "operator_warning": None,
                "key_source": None, "signature_hex": None}
    payload = _read_json(path, {})
    statement = payload.get("statement") or {}
    signature = payload.get("signature") or {}
    verification = payload.get("verification") or {}
    return {
        "present": True,
        "artifact_relpath": "outputs/attestation/attestation.json",
        "verdict": verification.get("verdict"),
        "signing_actor": signature.get("signing_actor"),
        "key_fingerprint": signature.get("signing_key_fingerprint"),
        "key_source": signature.get("key_source"),
        "operator_warning": bool(signature.get("operator_warning")),
        "signature_hex": signature.get("signature_hex"),
        "public_key_hex": signature.get("public_key_hex"),
        "attested_at_utc": payload.get("attested_at_utc"),
        "scenario_yaml_sha256": statement.get("scenario_yaml_sha256"),
        "active_profile_id": statement.get("active_profile_id"),
        "active_profile_sha256": statement.get("active_profile_sha256"),
        "dashboard_config_sha256": statement.get("dashboard_config_sha256"),
        "dwarf_source_sha256": statement.get("dwarf_source_sha256"),
        "tooling_versions": dict(statement.get("tooling_versions") or {}),
    }


def _export_section(run_dir: Path, run_id: str) -> dict[str, Any]:
    """Surface the outputs/sarif-export/dwarf-export.sarif file (if
    runtime_bundle_export_sarif has been run against this bundle) plus a
    pre-formatted CLI command operators can paste to generate the export
    when it is missing."""
    sarif_relpath = "outputs/sarif-export/dwarf-export.sarif"
    sarif_path = run_dir / sarif_relpath
    return {
        "sarif_present": sarif_path.is_file(),
        "sarif_artifact_relpath": sarif_relpath if sarif_path.is_file() else None,
        "sarif_size_bytes": sarif_path.stat().st_size if sarif_path.is_file() else None,
        "sarif_download_url": f"/runs/{run_id}/output?path={sarif_relpath}" if sarif_path.is_file() else None,
        "generate_command": (
            f"cardano-profile scenario run dwarf/scenarios/runtime-bundle-export-sarif-example-smoke.yaml "
            f"# (edit target_run_id to {run_id} before running)"
        ),
    }


def _substrate_topology_tile(run_dir: Path) -> dict[str, Any] | None:
    """outputs/substrate-compose/compose-report.json → topology tile.

    Operator-readable shape: per-node id/impl/version/role with a single
    pass/fail verdict driven by the report's `healthy` flag and whether
    every node also reports healthy individually."""
    path = run_dir / "outputs" / "substrate-compose" / "compose-report.json"
    if not path.is_file():
        return None
    payload = _read_json(path, {}) or {}
    nodes = payload.get("nodes") or []
    all_healthy = bool(payload.get("healthy")) and all(n.get("healthy") for n in nodes)
    impl_counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    for n in nodes:
        impl = n.get("impl") or "unknown"
        role = n.get("role") or "unknown"
        impl_counts[impl] = impl_counts.get(impl, 0) + 1
        role_counts[role] = role_counts.get(role, 0) + 1
    return {
        "verdict": "ok" if all_healthy else "error",
        "network": payload.get("network"),
        "node_count": payload.get("node_count") or len(nodes),
        "impl_counts": impl_counts,
        "role_counts": role_counts,
        "compose_project": payload.get("compose_project"),
        "nodes": [
            {
                "id": n.get("id"),
                "impl": n.get("impl"),
                "version": n.get("version") or n.get("resolved_version"),
                "role": n.get("role"),
                "healthy": bool(n.get("healthy")),
                "listen_address": n.get("listen_address"),
            }
            for n in nodes
        ],
    }


def _multi_node_observation_tile(run_dir: Path) -> dict[str, Any] | None:
    """outputs/multi-node-observation/observation-summary.json → tile.

    Distills per-node sample counts, computes ``tip_group_count`` (the
    number of distinct tip hashes the node observed during the window —
    >1 indicates a fork), and surfaces the observation window."""
    path = run_dir / "outputs" / "multi-node-observation" / "observation-summary.json"
    if not path.is_file():
        return None
    payload = _read_json(path, {}) or {}
    per_node = payload.get("per_node") or {}
    nodes = []
    any_divergent = False
    for node_id, node_data in per_node.items():
        tip = node_data.get("tip_state") or {}
        conn = node_data.get("connection_state") or {}
        samples = tip.get("samples") or []
        distinct_hashes = {s.get("hash") for s in samples if s.get("ok")}
        tip_group_count = len(distinct_hashes)
        if tip_group_count > 1:
            any_divergent = True
        nodes.append({
            "node_id": node_id,
            "implementation": node_data.get("implementation"),
            "version": node_data.get("version"),
            "tip_sample_count": tip.get("sample_count") or 0,
            "tip_successful_samples": tip.get("successful_sample_count") or 0,
            "tip_group_count": tip_group_count,
            "latest_slot": (tip.get("latest_tip") or {}).get("slot"),
            "connection_sample_count": conn.get("sample_count") or 0,
            "connection_successful_samples": conn.get("successful_sample_count") or 0,
        })
    if not nodes:
        verdict = "stale"
    elif any_divergent:
        verdict = "error"
    else:
        verdict = "ok"
    return {
        "verdict": verdict,
        "node_count": len(nodes),
        "observation_window_seconds": payload.get("observation_window_seconds"),
        "sample_interval_seconds": payload.get("sample_interval_seconds"),
        "observation_primitives": list(payload.get("observation_primitives") or []),
        "nodes": nodes,
        "any_divergent": any_divergent,
    }


def _byzantine_peer_tile(run_dir: Path) -> dict[str, Any] | None:
    """outputs/byzantine-peer/apply-report.json → tile."""
    path = run_dir / "outputs" / "byzantine-peer" / "apply-report.json"
    if not path.is_file():
        return None
    payload = _read_json(path, {}) or {}
    return {
        "verdict": "ok" if payload.get("healthy") else "error",
        "target_node_id": payload.get("target_node_id"),
        "upstream_address": payload.get("upstream_address"),
        "proxy_listen_address": payload.get("proxy_listen_address"),
        "intercepted_segments": payload.get("intercepted_segments") or 0,
        "mutated_segments": payload.get("mutated_segments") or 0,
    }


def _hf_boundary_tile(run_dir: Path) -> dict[str, Any] | None:
    """outputs/hf-boundary/hf-boundary-report.json → tile."""
    path = run_dir / "outputs" / "hf-boundary" / "hf-boundary-report.json"
    if not path.is_file():
        return None
    payload = _read_json(path, {}) or {}
    versions = payload.get("node_protocol_versions") or {}
    converged = bool(versions) and len(set(versions.values())) == 1
    return {
        "verdict": "ok" if converged else "error",
        "network": payload.get("network"),
        "target_slot": payload.get("target_slot"),
        "target_tx_id": payload.get("target_tx_id"),
        "node_protocol_versions": dict(versions),
        "converged": converged,
    }


def _era_transition_tile(run_dir: Path) -> dict[str, Any] | None:
    """outputs/era-transition/era-transition-report.json → tile."""
    path = run_dir / "outputs" / "era-transition" / "era-transition-report.json"
    if not path.is_file():
        return None
    payload = _read_json(path, {}) or {}
    pre = payload.get("pre_hf_validation") or {}
    post = payload.get("post_hf_validation") or {}
    pre_match = pre.get("rules_expected") == pre.get("rules_observed")
    post_match = post.get("rules_expected") == post.get("rules_observed")
    return {
        "verdict": "ok" if (pre_match and post_match) else "error",
        "network": payload.get("network"),
        "window_start_slot": payload.get("window_start_slot"),
        "window_end_slot": payload.get("window_end_slot"),
        "pre_expected": pre.get("rules_expected"),
        "pre_observed": pre.get("rules_observed"),
        "pre_match": pre_match,
        "post_expected": post.get("rules_expected"),
        "post_observed": post.get("rules_observed"),
        "post_match": post_match,
    }


def _genesis_mode_tile(run_dir: Path) -> dict[str, Any] | None:
    """outputs/genesis-mode/genesis-mode-report.json → tile."""
    path = run_dir / "outputs" / "genesis-mode" / "genesis-mode-report.json"
    if not path.is_file():
        return None
    payload = _read_json(path, {}) or {}
    captured = bool(payload.get("peer_set_capture_detected"))
    return {
        # Capture detection IS the success signal for the assertion (a
        # capture event was observed and the node escaped); but operator
        # eyes treat capture as the dangerous state. Keep "error" when
        # captured so the tile reads alarming.
        "verdict": "error" if captured else "ok",
        "network": payload.get("network"),
        "target_node": payload.get("target_node"),
        "final_mode": payload.get("final_mode"),
        "mode_path": list(payload.get("mode_path") or []),
        "peer_set_capture_detected": captured,
    }


def _substrate_evidence_section(run_dir: Path) -> dict[str, Any]:
    """Aggregate every substrate-output tile into one section. Tiles
    with no underlying file are simply omitted; the template renders
    only what's present."""
    tiles: list[tuple[str, dict[str, Any]]] = []
    pairs = [
        ("topology", _substrate_topology_tile(run_dir)),
        ("multi_node_observation", _multi_node_observation_tile(run_dir)),
        ("byzantine_peer", _byzantine_peer_tile(run_dir)),
        ("hf_boundary", _hf_boundary_tile(run_dir)),
        ("era_transition", _era_transition_tile(run_dir)),
        ("genesis_mode", _genesis_mode_tile(run_dir)),
    ]
    for name, tile in pairs:
        if tile is not None:
            tiles.append((name, tile))
    return {
        "present": bool(tiles),
        "tiles": dict(tiles),
        "tile_order": [name for name, _ in tiles],
    }


def _replay_artifact(run_dir: Path) -> dict[str, Any] | None:
    """If this run is itself a replay bundle, surface the verdict so the
    inspector reads the same way for replay outputs as for source runs."""
    path = run_dir / "outputs" / "replay" / "result.json"
    if not path.is_file():
        return None
    payload = _read_json(path, {})
    return {
        "verdict": payload.get("comparison_verdict"),
        "target_run_id": payload.get("target_run_id"),
        "replay_run_id": payload.get("replay_run_id"),
        "compared_relpaths": list(payload.get("compared_relpaths") or []),
        "comparisons": list(payload.get("comparisons") or []),
        "replayed_at_utc": payload.get("replayed_at_utc"),
    }


def _diff_artifact(run_dir: Path) -> dict[str, Any] | None:
    """If this run is a bundle-diff run, surface the diff verdict."""
    path = run_dir / "outputs" / "bundle-diff" / "diff.json"
    if not path.is_file():
        return None
    payload = _read_json(path, {})
    return {
        "verdict": payload.get("comparison_verdict"),
        "left_run_id": payload.get("left_run_id"),
        "right_run_id": payload.get("right_run_id"),
        "compared_relpaths": list(payload.get("compared_relpaths") or []),
        "comparisons": list(payload.get("comparisons") or []),
    }


def _actions_section(run_id: str) -> dict[str, Any]:
    """Operator-runnable CLI commands for replay/diff/sarif-export, with
    THIS bundle's run-id substituted into each invocation. Slice 30
    deliberately surfaces these as copy-paste commands rather than
    in-dashboard executions: the existing /api/scenario/run trigger only
    accepts a fixed scenario YAML, and the underlying primitives carry
    target_run_id inside the YAML — parameterising at trigger time would
    require new RPC plumbing this slice doesn't own."""
    return {
        "replay": {
            "label": "Replay this bundle",
            "primitive": "runtime_bundle_replay",
            "command": f"cardano-profile replay {run_id}",
            "summary": (
                "Re-execute the recorded scenario against the current target binary "
                "and write the replay outputs into a new bundle. The original "
                "and replay bundles can then be diffed."
            ),
        },
        "diff": {
            "label": "Diff against another bundle",
            "primitive": "runtime_bundle_diff",
            "command_template": (
                f"cardano-profile scenario run dwarf/scenarios/runtime-bundle-diff-example-smoke.yaml "
                f"# (edit left_run_id={run_id}, right_run_id=<other> before running)"
            ),
            "summary": (
                "Diff this bundle against a peer by run-id pair. The example smoke "
                "scenario hardcodes its bundle ids; copy it to a working YAML, "
                "substitute both ids, then promote with scenario promote."
            ),
        },
        "export_sarif": {
            "label": "Export SARIF",
            "primitive": "runtime_bundle_export_sarif",
            "command": (
                f"cardano-profile scenario run dwarf/scenarios/runtime-bundle-export-sarif-example-smoke.yaml "
                f"# (edit target_run_id={run_id} before running)"
            ),
            "summary": (
                "Render this bundle's findings into SARIF v2.1.0 for upstream tools "
                "(GitHub code-scanning, Sonar). The output lands at "
                "outputs/sarif-export/dwarf-export.sarif."
            ),
        },
        "verify_chain": {
            "label": "Verify chain to genesis",
            "primitive": "runtime_bundle_chain_verify",
            "command": f"cardano-profile verify {run_id}",
            "summary": (
                "Walk the hash chain back to genesis and assert every link's "
                "manifest_hash recomputes. The page already shows the in-place "
                "tamper-check verdict; this command produces a forensic bundle "
                "of the verification itself."
            ),
        },
    }


def operate_run_detail(run_id: str, *, runs_dir: Path | None = None) -> dict[str, Any] | None:
    """Return a render-ready bundle inspector payload, or None if missing.

    None is the "not found" signal: the view layer renders an explicit
    not-found state, never a misleading partial payload.
    """
    base = Path(runs_dir) if runs_dir is not None else _forensic_runs_dir()
    if not run_id or "/" in run_id or ".." in run_id:
        return None
    run_dir = base / run_id
    if not run_dir.is_dir():
        return None

    manifest = _read_json(run_dir / "manifest.json", {}) or {}
    chain = _read_json(run_dir / "chain.json", {}) or {}
    assertions = _read_json(run_dir / "assertions.json", []) or []
    log_tail = _read_ndjson(run_dir / "log.ndjson")[-30:]
    probe_summary = []
    probes_dir = run_dir / "probes"
    if probes_dir.is_dir():
        for path in sorted(probes_dir.glob("*.ndjson")):
            n = sum(1 for _ in path.open(encoding="utf-8"))
            probe_summary.append({"name": path.stem, "samples": n})
    cross_impl_md = None
    cross_impl_path = run_dir / "cross-impl-comparison.md"
    if cross_impl_path.is_file():
        cross_impl_md = cross_impl_path.read_text(encoding="utf-8")
    iteration_counts = _iteration_event_counts(run_dir)

    # Tamper check via forensic.verify. Imported lazily so the module
    # doesn't pull the forensic dependency at import time.
    from profile_manager import forensic
    try:
        verify_result = forensic.verify(run_id, runs_dir=base, state_dir=_state_dir(base))
        verify = {
            "ok": bool(verify_result.ok),
            "errors": [_structure_verify_error(e) for e in (verify_result.errors or [])],
        }
    except Exception as exc:  # noqa: BLE001 — defensive; never crash the inspector
        verify = {"ok": False, "errors": [_structure_verify_error(f"verify failed: {exc}")]}

    rs = manifest.get("resource_snapshot") or {}
    target = manifest.get("target") or {}
    scenario = manifest.get("scenario") or {}
    ass_summary = manifest.get("assertion_summary") or {}

    cross_impl_result = None
    if cross_impl_md:
        if "AGREED" in cross_impl_md:
            cross_impl_result = "AGREED"
        elif "DIVERGED" in cross_impl_md:
            cross_impl_result = "DIVERGED"

    return {
        "run_id": run_id,
        "scenario_id": scenario.get("id") or "",
        "target_implementation": target.get("implementation") or "",
        "target_version": target.get("version") or "",
        "runtime": manifest.get("runtime") or "",
        "exit_status": manifest.get("exit_status") or "",
        "started_at": manifest.get("started_at") or "",
        "ended_at": manifest.get("ended_at") or "",
        "actor": manifest.get("actor") or "",
        "seed": manifest.get("seed"),
        "profile": manifest.get("profile"),
        "assertion_summary": {
            "total": ass_summary.get("total", 0),
            "pass": ass_summary.get("pass", 0),
            "fail": ass_summary.get("fail", 0),
        },
        "wall_time_seconds": rs.get("wall_time_seconds"),
        "process_rss_delta_bytes": (rs.get("process_rss") or {}).get("delta_bytes"),
        "data_dir_delta_bytes": (rs.get("data_dir_disk") or {}).get("delta_bytes"),
        "iteration_counts": iteration_counts,
        "probes": probe_summary,
        "assertions": [
            {
                "primitive": a.get("primitive") or "",
                "params": a.get("params") or {},
                "evaluated_value": a.get("evaluated_value") or {},
                "evaluated_value_pretty": _pretty_format_payload(a.get("evaluated_value") or {}),
                "result": a.get("result") or "",
            }
            for a in assertions
        ],
        "log_tail": [
            {
                "ts": e.get("ts") or "",
                "phase": e.get("phase") or "",
                "primitive": e.get("primitive") or "",
                "event": e.get("event") or "",
                "payload": e.get("payload") or {},
                "payload_pretty": _pretty_format_payload(e.get("payload") or {}),
            }
            for e in log_tail
        ],
        "verify": verify,
        "chain": {
            "manifest_hash": chain.get("manifest_hash") or "",
            "prev_hash": chain.get("prev_hash") or "",
        },
        "explanation": _run_explanation(
            scenario_id=scenario.get("id") or "",
            target_implementation=target.get("implementation") or "",
            runtime=manifest.get("runtime") or "",
            exit_status=manifest.get("exit_status") or "",
            assertion_summary=ass_summary,
            verify_ok=bool(verify.get("ok")),
        ),
        "cross_impl": {
            "result": cross_impl_result,
            "markdown": cross_impl_md,
        },
        "bundle_url": f"/runs/{run_id}/bundle",
        "evidence_path": _safe_relative(run_dir, base.parent),
        # Slice 30 enrichments — three operator-facing sections that
        # surface ada3's bundle-workflow primitives without hiding the
        # absence-of-data state when those primitives have not been run
        # against this bundle yet.
        "substrate_evidence": _substrate_evidence_section(run_dir),
        "provenance": _provenance_section(run_dir),
        "export": _export_section(run_dir, run_id),
        "replay_artifact": _replay_artifact(run_dir),
        "diff_artifact": _diff_artifact(run_dir),
        "actions": _actions_section(run_id),
        # Item A (Phase 4.3 D-1) — thinness-suspicion banner data.
        "thinness_signals": _thinness_signals_section(run_dir),
        # Item C (Phase 4.3 D-1) — floor-preview re-evaluation tile.
        "floor_preview": _floor_preview_section(run_dir),
    }


def _floor_preview_section(run_dir: Path) -> dict[str, Any]:
    """Wrap floor_preview + summary so a single rule failure can never
    crash the inspector render. Cached at the floor_preview module."""
    from profile_manager.data.floor_preview import (
        floor_preview,
        floor_preview_summary,
    )
    try:
        rows = floor_preview(run_dir)
    except Exception:  # noqa: BLE001 — defensive
        rows = []
    return {"rows": rows, "summary": floor_preview_summary(rows)}


def _thinness_signals_section(run_dir: Path) -> list[dict[str, Any]]:
    """Wrap detect_thinness so a failure mode in the rules can never
    crash the inspector render."""
    from profile_manager.data.thinness_signals import detect_thinness
    try:
        return detect_thinness(run_dir)
    except Exception:  # noqa: BLE001
        return []
