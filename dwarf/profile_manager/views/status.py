"""Learn /status view."""
from __future__ import annotations

from profile_manager.data.status import (
    candidate_findings,
    current_phase_summary,
    data_source_used_filesystem_fallback,
    open_carry_overs,
    recent_main_commits,
    reset_data_source,
)
from profile_manager.templating import render


def render_learn_status() -> str:
    """Render /learn/status with four auto-derived sections."""
    reset_data_source()
    phase = current_phase_summary()
    recent = recent_main_commits(limit=20)
    candidates = candidate_findings()
    carry = open_carry_overs()
    fallback = data_source_used_filesystem_fallback()
    return render(
        "learn/status.j2",
        page_title="Status",
        density="reading",        active="learn",
        active_sub="status",
        phase=phase,
        recent_commits=recent,
        candidates=candidates,
        carry_overs=carry,
        data_source_caveat=fallback,
    )
