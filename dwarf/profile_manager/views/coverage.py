"""Learn /coverage view."""
from __future__ import annotations

from profile_manager.data.coverage import (
    fault_family_coverage,
    fuzzer_backend_coverage,
    mini_protocol_coverage,
)
from profile_manager.templating import render


def render_learn_coverage() -> str:
    """Render /learn/coverage with three auto-derived matrices."""
    matrices = [
        mini_protocol_coverage(),
        fault_family_coverage(),
        fuzzer_backend_coverage(),
    ]
    return render(
        "learn/coverage.j2",
        page_title="Coverage",
        density="reading",
        layout="wide",        active="learn",
        active_sub="coverage",
        matrices=matrices,
    )
