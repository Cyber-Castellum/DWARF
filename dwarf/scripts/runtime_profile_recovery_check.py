#!/usr/bin/env python3

import shutil
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_common import (  # noqa: E402
    PROFILE_A_CONFIG,
    directory_size_bytes,
    derive_chainsync_point,
    derive_range,
    point_slot,
    point_span,
    query_tip,
    start_session_with_details,
    replace_node_db,
    run_blockfetch,
    run_chainsync_fetch,
    stop_session_with_details,
    target_port,
    wait_for_all_tips,
    wait_for_all_tips_with_details,
    wait_for_node_slots,
    wait_for_node_slots_with_details,
)
from runtime_telemetry import emit_runtime_metric, emit_target_event  # noqa: E402

RUNTIME_ROOT = PROFILE_A_CONFIG.runtime_root
ENV_ROOT = PROFILE_A_CONFIG.env_root
NODE_NAMES = ("node1", "node2", "node3")
FETCH_TARGET_NODES = ("node2", "node3")
COPIED_STATE_NODE = "node2"
STALE_STATE_SOURCE_NODE = "node1"
REMEDIATION_SOURCE_NODE = "node3"
SYNTHETIC_STALE_SLOT_DELTA = 1500


def _emit_tip_metrics(label: str, tips: dict[str, dict]) -> None:
    emit_target_event(
        primitive="runtime_profile_recovery_check",
        event=f"{label}_tips",
        payload={node: {"slot": info["slot"], "block": info["block"]} for node, info in tips.items()},
    )
    for node, info in tips.items():
        emit_runtime_metric(f"{label}_{node}_slot", value=info["slot"], meta={"node": node, "kind": "slot"})
        emit_runtime_metric(f"{label}_{node}_block", value=info["block"], meta={"node": node, "kind": "block"})


def _emit_fetch_metric(label: str, *, node: str, protocol: str, success: bool, extra=None) -> None:
    payload = {"node": node, "protocol": protocol, "success": success}
    if extra:
        payload.update(extra)
    emit_target_event(
        primitive="runtime_profile_recovery_check",
        event=label,
        payload=payload,
        level="info" if success else "error",
    )
    emit_runtime_metric(
        f"{label}_{protocol}_{node}",
        value=1 if success else 0,
        meta=payload,
    )


def _emit_fetch_elapsed(label: str, *, node: str, protocol: str, elapsed_ms: float, phase: str | None = None) -> None:
    payload = {"node": node, "protocol": protocol, "elapsed_ms": elapsed_ms}
    if phase:
        payload["phase"] = phase
    emit_target_event(
        primitive="runtime_profile_recovery_check",
        event=f"{label}_fetch_elapsed",
        payload=payload,
    )
    emit_runtime_metric(
        f"{label}_{protocol}_{node}_elapsed_ms",
        value=elapsed_ms,
        meta=payload,
    )


def _emit_operation_details(label: str, details: dict) -> None:
    emit_target_event(
        primitive="runtime_profile_recovery_check",
        event=f"{label}_details",
        payload=details,
    )
    if "duration_seconds" in details:
        emit_runtime_metric(f"{label}_duration_seconds", value=details["duration_seconds"], meta={"kind": "duration"})
    if "attempts" in details:
        emit_runtime_metric(f"{label}_attempts", value=details["attempts"], meta={"kind": "attempts"})
    if "killed_pids" in details:
        emit_runtime_metric(f"{label}_killed_pid_count", value=len(details["killed_pids"]), meta={"kind": "count"})
    if "forced_kills" in details:
        emit_runtime_metric(f"{label}_forced_kill_count", value=len(details["forced_kills"]), meta={"kind": "count"})


def _emit_db_metrics(label: str, *, node: str, path: Path, role: str) -> int:
    size_bytes = directory_size_bytes(path)
    emit_target_event(
        primitive="runtime_profile_recovery_check",
        event=f"{label}_db_state",
        payload={"node": node, "role": role, "path": str(path), "size_bytes": size_bytes},
    )
    emit_runtime_metric(
        f"{label}_{role}_{node}_db_bytes",
        value=size_bytes,
        meta={"node": node, "role": role},
    )
    return size_bytes


def _emit_db_delta(label: str, *, node: str, left_role: str, left_bytes: int, right_role: str, right_bytes: int) -> None:
    delta = right_bytes - left_bytes
    emit_target_event(
        primitive="runtime_profile_recovery_check",
        event=f"{label}_db_delta",
        payload={
            "node": node,
            "left_role": left_role,
            "left_bytes": left_bytes,
            "right_role": right_role,
            "right_bytes": right_bytes,
            "delta_bytes": delta,
        },
    )
    emit_runtime_metric(
        f"{label}_{node}_db_delta_bytes",
        value=delta,
        meta={"node": node, "left_role": left_role, "right_role": right_role},
    )


def _emit_slot_gap(label: str, *, left_node: str, left_slot: int, right_node: str, right_slot: int) -> None:
    gap = right_slot - left_slot
    emit_target_event(
        primitive="runtime_profile_recovery_check",
        event=f"{label}_slot_gap",
        payload={"left_node": left_node, "left_slot": left_slot, "right_node": right_node, "right_slot": right_slot, "gap": gap},
    )
    emit_runtime_metric(f"{label}_slot_gap", value=gap, meta={"left_node": left_node, "right_node": right_node})


def _emit_slot_delta(label: str, *, node: str, baseline_slot: int, observed_slot: int, phase: str) -> None:
    delta = observed_slot - baseline_slot
    emit_target_event(
        primitive="runtime_profile_recovery_check",
        event=f"{label}_slot_delta",
        payload={
            "node": node,
            "baseline_slot": baseline_slot,
            "observed_slot": observed_slot,
            "delta": delta,
            "phase": phase,
        },
    )
    emit_runtime_metric(
        f"{label}_{node}_slot_delta",
        value=delta,
        meta={"node": node, "phase": phase},
    )


def _emit_point_metrics(label: str, *, point: str | None = None, from_point: str | None = None, to_point: str | None = None) -> None:
    payload: dict[str, object] = {}
    if point is not None:
        payload["point"] = point
        parsed_point_slot = point_slot(point)
        if parsed_point_slot is not None:
            payload["point_slot"] = parsed_point_slot
            emit_runtime_metric(
                f"{label}_point_slot",
                value=parsed_point_slot,
                meta={"kind": "point_slot"},
            )
    if from_point is not None and to_point is not None:
        payload["from_point"] = from_point
        payload["to_point"] = to_point
        parsed_from_slot = point_slot(from_point)
        parsed_to_slot = point_slot(to_point)
        parsed_slot_span = point_span(from_point, to_point)
        if parsed_from_slot is not None:
            payload["from_slot"] = parsed_from_slot
            emit_runtime_metric(
                f"{label}_from_slot",
                value=parsed_from_slot,
                meta={"kind": "from_slot"},
            )
        if parsed_to_slot is not None:
            payload["to_slot"] = parsed_to_slot
            emit_runtime_metric(
                f"{label}_to_slot",
                value=parsed_to_slot,
                meta={"kind": "to_slot"},
            )
        payload["slot_span"] = parsed_slot_span
        emit_runtime_metric(
            f"{label}_slot_span",
            value=parsed_slot_span,
            meta={"kind": "slot_span"},
        )
    emit_target_event(
        primitive="runtime_profile_recovery_check",
        event=f"{label}_points",
        payload=payload,
    )


