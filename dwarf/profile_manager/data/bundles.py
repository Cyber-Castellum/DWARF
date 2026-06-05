"""Pure forensic-bundle and evidence-row helpers for the dashboard data layer."""
from __future__ import annotations

import os
from pathlib import Path

from profile_manager.data.files import _latest_files


def _latest_evidence_rows():
    from profile_manager.dashboard import PROJECT_ROOT

    return [
        {
            "path": str(path.relative_to(PROJECT_ROOT)),
            "size_kib": int(path.stat().st_size / 1024),
        }
        for path in _latest_files(PROJECT_ROOT / "agent" / "testing", ["**/*.md", "**/*.json"], count=12)
    ]


def _forensic_bundles_dir():
    env = os.environ.get("ADA2_DWARF_BUNDLES_DIR")
    if env:
        return Path(env)
    # data/bundles.py -> data/ -> profile_manager/ -> dwarf/
    return Path(__file__).resolve().parents[2] / "bundles"
