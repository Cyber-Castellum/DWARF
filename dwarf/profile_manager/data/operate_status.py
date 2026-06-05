"""Pure data extractors for /operate/status — substrate health + config.

Slice 25 consolidates the legacy /architecture (live deployment topology
visualization) and /settings (config + active-profile table) into a
single read-only operator surface. All data flows through this module:
the view layer remains a render-only adapter.

Source of truth chain:
    substrate_health     -> data.health.<live_health, _health_from_body>
    active_profile_tile  -> data.profiles._profile_rows + payload.live.profile_id
    last_sync_tile       -> payload.generated_at + last_local_health.evidence_path
    dashboard_serving    -> token / port / bind passed in by the view caller
    configuration_rows   -> payload.config

Substrate health pill semantics:
    "ok"      : live SSH poll succeeded and all parsed counts present
    "stale"   : evidence is local-cached (live disabled or SSH unreachable)
    "error"   : poll attempted but returncode != 0 or counts missing

No fabrication: cells render the literal value from the source payload,
or "unknown" / "—" when the field is genuinely absent on disk.
"""
from __future__ import annotations

from typing import Any


_UNKNOWN = "unknown"
_DASH = "—"


def _safe_int(value: Any) -> int | None:
    if value in (None, "", _UNKNOWN):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def substrate_health(payload: dict[str, Any]) -> dict[str, Any]:
    """Top-tile + node-pill data for the live substrate.

    Returns:
        {
            "source":            "live" | "cached" | "missing",
            "state":             "ok" | "stale" | "error",
            "tip_block":         str,
            "sync_progress":     str,
            "node_processes":    int | None,
            "expected_nodes":    int | None,
            "socket_count":      int | None,
            "listener_count":    int | None,
            "loopback_only":     "true" | "false" | "unknown",
            "evidence_path":     str | None,
            "node_pills":        [{"name": "node1", "state": "ok"|"warn"}, ...]
        }

    State logic intentionally conservative: only "ok" when the live SSH
    poll completed (returncode == 0) AND parsed counts are present AND
    the running process count matches the active profile's expected
    node_count. Any deviation is "stale" or "error".
    """
    live = payload.get("live") or {}
    last_local = payload.get("last_local_health") or {}
    health = live.get("health") or last_local or {}
    parsed = health.get("parsed") or {}
    returncode = health.get("returncode")

    if live.get("enabled") and returncode == 0:
        source = "live"
    elif last_local.get("evidence_path"):
        source = "cached"
    else:
        source = "missing"

    node_processes = _safe_int(parsed.get("cardano_node_processes"))
    socket_count = _safe_int(parsed.get("socket_count"))
    listener_count = _safe_int(parsed.get("listener_count"))
    loopback_only = str(parsed.get("loopback_only") or _UNKNOWN).lower()

    active = active_profile(payload)
    expected_nodes = _safe_int(active.get("node_count"))

    if source == "missing":
        state = "error"
    elif source == "cached":
        state = "stale"
    elif node_processes is None or expected_nodes is None:
        state = "error"
    elif node_processes == expected_nodes:
        state = "ok"
    else:
        state = "stale"

    pills = []
    for idx in range(expected_nodes or 0):
        name = f"node{idx + 1}"
        if state == "ok":
            pill_state = "ok"
        elif state == "stale" and node_processes and idx < node_processes:
            pill_state = "ok"
        elif state == "stale":
            pill_state = "warn"
        else:
            pill_state = "error"
        pills.append({"name": name, "state": pill_state})

    return {
        "source": source,
        "state": state,
        "transport": live.get("transport"),
        "tip_block": parsed.get("tip_block") or _UNKNOWN,
        "sync_progress": parsed.get("sync_progress") or _UNKNOWN,
        "node_processes": node_processes,
        "expected_nodes": expected_nodes,
        "socket_count": socket_count,
        "listener_count": listener_count,
        "loopback_only": loopback_only,
        "evidence_path": health.get("evidence_path") or last_local.get("evidence_path"),
        "node_pills": pills,
    }