def _prepare_stale_snapshot_for_recovery(
    *,
    label: str,
    baseline: dict[str, dict],
    source_db: Path,
    snapshot_db: Path,
) -> tuple[dict[str, dict], dict, dict]:
    baseline_tip = baseline[COPIED_STATE_NODE]
    source_tip = baseline[STALE_STATE_SOURCE_NODE]
    if source_tip["slot"] < baseline_tip["slot"]:
        return baseline, baseline_tip, source_tip

    shutil.copytree(source_db, snapshot_db)
    emit_target_event(
        primitive="runtime_profile_recovery_check",
        event=f"{label}_synthetic_stale_snapshot",
        payload={"source_node": STALE_STATE_SOURCE_NODE, "path": str(snapshot_db), "snapshot_slot": source_tip["slot"]},
    )

    required_slot = baseline_tip["slot"] + SYNTHETIC_STALE_SLOT_DELTA
    advanced_focus, advanced_wait = wait_for_node_slots_with_details(
        PROFILE_A_CONFIG,
        {COPIED_STATE_NODE: required_slot, REMEDIATION_SOURCE_NODE: required_slot},
        timeout_seconds=240,
    )
    if advanced_wait.get("timed_out"):
        raise RuntimeError(
            f"timed out waiting to synthesize stale snapshot for {COPIED_STATE_NODE}: "
            f"{advanced_wait.get('last_error')}"
        )
    _emit_operation_details(f"{label}_synthetic_stale_wait", advanced_wait)

    refreshed, refreshed_wait = wait_for_all_tips_with_details(PROFILE_A_CONFIG, NODE_NAMES, timeout_seconds=120)
    if refreshed_wait.get("timed_out"):
        raise RuntimeError(f"timed out waiting for all tips: {refreshed_wait.get('last_error')}")
    _emit_operation_details(f"{label}_synthetic_stale_baseline_wait", refreshed_wait)
    _emit_tip_metrics(f"{label}_synthetic_stale_baseline", refreshed)
    refreshed_tip = refreshed[COPIED_STATE_NODE]
    if source_tip["slot"] >= refreshed_tip["slot"]:
        raise RuntimeError(
            f"synthetic stale source {STALE_STATE_SOURCE_NODE} did not fall behind {COPIED_STATE_NODE}: "
            f"{source_tip['slot']} >= {refreshed_tip['slot']}"
        )
    if advanced_focus is not None:
        emit_target_event(
            primitive="runtime_profile_recovery_check",
            event=f"{label}_synthetic_stale_focus",
            payload=advanced_focus,
        )
    return refreshed, refreshed_tip, source_tip


def run_restart_recovery() -> int:
    baseline, baseline_wait = wait_for_all_tips_with_details(PROFILE_A_CONFIG, NODE_NAMES, timeout_seconds=90)
    if baseline_wait.get("timed_out"):
        raise RuntimeError(f"timed out waiting for all tips: {baseline_wait.get('last_error')}")
    _emit_operation_details("restart_baseline_wait", baseline_wait)
    _emit_tip_metrics("restart_baseline", baseline)
    baseline_slot = min(info["slot"] for info in baseline.values())
    _emit_operation_details("restart_stop", stop_session_with_details(PROFILE_A_CONFIG))
    _emit_operation_details("restart_start", start_session_with_details(PROFILE_A_CONFIG))
    recovered, recovered_wait = wait_for_all_tips_with_details(
        PROFILE_A_CONFIG,
        NODE_NAMES,
        min_slot=baseline_slot,
        timeout_seconds=180,
    )
    if recovered_wait.get("timed_out"):
        raise RuntimeError(f"timed out waiting for all tips: {recovered_wait.get('last_error')}")
    _emit_operation_details("restart_recovered_wait", recovered_wait)
    _emit_tip_metrics("restart_recovered", recovered)
    for node in NODE_NAMES:
        _emit_slot_delta(
            "restart_recovered",
            node=node,
            baseline_slot=baseline[node]["slot"],
            observed_slot=recovered[node]["slot"],
            phase="recovered",
        )
    print(
        "restart_recovery "
        f"baseline_node1_slot={baseline['node1']['slot']} "
        f"recovered_node1_slot={recovered['node1']['slot']} "
        f"baseline_node1_block={baseline['node1']['block']} "
        f"recovered_node1_block={recovered['node1']['block']}"
    )
    for node in NODE_NAMES:
        info = recovered[node]
        print(f"{node} slot={info['slot']} block={info['block']} syncProgress={info['syncProgress']}")
    return 0


