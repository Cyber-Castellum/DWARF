"""Pure evidence-package catalog helpers for the dashboard data layer."""
from __future__ import annotations

from profile_manager.evidence_packages import load_evidence_packages


def _package_rows():
    return [
        {
            "id": package.id,
            "label": package.label,
            "run_state": "runnable" if package.runnable else "status-only",
            "status": package.status,
            "candidate_ids": package.candidate_ids,
            "blockers": package.blockers,
        }
        for package in load_evidence_packages()
    ]