def active_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """Pick the profile referenced by payload.live.profile_id.

    Falls back to the first profile in payload.profiles if the live
    block omits a profile_id, and to an empty stub if there are no
    profiles at all.
    """
    profiles = payload.get("profiles") or []
    live_id = (payload.get("live") or {}).get("profile_id")
    if live_id:
        for p in profiles:
            if p.get("id") == live_id:
                return dict(p)
    if profiles:
        return dict(profiles[0])
    return {}


def active_profile_tile(payload: dict[str, Any]) -> dict[str, Any]:
    """Render-ready tile for the active profile (id, label, fleet shape)."""
    p = active_profile(payload)
    if not p:
        return {
            "id": _DASH,
            "label": "no profiles loaded",
            "node_type": _DASH,
            "node_count": _DASH,
            "peer_sharing": False,
            "remote_runtime_root": _DASH,
        }
    return {
        "id": p.get("id") or _DASH,
        "label": p.get("label") or _DASH,
        "node_type": p.get("node_type") or _DASH,
        "node_count": p.get("node_count") if p.get("node_count") is not None else _DASH,
        "peer_sharing": bool(p.get("peer_sharing")),
        "remote_runtime_root": p.get("remote_runtime_root") or _DASH,
    }


def last_sync_tile(payload: dict[str, Any]) -> dict[str, Any]:
    """Render-ready tile for the last sync timestamp + evidence path."""
    last_local = payload.get("last_local_health") or {}
    return {
        "generated_at": payload.get("generated_at") or _DASH,
        "evidence_path": last_local.get("evidence_path") or _DASH,
        "live_enabled": bool((payload.get("live") or {}).get("enabled")),
    }


def dashboard_serving_tile(*, port: int | None = None, bind: str | None = None,
                            token: str | None = None) -> dict[str, Any]:
    """Render-ready tile for the running dashboard's network surface.

    The view layer must pass in the live runtime values; nothing here
    introspects the running process. token redacted to a length-only
    hint so the page can be screenshot-shared without leaking the
    secret.
    """
    return {
        "port": port if port is not None else _DASH,
        "bind": bind or _DASH,
        "token_set": bool(token),
        "token_length": len(token) if token else 0,
    }


def configuration_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Configuration table rows. Each row is {label, value, copyable}.

    Rows:
        host                — config.host
        ssh_user            — config.ssh_user
        remote_base_path    — config.remote_base_path
        config_path         — config.path
        deployment_name     — config.deployment_name
        active_profile      — active profile id
        runtime_root        — active profile remote_runtime_root
    """
    cfg = payload.get("config") or {}
    profile = active_profile(payload)
    if not cfg.get("present"):
        return [
            {"label": "config", "value": cfg.get("message") or "Config missing", "copyable": False},
            {"label": "config path", "value": cfg.get("path") or _DASH, "copyable": True},
        ]
    return [
        {"label": "deployment", "value": cfg.get("deployment_name") or _DASH, "copyable": True},
        {"label": "host", "value": cfg.get("host") or _DASH, "copyable": True},
        {"label": "ssh user", "value": cfg.get("ssh_user") or _DASH, "copyable": True},
        {"label": "remote base path", "value": cfg.get("remote_base_path") or _DASH, "copyable": True},
        {"label": "config path", "value": cfg.get("path") or _DASH, "copyable": True},
        {"label": "active profile", "value": profile.get("id") or _DASH, "copyable": True},
        {"label": "runtime root", "value": profile.get("remote_runtime_root") or _DASH, "copyable": True},
    ]


def all_profiles(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return all profile rows enriched with an `is_active` flag."""
    live_id = (payload.get("live") or {}).get("profile_id")
    rows = []
    for p in payload.get("profiles") or []:
        rows.append({
            **p,
            "is_active": p.get("id") == live_id,
        })
    return rows