def run_restart_recovery_fetch() -> int:
    baseline, baseline_wait = wait_for_all_tips_with_details(PROFILE_A_CONFIG, NODE_NAMES, timeout_seconds=90)
    if baseline_wait.get("timed_out"):
        raise RuntimeError(f"timed out waiting for all tips: {baseline_wait.get('last_error')}")
    _emit_operation_details("restart_fetch_baseline_wait", baseline_wait)
    _emit_tip_metrics("restart_fetch_baseline", baseline)
    baseline_focus = {node: baseline[node] for node in FETCH_TARGET_NODES}
    from_point, to_point = derive_range(PROFILE_A_CONFIG)
    _emit_point_metrics("restart_fetch_range", from_point=from_point, to_point=to_point)
    _emit_operation_details("restart_fetch_stop", stop_session_with_details(PROFILE_A_CONFIG))
    _emit_operation_details("restart_fetch_start", start_session_with_details(PROFILE_A_CONFIG))
    recovered, recovered_wait = wait_for_node_slots_with_details(
        PROFILE_A_CONFIG,
        {node: baseline_focus[node]["slot"] for node in FETCH_TARGET_NODES},
        timeout_seconds=180,
    )
    if recovered_wait.get("timed_out"):
        raise RuntimeError(f"timed out waiting for node slots: {recovered_wait.get('last_error')}")
    _emit_operation_details("restart_fetch_recovered_wait", recovered_wait)
    final_tips, final_wait = wait_for_all_tips_with_details(PROFILE_A_CONFIG, NODE_NAMES, timeout_seconds=90)
    if final_wait.get("timed_out"):
        raise RuntimeError(f"timed out waiting for all tips: {final_wait.get('last_error')}")
    _emit_operation_details("restart_fetch_final_wait", final_wait)
    _emit_tip_metrics("restart_fetch_recovered", final_tips)
    for node in FETCH_TARGET_NODES:
        _emit_slot_delta(
            "restart_fetch_recovered",
            node=node,
            baseline_slot=baseline_focus[node]["slot"],
            observed_slot=recovered[node]["slot"],
            phase="recovered",
        )
    fetch_successes = 0
    total_elapsed_ms = 0.0
    for node in FETCH_TARGET_NODES:
        started_at = time.monotonic()
        result = run_blockfetch(PROFILE_A_CONFIG, target_port(PROFILE_A_CONFIG, node), from_point, to_point)
        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        total_elapsed_ms += elapsed_ms
        _emit_fetch_elapsed("restart_fetch_blockfetch", node=node, protocol="blockfetch", elapsed_ms=elapsed_ms, phase="restored")
        if result.returncode != 0:
            _emit_fetch_metric("restart_fetch_blockfetch", node=node, protocol="blockfetch", success=False, extra={"exit_code": result.returncode})
            raise RuntimeError(f"post-restart blockfetch failed for {node} with exit {result.returncode}")
        fetch_successes += 1
        _emit_fetch_metric("restart_fetch_blockfetch", node=node, protocol="blockfetch", success=True, extra={"exit_code": result.returncode})
    emit_runtime_metric("restart_fetch_successes", value=fetch_successes, meta={"protocol": "blockfetch"})
    emit_runtime_metric("restart_fetch_total_elapsed_ms", value=total_elapsed_ms, meta={"protocol": "blockfetch"})
    print(
        "restart_recovery_fetch "
        f"baseline_node2_slot={baseline['node2']['slot']} "
        f"baseline_node3_slot={baseline['node3']['slot']} "
        f"recovered_node2_slot={recovered['node2']['slot']} "
        f"recovered_node3_slot={recovered['node3']['slot']} "
        f"from_point={from_point} "
        f"to_point={to_point} "
        f"fetch_successes={fetch_successes}"
    )
    for node in NODE_NAMES:
        info = final_tips[node]
        print(f"{node} slot={info['slot']} block={info['block']} syncProgress={info['syncProgress']}")
    return 0


def run_copied_state_recovery() -> int:
    baseline, baseline_wait = wait_for_all_tips_with_details(PROFILE_A_CONFIG, NODE_NAMES, timeout_seconds=90)
    if baseline_wait.get("timed_out"):
        raise RuntimeError(f"timed out waiting for all tips: {baseline_wait.get('last_error')}")
    _emit_operation_details("copied_state_baseline_wait", baseline_wait)
    _emit_tip_metrics("copied_state_baseline", baseline)
    source_db = ENV_ROOT / "node-data" / STALE_STATE_SOURCE_NODE / "db"
    target_db = ENV_ROOT / "node-data" / COPIED_STATE_NODE / "db"
    remediation_db = ENV_ROOT / "node-data" / REMEDIATION_SOURCE_NODE / "db"
    with tempfile.TemporaryDirectory(prefix="dwarf-stale-db-snapshot-") as tmp:
        snapshot_db = Path(tmp) / "stale-db"
        baseline, baseline_tip, source_tip = _prepare_stale_snapshot_for_recovery(
            label="copied_state",
            baseline=baseline,
            source_db=source_db,
            snapshot_db=snapshot_db,
        )
        source_db_bytes = _emit_db_metrics("copied_state", node=STALE_STATE_SOURCE_NODE, path=source_db, role="source")
        target_db_bytes = _emit_db_metrics("copied_state", node=COPIED_STATE_NODE, path=target_db, role="target")
        remediation_db_bytes = _emit_db_metrics("copied_state", node=REMEDIATION_SOURCE_NODE, path=remediation_db, role="remediation")
        _emit_db_delta(
            "copied_state_source_vs_target",
            node=COPIED_STATE_NODE,
            left_role="source",
            left_bytes=source_db_bytes,
            right_role="target",
            right_bytes=target_db_bytes,
        )
        _emit_db_delta(
            "copied_state_remediation_vs_target",
            node=COPIED_STATE_NODE,
            left_role="remediation",
            left_bytes=remediation_db_bytes,
            right_role="target",
            right_bytes=target_db_bytes,
        )
        _emit_operation_details("copied_state_stop", stop_session_with_details(PROFILE_A_CONFIG))
        if not snapshot_db.exists():
            shutil.copytree(source_db, snapshot_db)
        snapshot_db_bytes = _emit_db_metrics("copied_state", node=STALE_STATE_SOURCE_NODE, path=snapshot_db, role="snapshot")
        _emit_db_delta(
            "copied_state_snapshot_vs_source",
            node=COPIED_STATE_NODE,
            left_role="source",
            left_bytes=source_db_bytes,
            right_role="snapshot",
            right_bytes=snapshot_db_bytes,
        )
        replace_node_db(PROFILE_A_CONFIG, COPIED_STATE_NODE, snapshot_db)
        _emit_operation_details("copied_state_start", start_session_with_details(PROFILE_A_CONFIG))
        outcome = "non_recovery_within_bound"
        try:
            recovered, recovered_wait = wait_for_node_slots_with_details(
                PROFILE_A_CONFIG,
                {COPIED_STATE_NODE: baseline_tip["slot"]},
                timeout_seconds=120,
            )
            _emit_operation_details("copied_state_recovery_wait", recovered_wait)
            if (
                not recovered_wait.get("timed_out")
                and recovered is not None
                and recovered[COPIED_STATE_NODE]["slot"] >= baseline_tip["slot"]
            ):
                raise RuntimeError(
                    f"{COPIED_STATE_NODE} unexpectedly recovered to slot "
                    f"{recovered[COPIED_STATE_NODE]['slot']} within the bounded window"
                )
        except RuntimeError as exc:
            if "unexpectedly recovered" in str(exc):
                raise
        finally:
            _emit_operation_details("copied_state_remediation_stop", stop_session_with_details(PROFILE_A_CONFIG))
            replace_node_db(PROFILE_A_CONFIG, COPIED_STATE_NODE, remediation_db)
            _emit_operation_details("copied_state_remediation_start", start_session_with_details(PROFILE_A_CONFIG))
        restored_focus, restored_wait = wait_for_node_slots_with_details(
            PROFILE_A_CONFIG,
            {COPIED_STATE_NODE: baseline_tip["slot"], REMEDIATION_SOURCE_NODE: baseline_tip["slot"]},
            timeout_seconds=240,
        )
        if restored_wait.get("timed_out"):
            raise RuntimeError(f"timed out waiting for node slots: {restored_wait.get('last_error')}")
        _emit_operation_details("copied_state_restored_wait", restored_wait)
        restored, restored_all_wait = wait_for_all_tips_with_details(PROFILE_A_CONFIG, NODE_NAMES, timeout_seconds=120)
        if restored_all_wait.get("timed_out"):
            raise RuntimeError(f"timed out waiting for all tips: {restored_all_wait.get('last_error')}")
        _emit_operation_details("copied_state_restored_all_wait", restored_all_wait)
        restored_target_db_bytes = _emit_db_metrics("copied_state", node=COPIED_STATE_NODE, path=target_db, role="restored")
        _emit_db_delta(
            "copied_state_restored_vs_remediation",
            node=COPIED_STATE_NODE,
            left_role="remediation",
            left_bytes=remediation_db_bytes,
            right_role="restored",
            right_bytes=restored_target_db_bytes,
        )
    _emit_tip_metrics("copied_state_restored", restored)
    _emit_slot_delta(
        "copied_state_source_to_baseline",
        node=COPIED_STATE_NODE,
        baseline_slot=source_tip["slot"],
        observed_slot=baseline_tip["slot"],
        phase="baseline",
    )
    for node in (COPIED_STATE_NODE, REMEDIATION_SOURCE_NODE):
        _emit_slot_delta(
            "copied_state_restored",
            node=node,
            baseline_slot=baseline[node]["slot"],
            observed_slot=restored[node]["slot"],
            phase="restored",
        )
    print(
        "copied_state_bounded_negative "
        f"target_node={COPIED_STATE_NODE} "
        f"source_node={STALE_STATE_SOURCE_NODE} "
        f"remediation_source_node={REMEDIATION_SOURCE_NODE} "
        f"snapshot_slot={source_tip['slot']} "
        f"baseline_slot={baseline_tip['slot']} "
        f"outcome={outcome} "
        f"restored_slot={restored[COPIED_STATE_NODE]['slot']} "
        f"snapshot_block={source_tip['block']} "
        f"baseline_block={baseline_tip['block']} "
        f"restored_block={restored[COPIED_STATE_NODE]['block']}"
    )
    for node in NODE_NAMES:
        info = restored[node]
        print(f"{node} slot={info['slot']} block={info['block']} syncProgress={info['syncProgress']}")
    return 0


