"""Side-by-side substrate-aware compare for two run-id bundles.

The existing /operate/compare is metric-level / cross-impl-comparison
oriented. Substrate runs (compose / multi-node-observation /
byzantine-peer / hf-boundary / era-transition / genesis-mode) need a
TOPOLOGY-level diff: which node forked, which protocol diverged, what
flipped between baseline and adversarial.

This module re-uses operate_run_detail as the per-side extractor and
produces a structured comparison dict. The view layer renders it
without further computation.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _runs_dir() -> Path:
    env = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "runs"


def _safe_run_id(run_id: str) -> bool:
    return bool(run_id) and "/" not in run_id and ".." not in run_id


def _headline(detail: dict[str, Any]) -> dict[str, Any]:
    """Top-of-page summary fields a single run contributes to the diff."""
    return {
        "run_id": detail.get("run_id"),
        "scenario_id": detail.get("scenario_id") or "",
        "exit_status": detail.get("exit_status") or "",
        "target_implementation": detail.get("target_implementation") or "",
        "target_version": detail.get("target_version") or "",
        "runtime": detail.get("runtime") or "",
        "started_at": detail.get("started_at") or "",
        "ended_at": detail.get("ended_at") or "",
        "wall_time_seconds": detail.get("wall_time_seconds"),
        "assertion_summary": dict(detail.get("assertion_summary") or {}),
        "verify_ok": bool((detail.get("verify") or {}).get("ok")),
        "bundle_url": f"/operate/runs/{detail.get('run_id')}",
    }


def _assertion_key(assertion: dict[str, Any]) -> tuple[str, str]:
    """Identity for matching assertions across runs.

    The assertions stream carries (primitive, params) pairs where params
    is a small JSON-able dict. Key on (primitive, sorted-params-json) so
    e.g. two `tip_group_count` assertions with the same params land on
    the same row even when the runs differ.
    """
    import json
    primitive = assertion.get("primitive") or ""
    params = assertion.get("params") or {}
    try:
        params_key = json.dumps(params, sort_keys=True)
    except (TypeError, ValueError):
        params_key = str(params)
    return (primitive, params_key)


def _diff_assertions(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> dict[str, Any]:
    """Match assertions by (primitive, params) and surface flips.

    Returns ``{flipped, only_left, only_right, both_pass, both_fail}``
    counts plus a ``rows`` list of every union row with both sides'
    results — the template can render the table directly.
    """
    left_map: dict[tuple, dict[str, Any]] = {}
    for a in left:
        left_map[_assertion_key(a)] = a
    right_map: dict[tuple, dict[str, Any]] = {}
    for a in right:
        right_map[_assertion_key(a)] = a
    keys = sorted(set(left_map) | set(right_map))
    rows: list[dict[str, Any]] = []
    flipped = 0
    only_left = 0
    only_right = 0
    both_pass = 0
    both_fail = 0
    for k in keys:
        l = left_map.get(k)
        r = right_map.get(k)
        l_result = (l or {}).get("result") or "—"
        r_result = (r or {}).get("result") or "—"
        if l is None:
            kind = "only_right"
            only_right += 1
        elif r is None:
            kind = "only_left"
            only_left += 1
        elif l_result != r_result:
            kind = "flipped"
            flipped += 1
        elif l_result == "pass":
            kind = "both_pass"
            both_pass += 1
        else:
            kind = "both_fail"
            both_fail += 1
        rows.append({
            "primitive": k[0],
            "params_json": k[1],
            "left_result": l_result,
            "right_result": r_result,
            "kind": kind,
        })
    return {
        "rows": rows,
        "flipped": flipped,
        "only_left": only_left,
        "only_right": only_right,
        "both_pass": both_pass,
        "both_fail": both_fail,
    }


def _diff_topology_tile(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Per-node delta for the substrate-compose topology tile."""
    left_nodes = {n.get("id"): n for n in ((left or {}).get("nodes") or [])}
    right_nodes = {n.get("id"): n for n in ((right or {}).get("nodes") or [])}
    ids = sorted(set(left_nodes) | set(right_nodes))
    nodes: list[dict[str, Any]] = []
    for nid in ids:
        l = left_nodes.get(nid)
        r = right_nodes.get(nid)
        nodes.append({
            "id": nid,
            "left_impl": (l or {}).get("impl"),
            "right_impl": (r or {}).get("impl"),
            "left_role": (l or {}).get("role"),
            "right_role": (r or {}).get("role"),
            "left_healthy": (l or {}).get("healthy"),
            "right_healthy": (r or {}).get("healthy"),
            "role_changed": (l or {}).get("role") != (r or {}).get("role") and l is not None and r is not None,
            "health_changed": (l or {}).get("healthy") != (r or {}).get("healthy") and l is not None and r is not None,
            "only_left": l is not None and r is None,
            "only_right": r is not None and l is None,
        })
    return {
        "left_present": left is not None,
        "right_present": right is not None,
        "left_verdict": (left or {}).get("verdict"),
        "right_verdict": (right or {}).get("verdict"),
        "verdict_changed": (left or {}).get("verdict") != (right or {}).get("verdict") and left is not None and right is not None,
        "nodes": nodes,
    }


