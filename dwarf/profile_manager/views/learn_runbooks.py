"""Views for /learn/operator-runbook, /learn/developer-onboarding,
and /learn/plugin-authoring-guide (dispatch 14)."""
from __future__ import annotations

from profile_manager.templating import render


def render_learn_operator_runbook() -> str:
    return render(
        "learn/operator_runbook.j2",
        page_title="Operator runbook",
        density="reading",
        active="learn",
        active_sub="operator-runbook",
    )


def render_learn_developer_onboarding() -> str:
    return render(
        "learn/developer_onboarding.j2",
        page_title="Developer onboarding",
        density="reading",
        active="learn",
        active_sub="developer-onboarding",
    )


def render_learn_plugin_authoring_guide() -> str:
    return render(
        "learn/plugin_authoring_guide.j2",
        page_title="Plugin authoring",
        density="reading",
        active="learn",
        active_sub="plugin-authoring",
    )