def run_copied_state_recovery_fetch() -> int:
    baseline, baseline_wait = wait_for_all_tips_with_details(PROFILE_A_CONFIG, NODE_NAMES, timeout_seconds=90)
    if baseline_wait.get("timed_out"):
        raise RuntimeError(f"timed out waiting for all tips: {baseline_wait.get('last_error')}")
    _emit_operation_details("copied_state_fetch_baseline_wait", baseline_wait)
    _emit_tip_metrics("copied_state_fetch_baseline", baseline)
    baseline_tip = baseline[COPIED_STATE_NODE]
    source_tip = baseline[STALE_STATE_SOURCE_NODE]
    from_point, to_point = derive_range(PROFILE_A_CONFIG)
    _emit_point_metrics("copied_state_fetch_range", from_point=from_point, to_point=to_point)
    if source_tip["slot"] >= baseline_tip["slot"]:
        raise RuntimeError(
            f"stale source {STALE_STATE_SOURCE_NODE} is not behind {COPIED_STATE_NODE}: "
            f"{source_tip['slot']} >= {baseline_tip['slot']}"
        )
    source_db = ENV_ROOT / "node-data" / STALE_STATE_SOURCE_NODE / "db"
    target_db = ENV_ROOT / "node-data" / COPIED_STATE_NODE / "db"
    remediation_db = ENV_ROOT / "node-data" / REMEDIATION_SOURCE_NODE / "db"
    with tempfile.TemporaryDirectory(prefix="dwarf-stale-db-snapshot-") as tmp:
        snapshot_db = Path(tmp) / "stale-db"
        source_db_bytes = _emit_db_metrics("copied_state_fetch", node=STALE_STATE_SOURCE_NODE, path=source_db, role="source")
        target_db_bytes = _emit_db_metrics("copied_state_fetch", node=COPIED_STATE_NODE, path=target_db, role="target")
        remediation_db_bytes = _emit_db_metrics("copied_state_fetch", node=REMEDIATION_SOURCE_NODE, path=remediation_db, role="remediation")
        _emit_db_delta("copied_state_fetch_source_vs_target", node=COPIED_STATE_NODE, left_role="source", left_bytes=source_db_bytes, right_role="target", right_bytes=target_db_bytes)
        _emit_db_delta("copied_state_fetch_remediation_vs_target", node=COPIED_STATE_NODE, left_role="remediation", left_bytes=remediation_db_bytes, right_role="target", right_bytes=target_db_bytes)
        _emit_operation_details("copied_state_fetch_stop", stop_session_with_details(PROFILE_A_CONFIG))
        shutil.copytree(source_db, snapshot_db)
        snapshot_db_bytes = _emit_db_metrics("copied_state_fetch", node=STALE_STATE_SOURCE_NODE, path=snapshot_db, role="snapshot")
        _emit_db_delta("copied_state_fetch_snapshot_vs_source", node=COPIED_STATE_NODE, left_role="source", left_bytes=source_db_bytes, right_role="snapshot", right_bytes=snapshot_db_bytes)
        replace_node_db(PROFILE_A_CONFIG, COPIED_STATE_NODE, snapshot_db)
        _emit_operation_details("copied_state_fetch_start", start_session_with_details(PROFILE_A_CONFIG))
        try:
            recovered, recovered_wait = wait_for_node_slots_with_details(
                PROFILE_A_CONFIG,
                {COPIED_STATE_NODE: baseline_tip["slot"]},
                timeout_seconds=120,
            )
            _emit_operation_details("copied_state_fetch_recovery_wait", recovered_wait)
            if (
                not recovered_wait.get("timed_out")
                and recovered is not None
                and recovered[COPIED_STATE_NODE]["slot"] >= baseline_tip["slot"]
            ):
                raise RuntimeError(
                    f"{COPIED_STATE_NODE} unexpectedly recovered to slot "
                    f"{recovered[COPIED_STATE_NODE]['slot']} within the bounded window"
                )
        except RuntimeError as exc:
            if "unexpectedly recovered" in str(exc):
                raise
        finally:
            _emit_operation_details("copied_state_fetch_remediation_stop", stop_session_with_details(PROFILE_A_CONFIG))
            replace_node_db(PROFILE_A_CONFIG, COPIED_STATE_NODE, remediation_db)
            _emit_operation_details("copied_state_fetch_remediation_start", start_session_with_details(PROFILE_A_CONFIG))
        restored_focus, restored_wait = wait_for_node_slots_with_details(
            PROFILE_A_CONFIG,
            {COPIED_STATE_NODE: baseline_tip["slot"], REMEDIATION_SOURCE_NODE: baseline_tip["slot"]},
            timeout_seconds=240,
        )
        if restored_wait.get("timed_out"):
            raise RuntimeError(f"timed out waiting for node slots: {restored_wait.get('last_error')}")
        _emit_operation_details("copied_state_fetch_restored_wait", restored_wait)
        restored, restored_all_wait = wait_for_all_tips_with_details(PROFILE_A_CONFIG, NODE_NAMES, timeout_seconds=120)
        if restored_all_wait.get("timed_out"):
            raise RuntimeError(f"timed out waiting for all tips: {restored_all_wait.get('last_error')}")
        _emit_operation_details("copied_state_fetch_restored_all_wait", restored_all_wait)
        restored_target_db_bytes = _emit_db_metrics("copied_state_fetch", node=COPIED_STATE_NODE, path=target_db, role="restored")
        _emit_db_delta("copied_state_fetch_restored_vs_remediation", node=COPIED_STATE_NODE, left_role="remediation", left_bytes=remediation_db_bytes, right_role="restored", right_bytes=restored_target_db_bytes)
    _emit_tip_metrics("copied_state_fetch_restored", restored)
    _emit_slot_delta("copied_state_fetch_source_to_baseline", node=COPIED_STATE_NODE, baseline_slot=source_tip["slot"], observed_slot=baseline_tip["slot"], phase="baseline")
    for node in (COPIED_STATE_NODE, REMEDIATION_SOURCE_NODE):
        _emit_slot_delta("copied_state_fetch_restored", node=node, baseline_slot=baseline[node]["slot"], observed_slot=restored[node]["slot"], phase="restored")
    fetch_successes = 0
    total_elapsed_ms = 0.0
    for node in FETCH_TARGET_NODES:
        started_at = time.monotonic()
        result = run_blockfetch(PROFILE_A_CONFIG, target_port(PROFILE_A_CONFIG, node), from_point, to_point)
        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        total_elapsed_ms += elapsed_ms
        _emit_fetch_elapsed("copied_state_fetch_blockfetch", node=node, protocol="blockfetch", elapsed_ms=elapsed_ms, phase="restored")
        if result.returncode != 0:
            _emit_fetch_metric("copied_state_fetch_blockfetch", node=node, protocol="blockfetch", success=False, extra={"exit_code": result.returncode})
            raise RuntimeError(f"post-remediation blockfetch failed for {node} with exit {result.returncode}")
        fetch_successes += 1
        _emit_fetch_metric("copied_state_fetch_blockfetch", node=node, protocol="blockfetch", success=True, extra={"exit_code": result.returncode})
    emit_runtime_metric("copied_state_fetch_successes", value=fetch_successes, meta={"protocol": "blockfetch"})
    emit_runtime_metric("copied_state_fetch_total_elapsed_ms", value=total_elapsed_ms, meta={"protocol": "blockfetch"})
    print(
        "copied_state_remediation_fetch "
        f"target_node={COPIED_STATE_NODE} "
        f"source_node={STALE_STATE_SOURCE_NODE} "
        f"remediation_source_node={REMEDIATION_SOURCE_NODE} "
        f"snapshot_slot={source_tip['slot']} "
        f"baseline_slot={baseline_tip['slot']} "
        f"restored_node2_slot={restored_focus[COPIED_STATE_NODE]['slot']} "
        f"restored_node3_slot={restored_focus[REMEDIATION_SOURCE_NODE]['slot']} "
        f"from_point={from_point} "
        f"to_point={to_point} "
        f"fetch_successes={fetch_successes}"
    )
    for node in NODE_NAMES:
        info = restored[node]
        print(f"{node} slot={info['slot']} block={info['block']} syncProgress={info['syncProgress']}")
    return 0


