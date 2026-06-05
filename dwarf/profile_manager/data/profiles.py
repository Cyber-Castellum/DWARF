"""Pure profile-catalog helpers for the dashboard data layer."""
from __future__ import annotations

from profile_manager.profiles import load_profiles


def _profile_rows():
    return [
        {
            "id": profile.id,
            "label": profile.label,
            "node_type": profile.node_type,
            "node_count": profile.node_count,
            "peer_sharing": profile.peer_sharing,
            "remote_runtime_root": profile.remote_runtime_root,
        }
        for profile in load_profiles()
    ]