def _diff_multi_node_observation(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Per-node tip_group_count delta — flags nodes that forked."""
    left_nodes = {n.get("node_id"): n for n in ((left or {}).get("nodes") or [])}
    right_nodes = {n.get("node_id"): n for n in ((right or {}).get("nodes") or [])}
    ids = sorted(set(left_nodes) | set(right_nodes))
    nodes: list[dict[str, Any]] = []
    for nid in ids:
        l = left_nodes.get(nid) or {}
        r = right_nodes.get(nid) or {}
        l_groups = l.get("tip_group_count") or 0
        r_groups = r.get("tip_group_count") or 0
        nodes.append({
            "node_id": nid,
            "left_tip_group_count": l_groups,
            "right_tip_group_count": r_groups,
            "left_latest_slot": l.get("latest_slot"),
            "right_latest_slot": r.get("latest_slot"),
            "diverged_left": l_groups > 1,
            "diverged_right": r_groups > 1,
            "diverged_changed": (l_groups > 1) != (r_groups > 1),
        })
    return {
        "left_present": left is not None,
        "right_present": right is not None,
        "left_verdict": (left or {}).get("verdict"),
        "right_verdict": (right or {}).get("verdict"),
        "verdict_changed": (left or {}).get("verdict") != (right or {}).get("verdict") and left is not None and right is not None,
        "nodes": nodes,
    }


def _diff_byzantine_peer(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "left_present": left is not None,
        "right_present": right is not None,
        "left_verdict": (left or {}).get("verdict"),
        "right_verdict": (right or {}).get("verdict"),
        "left_intercepted": (left or {}).get("intercepted_segments") or 0,
        "right_intercepted": (right or {}).get("intercepted_segments") or 0,
        "left_mutated": (left or {}).get("mutated_segments") or 0,
        "right_mutated": (right or {}).get("mutated_segments") or 0,
        "left_target": (left or {}).get("target_node_id"),
        "right_target": (right or {}).get("target_node_id"),
    }


def _diff_hf_boundary(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    left_versions = (left or {}).get("node_protocol_versions") or {}
    right_versions = (right or {}).get("node_protocol_versions") or {}
    ids = sorted(set(left_versions) | set(right_versions))
    nodes = []
    for nid in ids:
        l_v = left_versions.get(nid)
        r_v = right_versions.get(nid)
        nodes.append({
            "node_id": nid,
            "left_version": l_v,
            "right_version": r_v,
            "version_changed": l_v != r_v and l_v is not None and r_v is not None,
        })
    return {
        "left_present": left is not None,
        "right_present": right is not None,
        "left_verdict": (left or {}).get("verdict"),
        "right_verdict": (right or {}).get("verdict"),
        "left_converged": (left or {}).get("converged"),
        "right_converged": (right or {}).get("converged"),
        "nodes": nodes,
    }


def _diff_era_transition(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "left_present": left is not None,
        "right_present": right is not None,
        "left_verdict": (left or {}).get("verdict"),
        "right_verdict": (right or {}).get("verdict"),
        "left_pre_observed": (left or {}).get("pre_observed"),
        "right_pre_observed": (right or {}).get("pre_observed"),
        "left_post_observed": (left or {}).get("post_observed"),
        "right_post_observed": (right or {}).get("post_observed"),
        "pre_changed": (left or {}).get("pre_observed") != (right or {}).get("pre_observed") and left is not None and right is not None,
        "post_changed": (left or {}).get("post_observed") != (right or {}).get("post_observed") and left is not None and right is not None,
    }


def _diff_genesis_mode(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "left_present": left is not None,
        "right_present": right is not None,
        "left_verdict": (left or {}).get("verdict"),
        "right_verdict": (right or {}).get("verdict"),
        "left_final_mode": (left or {}).get("final_mode"),
        "right_final_mode": (right or {}).get("final_mode"),
        "left_capture": (left or {}).get("peer_set_capture_detected"),
        "right_capture": (right or {}).get("peer_set_capture_detected"),
        "capture_changed": (left or {}).get("peer_set_capture_detected") != (right or {}).get("peer_set_capture_detected"),
    }


def compare_runs(left_id: str, right_id: str, *, runs_dir: Path | None = None) -> dict[str, Any] | None:
    """Build the full side-by-side diff payload, or None if either run
    is missing / un-readable. Empty / blank inputs return None too — the
    view layer renders a not-found state for that case."""
    if not _safe_run_id(left_id) or not _safe_run_id(right_id):
        return None
    from profile_manager.data.operate_run import operate_run_detail

    base = Path(runs_dir) if runs_dir is not None else _runs_dir()
    left_detail = operate_run_detail(left_id, runs_dir=base)
    right_detail = operate_run_detail(right_id, runs_dir=base)
    if left_detail is None or right_detail is None:
        return {
            "left_run_id": left_id,
            "right_run_id": right_id,
            "left_present": left_detail is not None,
            "right_present": right_detail is not None,
            "headlines": None,
            "assertions": None,
            "tiles": {},
            "tile_order": [],
        }

    left_tiles = (left_detail.get("substrate_evidence") or {}).get("tiles") or {}
    right_tiles = (right_detail.get("substrate_evidence") or {}).get("tiles") or {}
    tile_diffs: dict[str, Any] = {}
    for name, differ in [
        ("topology", _diff_topology_tile),
        ("multi_node_observation", _diff_multi_node_observation),
        ("byzantine_peer", _diff_byzantine_peer),
        ("hf_boundary", _diff_hf_boundary),
        ("era_transition", _diff_era_transition),
        ("genesis_mode", _diff_genesis_mode),
    ]:
        l = left_tiles.get(name)
        r = right_tiles.get(name)
        if l is None and r is None:
            continue
        tile_diffs[name] = differ(l, r)

    return {
        "left_run_id": left_id,
        "right_run_id": right_id,
        "left_present": True,
        "right_present": True,
        "headlines": {
            "left": _headline(left_detail),
            "right": _headline(right_detail),
        },
        "assertions": _diff_assertions(
            left_detail.get("assertions") or [],
            right_detail.get("assertions") or [],
        ),
        "tiles": tile_diffs,
        "tile_order": list(tile_diffs.keys()),
    }