def run_copied_state_bounded_divergence() -> int:
    baseline, baseline_wait = wait_for_all_tips_with_details(PROFILE_A_CONFIG, NODE_NAMES, timeout_seconds=90)
    if baseline_wait.get("timed_out"):
        raise RuntimeError(f"timed out waiting for all tips: {baseline_wait.get('last_error')}")
    _emit_operation_details("copied_state_divergence_baseline_wait", baseline_wait)
    _emit_tip_metrics("copied_state_divergence_baseline", baseline)
    baseline_tip = baseline[COPIED_STATE_NODE]
    source_tip = baseline[STALE_STATE_SOURCE_NODE]
    from_point, to_point = derive_range(PROFILE_A_CONFIG)
    _emit_point_metrics("copied_state_divergence_range", from_point=from_point, to_point=to_point)
    if source_tip["slot"] >= baseline_tip["slot"]:
        raise RuntimeError(
            f"stale source {STALE_STATE_SOURCE_NODE} is not behind {COPIED_STATE_NODE}: "
            f"{source_tip['slot']} >= {baseline_tip['slot']}"
        )
    source_db = ENV_ROOT / "node-data" / STALE_STATE_SOURCE_NODE / "db"
    target_db = ENV_ROOT / "node-data" / COPIED_STATE_NODE / "db"
    remediation_db = ENV_ROOT / "node-data" / REMEDIATION_SOURCE_NODE / "db"
    with tempfile.TemporaryDirectory(prefix="dwarf-stale-db-snapshot-") as tmp:
        snapshot_db = Path(tmp) / "stale-db"
        source_db_bytes = _emit_db_metrics("copied_state_divergence", node=STALE_STATE_SOURCE_NODE, path=source_db, role="source")
        target_db_bytes = _emit_db_metrics("copied_state_divergence", node=COPIED_STATE_NODE, path=target_db, role="target")
        remediation_db_bytes = _emit_db_metrics("copied_state_divergence", node=REMEDIATION_SOURCE_NODE, path=remediation_db, role="remediation")
        _emit_db_delta("copied_state_divergence_source_vs_target", node=COPIED_STATE_NODE, left_role="source", left_bytes=source_db_bytes, right_role="target", right_bytes=target_db_bytes)
        _emit_db_delta("copied_state_divergence_remediation_vs_target", node=COPIED_STATE_NODE, left_role="remediation", left_bytes=remediation_db_bytes, right_role="target", right_bytes=target_db_bytes)
        _emit_operation_details("copied_state_divergence_stop", stop_session_with_details(PROFILE_A_CONFIG))
        shutil.copytree(source_db, snapshot_db)
        snapshot_db_bytes = _emit_db_metrics("copied_state_divergence", node=STALE_STATE_SOURCE_NODE, path=snapshot_db, role="snapshot")
        _emit_db_delta("copied_state_divergence_snapshot_vs_source", node=COPIED_STATE_NODE, left_role="source", left_bytes=source_db_bytes, right_role="snapshot", right_bytes=snapshot_db_bytes)
        replace_node_db(PROFILE_A_CONFIG, COPIED_STATE_NODE, snapshot_db)
        _emit_operation_details("copied_state_divergence_start", start_session_with_details(PROFILE_A_CONFIG))
        divergent, divergent_wait = wait_for_all_tips_with_details(PROFILE_A_CONFIG, NODE_NAMES, timeout_seconds=120)
        if divergent_wait.get("timed_out"):
            raise RuntimeError(f"timed out waiting for all tips: {divergent_wait.get('last_error')}")
        _emit_operation_details("copied_state_divergence_wait", divergent_wait)
        _emit_tip_metrics("copied_state_divergence_window", divergent)
        _emit_slot_gap(
            "copied_state_divergence_window",
            left_node=COPIED_STATE_NODE,
            left_slot=divergent[COPIED_STATE_NODE]["slot"],
            right_node=REMEDIATION_SOURCE_NODE,
            right_slot=divergent[REMEDIATION_SOURCE_NODE]["slot"],
        )
        if divergent[COPIED_STATE_NODE]["slot"] >= divergent[REMEDIATION_SOURCE_NODE]["slot"]:
            raise RuntimeError(
                f"expected bounded divergence with {COPIED_STATE_NODE} behind {REMEDIATION_SOURCE_NODE}, saw "
                f"{divergent[COPIED_STATE_NODE]['slot']} and {divergent[REMEDIATION_SOURCE_NODE]['slot']}"
            )
        divergence_fetch_successes = 0
        divergence_total_elapsed_ms = 0.0
        for node in FETCH_TARGET_NODES:
            started_at = time.monotonic()
            result = run_blockfetch(PROFILE_A_CONFIG, target_port(PROFILE_A_CONFIG, node), from_point, to_point)
            elapsed_ms = (time.monotonic() - started_at) * 1000.0
            divergence_total_elapsed_ms += elapsed_ms
            _emit_fetch_elapsed("copied_state_divergence_blockfetch", node=node, protocol="blockfetch", elapsed_ms=elapsed_ms, phase="divergence")
            if result.returncode != 0:
                _emit_fetch_metric("copied_state_divergence_blockfetch", node=node, protocol="blockfetch", success=False, extra={"exit_code": result.returncode, "phase": "divergence"})
                raise RuntimeError(f"divergence-window blockfetch failed for {node} with exit {result.returncode}")
            divergence_fetch_successes += 1
            _emit_fetch_metric("copied_state_divergence_blockfetch", node=node, protocol="blockfetch", success=True, extra={"exit_code": result.returncode, "phase": "divergence"})
        _emit_operation_details("copied_state_divergence_remediation_stop", stop_session_with_details(PROFILE_A_CONFIG))
        replace_node_db(PROFILE_A_CONFIG, COPIED_STATE_NODE, remediation_db)
        _emit_operation_details("copied_state_divergence_remediation_start", start_session_with_details(PROFILE_A_CONFIG))
        restored_focus, restored_wait = wait_for_node_slots_with_details(
            PROFILE_A_CONFIG,
            {COPIED_STATE_NODE: baseline_tip["slot"], REMEDIATION_SOURCE_NODE: baseline_tip["slot"]},
            timeout_seconds=240,
        )
        if restored_wait.get("timed_out"):
            raise RuntimeError(f"timed out waiting for node slots: {restored_wait.get('last_error')}")
        _emit_operation_details("copied_state_divergence_restored_wait", restored_wait)
        postrestore_fetch_successes = 0
        postrestore_total_elapsed_ms = 0.0
        for node in FETCH_TARGET_NODES:
            started_at = time.monotonic()
            result = run_blockfetch(PROFILE_A_CONFIG, target_port(PROFILE_A_CONFIG, node), from_point, to_point)
            elapsed_ms = (time.monotonic() - started_at) * 1000.0
            postrestore_total_elapsed_ms += elapsed_ms
            _emit_fetch_elapsed("copied_state_restore_blockfetch", node=node, protocol="blockfetch", elapsed_ms=elapsed_ms, phase="restored")
            if result.returncode != 0:
                _emit_fetch_metric("copied_state_restore_blockfetch", node=node, protocol="blockfetch", success=False, extra={"exit_code": result.returncode, "phase": "restored"})
                raise RuntimeError(f"post-restore blockfetch failed for {node} with exit {result.returncode}")
            postrestore_fetch_successes += 1
            _emit_fetch_metric("copied_state_restore_blockfetch", node=node, protocol="blockfetch", success=True, extra={"exit_code": result.returncode, "phase": "restored"})
        restored, restored_all_wait = wait_for_all_tips_with_details(PROFILE_A_CONFIG, NODE_NAMES, timeout_seconds=120)
        if restored_all_wait.get("timed_out"):
            raise RuntimeError(f"timed out waiting for all tips: {restored_all_wait.get('last_error')}")
        _emit_operation_details("copied_state_divergence_restored_all_wait", restored_all_wait)
        restored_target_db_bytes = _emit_db_metrics("copied_state_divergence", node=COPIED_STATE_NODE, path=target_db, role="restored")
        _emit_db_delta("copied_state_divergence_restored_vs_remediation", node=COPIED_STATE_NODE, left_role="remediation", left_bytes=remediation_db_bytes, right_role="restored", right_bytes=restored_target_db_bytes)
    _emit_tip_metrics("copied_state_divergence_restored", restored)
    _emit_slot_delta("copied_state_divergence_source_to_baseline", node=COPIED_STATE_NODE, baseline_slot=source_tip["slot"], observed_slot=baseline_tip["slot"], phase="baseline")
    for node in (COPIED_STATE_NODE, REMEDIATION_SOURCE_NODE):
        _emit_slot_delta("copied_state_divergence_window", node=node, baseline_slot=baseline[node]["slot"], observed_slot=divergent[node]["slot"], phase="divergence")
        _emit_slot_delta("copied_state_divergence_restored", node=node, baseline_slot=baseline[node]["slot"], observed_slot=restored[node]["slot"], phase="restored")
    emit_runtime_metric("copied_state_divergence_fetch_successes", value=divergence_fetch_successes, meta={"protocol": "blockfetch", "phase": "divergence"})
    emit_runtime_metric("copied_state_postrestore_fetch_successes", value=postrestore_fetch_successes, meta={"protocol": "blockfetch", "phase": "restored"})
    emit_runtime_metric("copied_state_divergence_total_elapsed_ms", value=divergence_total_elapsed_ms, meta={"protocol": "blockfetch", "phase": "divergence"})
    emit_runtime_metric("copied_state_postrestore_total_elapsed_ms", value=postrestore_total_elapsed_ms, meta={"protocol": "blockfetch", "phase": "restored"})
    print(
        "copied_state_bounded_divergence "
        f"target_node={COPIED_STATE_NODE} "
        f"source_node={STALE_STATE_SOURCE_NODE} "
        f"remediation_source_node={REMEDIATION_SOURCE_NODE} "
        f"snapshot_slot={source_tip['slot']} "
        f"baseline_slot={baseline_tip['slot']} "
        f"divergent_node2_slot={divergent[COPIED_STATE_NODE]['slot']} "
        f"divergent_node3_slot={divergent[REMEDIATION_SOURCE_NODE]['slot']} "
        f"from_point={from_point} "
        f"to_point={to_point} "
        f"divergence_fetch_successes={divergence_fetch_successes} "
        f"restored_node2_slot={restored_focus[COPIED_STATE_NODE]['slot']} "
        f"restored_node3_slot={restored_focus[REMEDIATION_SOURCE_NODE]['slot']} "
        f"postrestore_fetch_successes={postrestore_fetch_successes}"
    )
    for node in NODE_NAMES:
        info = restored[node]
        print(f"{node} slot={info['slot']} block={info['block']} syncProgress={info['syncProgress']}")
    return 0


