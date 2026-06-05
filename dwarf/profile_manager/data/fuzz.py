"""Pure smoke/fuzz catalog helpers for the dashboard data layer."""
from __future__ import annotations

from profile_manager.fuzz import load_fuzz_tests
from profile_manager.smoke import load_smoke_tests


def _smoke_rows():
    return [
        {
            "id": smoke.id,
            "label": smoke.label,
            "category": smoke.category,
            "working_directory": smoke.working_directory,
            "timeout_seconds": smoke.timeout_seconds,
        }
        for smoke in load_smoke_tests()
    ]


def _fuzz_rows():
    return [
        {
            "id": fuzz.id,
            "label": fuzz.label,
            "category": fuzz.category,
            "target_package": fuzz.target_package,
            "safety_level": fuzz.safety_level,
            "requires_deployed_testnet": fuzz.requires_deployed_testnet,
            "related_candidates": fuzz.related_candidates,
        }
        for fuzz in load_fuzz_tests()
    ]
