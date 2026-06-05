"""Views for /learn/examples and /learn/getting-started (slice 1)."""
from __future__ import annotations

from profile_manager.data.learn_examples import list_examples
from profile_manager.templating import render


def render_learn_examples() -> str:
    examples = list_examples()
    return render(
        "learn/examples.j2",
        page_title="Examples",
        density="reading",
        active="learn",
        active_sub="examples",
        examples=examples,
    )


def render_learn_getting_started() -> str:
    return render(
        "learn/getting_started.j2",
        page_title="Getting started",
        density="reading",
        active="learn",
        active_sub="getting-started",
    )