def run_copied_state_chainsync_divergence() -> int:
    baseline, baseline_wait = wait_for_all_tips_with_details(PROFILE_A_CONFIG, NODE_NAMES, timeout_seconds=90)
    if baseline_wait.get("timed_out"):
        raise RuntimeError(f"timed out waiting for all tips: {baseline_wait.get('last_error')}")
    _emit_operation_details("copied_state_chainsync_baseline_wait", baseline_wait)
    _emit_tip_metrics("copied_state_chainsync_baseline", baseline)
    baseline_tip = baseline[COPIED_STATE_NODE]
    source_tip = baseline[STALE_STATE_SOURCE_NODE]
    point = derive_chainsync_point(PROFILE_A_CONFIG)
    _emit_point_metrics("copied_state_chainsync_point", point=point)
    if source_tip["slot"] >= baseline_tip["slot"]:
        raise RuntimeError(
            f"stale source {STALE_STATE_SOURCE_NODE} is not behind {COPIED_STATE_NODE}: "
            f"{source_tip['slot']} >= {baseline_tip['slot']}"
        )
    source_db = ENV_ROOT / "node-data" / STALE_STATE_SOURCE_NODE / "db"
    target_db = ENV_ROOT / "node-data" / COPIED_STATE_NODE / "db"
    remediation_db = ENV_ROOT / "node-data" / REMEDIATION_SOURCE_NODE / "db"
    with tempfile.TemporaryDirectory(prefix="dwarf-stale-db-snapshot-") as tmp:
        tmp_root = Path(tmp)
        snapshot_db = tmp_root / "stale-db"
        source_db_bytes = _emit_db_metrics("copied_state_chainsync", node=STALE_STATE_SOURCE_NODE, path=source_db, role="source")
        target_db_bytes = _emit_db_metrics("copied_state_chainsync", node=COPIED_STATE_NODE, path=target_db, role="target")
        remediation_db_bytes = _emit_db_metrics("copied_state_chainsync", node=REMEDIATION_SOURCE_NODE, path=remediation_db, role="remediation")
        _emit_db_delta("copied_state_chainsync_source_vs_target", node=COPIED_STATE_NODE, left_role="source", left_bytes=source_db_bytes, right_role="target", right_bytes=target_db_bytes)
        _emit_db_delta("copied_state_chainsync_remediation_vs_target", node=COPIED_STATE_NODE, left_role="remediation", left_bytes=remediation_db_bytes, right_role="target", right_bytes=target_db_bytes)
        _emit_operation_details("copied_state_chainsync_stop", stop_session_with_details(PROFILE_A_CONFIG))
        shutil.copytree(source_db, snapshot_db)
        snapshot_db_bytes = _emit_db_metrics("copied_state_chainsync", node=STALE_STATE_SOURCE_NODE, path=snapshot_db, role="snapshot")
        _emit_db_delta("copied_state_chainsync_snapshot_vs_source", node=COPIED_STATE_NODE, left_role="source", left_bytes=source_db_bytes, right_role="snapshot", right_bytes=snapshot_db_bytes)
        replace_node_db(PROFILE_A_CONFIG, COPIED_STATE_NODE, snapshot_db)
        _emit_operation_details("copied_state_chainsync_start", start_session_with_details(PROFILE_A_CONFIG))
        divergent, divergent_wait = wait_for_all_tips_with_details(PROFILE_A_CONFIG, NODE_NAMES, timeout_seconds=120)
        if divergent_wait.get("timed_out"):
            raise RuntimeError(f"timed out waiting for all tips: {divergent_wait.get('last_error')}")
        _emit_operation_details("copied_state_chainsync_divergence_wait", divergent_wait)
        _emit_tip_metrics("copied_state_chainsync_divergence_window", divergent)
        _emit_slot_gap(
            "copied_state_chainsync_divergence_window",
            left_node=COPIED_STATE_NODE,
            left_slot=divergent[COPIED_STATE_NODE]["slot"],
            right_node=REMEDIATION_SOURCE_NODE,
            right_slot=divergent[REMEDIATION_SOURCE_NODE]["slot"],
        )
        if divergent[COPIED_STATE_NODE]["slot"] >= divergent[REMEDIATION_SOURCE_NODE]["slot"]:
            raise RuntimeError(
                f"expected bounded divergence with {COPIED_STATE_NODE} behind {REMEDIATION_SOURCE_NODE}, saw "
                f"{divergent[COPIED_STATE_NODE]['slot']} and {divergent[REMEDIATION_SOURCE_NODE]['slot']}"
            )
        divergence_fetch_successes = 0
        divergence_total_elapsed_ms = 0.0
        for node in FETCH_TARGET_NODES:
            out_dir = tmp_root / f"chainsync-divergent-{node}"
            out_dir.mkdir(parents=True, exist_ok=True)
            started_at = time.monotonic()
            result = run_chainsync_fetch(PROFILE_A_CONFIG, target_port(PROFILE_A_CONFIG, node), point, out_dir)
            elapsed_ms = (time.monotonic() - started_at) * 1000.0
            divergence_total_elapsed_ms += elapsed_ms
            if result.returncode != 0:
                _emit_fetch_elapsed("copied_state_divergence_chainsync", node=node, protocol="chainsync", elapsed_ms=elapsed_ms, phase="divergence")
                _emit_fetch_metric("copied_state_divergence_chainsync", node=node, protocol="chainsync", success=False, extra={"exit_code": result.returncode, "phase": "divergence"})
                raise RuntimeError(f"divergence-window chainsync failed for {node} with exit {result.returncode}")
            count = len(list(out_dir.glob("header.*.cbor")))
            if count < 2:
                raise RuntimeError(f"divergence-window chainsync returned too few headers for {node}: {count}")
            divergence_fetch_successes += 1
            _emit_fetch_elapsed("copied_state_divergence_chainsync", node=node, protocol="chainsync", elapsed_ms=elapsed_ms, phase="divergence")
            _emit_fetch_metric("copied_state_divergence_chainsync", node=node, protocol="chainsync", success=True, extra={"phase": "divergence", "header_count": count})
        _emit_operation_details("copied_state_chainsync_remediation_stop", stop_session_with_details(PROFILE_A_CONFIG))
        replace_node_db(PROFILE_A_CONFIG, COPIED_STATE_NODE, remediation_db)
        _emit_operation_details("copied_state_chainsync_remediation_start", start_session_with_details(PROFILE_A_CONFIG))
        restored_focus, restored_wait = wait_for_node_slots_with_details(
            PROFILE_A_CONFIG,
            {COPIED_STATE_NODE: baseline_tip["slot"], REMEDIATION_SOURCE_NODE: baseline_tip["slot"]},
            timeout_seconds=240,
        )
        if restored_wait.get("timed_out"):
            raise RuntimeError(f"timed out waiting for node slots: {restored_wait.get('last_error')}")
        _emit_operation_details("copied_state_chainsync_restored_wait", restored_wait)
        postrestore_fetch_successes = 0
        postrestore_total_elapsed_ms = 0.0
        for node in FETCH_TARGET_NODES:
            out_dir = tmp_root / f"chainsync-restored-{node}"
            out_dir.mkdir(parents=True, exist_ok=True)
            started_at = time.monotonic()
            result = run_chainsync_fetch(PROFILE_A_CONFIG, target_port(PROFILE_A_CONFIG, node), point, out_dir)
            elapsed_ms = (time.monotonic() - started_at) * 1000.0
            postrestore_total_elapsed_ms += elapsed_ms
            if result.returncode != 0:
                _emit_fetch_elapsed("copied_state_restore_chainsync", node=node, protocol="chainsync", elapsed_ms=elapsed_ms, phase="restored")
                _emit_fetch_metric("copied_state_restore_chainsync", node=node, protocol="chainsync", success=False, extra={"exit_code": result.returncode, "phase": "restored"})
                raise RuntimeError(f"post-restore chainsync failed for {node} with exit {result.returncode}")
            count = len(list(out_dir.glob("header.*.cbor")))
            if count < 2:
                raise RuntimeError(f"post-restore chainsync returned too few headers for {node}: {count}")
            postrestore_fetch_successes += 1
            _emit_fetch_elapsed("copied_state_restore_chainsync", node=node, protocol="chainsync", elapsed_ms=elapsed_ms, phase="restored")
            _emit_fetch_metric("copied_state_restore_chainsync", node=node, protocol="chainsync", success=True, extra={"phase": "restored", "header_count": count})
        restored, restored_all_wait = wait_for_all_tips_with_details(PROFILE_A_CONFIG, NODE_NAMES, timeout_seconds=120)
        if restored_all_wait.get("timed_out"):
            raise RuntimeError(f"timed out waiting for all tips: {restored_all_wait.get('last_error')}")
        _emit_operation_details("copied_state_chainsync_restored_all_wait", restored_all_wait)
        restored_target_db_bytes = _emit_db_metrics("copied_state_chainsync", node=COPIED_STATE_NODE, path=target_db, role="restored")
        _emit_db_delta("copied_state_chainsync_restored_vs_remediation", node=COPIED_STATE_NODE, left_role="remediation", left_bytes=remediation_db_bytes, right_role="restored", right_bytes=restored_target_db_bytes)
    _emit_tip_metrics("copied_state_chainsync_restored", restored)
    _emit_slot_delta("copied_state_chainsync_source_to_baseline", node=COPIED_STATE_NODE, baseline_slot=source_tip["slot"], observed_slot=baseline_tip["slot"], phase="baseline")
    for node in (COPIED_STATE_NODE, REMEDIATION_SOURCE_NODE):
        _emit_slot_delta("copied_state_chainsync_divergence_window", node=node, baseline_slot=baseline[node]["slot"], observed_slot=divergent[node]["slot"], phase="divergence")
        _emit_slot_delta("copied_state_chainsync_restored", node=node, baseline_slot=baseline[node]["slot"], observed_slot=restored[node]["slot"], phase="restored")
    emit_runtime_metric("copied_state_divergence_chainsync_successes", value=divergence_fetch_successes, meta={"protocol": "chainsync", "phase": "divergence"})
    emit_runtime_metric("copied_state_postrestore_chainsync_successes", value=postrestore_fetch_successes, meta={"protocol": "chainsync", "phase": "restored"})
    emit_runtime_metric("copied_state_divergence_chainsync_total_elapsed_ms", value=divergence_total_elapsed_ms, meta={"protocol": "chainsync", "phase": "divergence"})
    emit_runtime_metric("copied_state_postrestore_chainsync_total_elapsed_ms", value=postrestore_total_elapsed_ms, meta={"protocol": "chainsync", "phase": "restored"})
    print(
        "copied_state_chainsync_divergence "
        f"target_node={COPIED_STATE_NODE} "
        f"source_node={STALE_STATE_SOURCE_NODE} "
        f"remediation_source_node={REMEDIATION_SOURCE_NODE} "
        f"snapshot_slot={source_tip['slot']} "
        f"baseline_slot={baseline_tip['slot']} "
        f"divergent_node2_slot={divergent[COPIED_STATE_NODE]['slot']} "
        f"divergent_node3_slot={divergent[REMEDIATION_SOURCE_NODE]['slot']} "
        f"point={point} "
        f"divergence_fetch_successes={divergence_fetch_successes} "
        f"restored_node2_slot={restored_focus[COPIED_STATE_NODE]['slot']} "
        f"restored_node3_slot={restored_focus[REMEDIATION_SOURCE_NODE]['slot']} "
        f"postrestore_fetch_successes={postrestore_fetch_successes}"
    )
    for node in NODE_NAMES:
        info = restored[node]
        print(f"{node} slot={info['slot']} block={info['block']} syncProgress={info['syncProgress']}")
    return 0


def main(argv):
    valid = {"restart", "restart-fetch", "copied-state", "copied-state-fetch", "copied-state-divergence", "copied-state-chainsync-divergence"}
    if len(argv) != 2 or argv[1] not in valid:
        print("usage: runtime_profile_recovery_check.py {restart|restart-fetch|copied-state|copied-state-fetch|copied-state-divergence|copied-state-chainsync-divergence}", file=sys.stderr)
        return 2
    if argv[1] == "restart":
        return run_restart_recovery()
    if argv[1] == "restart-fetch":
        return run_restart_recovery_fetch()
    if argv[1] == "copied-state":
        return run_copied_state_recovery()
    if argv[1] == "copied-state-divergence":
        return run_copied_state_bounded_divergence()
    if argv[1] == "copied-state-chainsync-divergence":
        return run_copied_state_chainsync_divergence()
    return run_copied_state_recovery_fetch()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
