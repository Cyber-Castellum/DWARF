"""Operator profile catalog for /operate/profiles.

Walks dwarf/profiles/*/profile.yaml defensively and returns enriched
dicts ready for template rendering. The discipline carried from slice 7:
malformed JSON is skipped silently per file; no exception escapes.

The _profile_url helper is the single source of truth for profile URLs.
Slice 10 (/operate/runs) and any later operator page must call this
helper rather than inlining /operate/profiles# strings.

Curated primary fields:
    id, label, node_type, node_count, amaru_node_count,
    network_magic, peer_sharing, remote_runtime_root, url

Optional fields (key absent when None):
    upstream_peer_address, listen_address, amaru_network

Deliberately omitted (anti-creep rail enforced by test):
    compose_project, config_source_dir
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from profile_manager.profiles import PROFILE_ROOT, Profile


def _profile_url(profile_id: str) -> str:
    """Single source of truth for profile detail URLs.

    Returns /operate/profiles#<profile_id> today; future
    /operate/profiles/<profile_id> sub-route migration is a one-line
    change to this function. Do NOT inline URL building elsewhere.
    """
    return f"/operate/profiles#{profile_id}"


def _enrich_profile(profile: Profile) -> dict[str, Any]:
    """Translate a Profile dataclass into a render-ready dict."""
    out: dict[str, Any] = {
        "id": profile.id,
        "label": profile.label,
        "node_type": profile.node_type,
        "node_count": profile.node_count,
        "amaru_node_count": profile.amaru_node_count,
        "network_magic": profile.network_magic,
        "peer_sharing": profile.peer_sharing,
        "remote_runtime_root": profile.remote_runtime_root,
        "url": _profile_url(profile.id),
    }
    if profile.upstream_peer_address is not None:
        out["upstream_peer_address"] = profile.upstream_peer_address
    if profile.listen_address is not None:
        out["listen_address"] = profile.listen_address
    if profile.amaru_network is not None:
        out["amaru_network"] = profile.amaru_network
    return out


def operate_profile_entries(profile_root: Path | None = None) -> list[dict[str, Any]]:
    """Walk profile_root for profile.yaml files; return enriched dicts.

    Defensive: each profile is parsed in its own try/except. A malformed
    JSON, a missing required key, or an unreadable file silently skips
    that profile from the listing — never raises. Mirrors slice-7's
    malformed-comparison-JSON discipline.

    Order matches the alphabetical id sort that load_profiles() produces.
    """
    root = profile_root if profile_root is not None else PROFILE_ROOT
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/profile.yaml")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            profile = Profile.from_dict(data)
        except (json.JSONDecodeError, KeyError, OSError, ValueError):
            continue
        out.append(_enrich_profile(profile))
    return out
