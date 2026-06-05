"""Learn /concepts view."""
from __future__ import annotations

from profile_manager.data.concepts import CONCEPTS
from profile_manager.templating import render


def render_learn_concepts() -> str:
    """Render the /learn/concepts glossary page.

    Sorts the hand-authored catalog alphabetically by slug at render
    time so display order is deterministic and independent of catalog
    insertion order.
    """
    concepts = sorted(CONCEPTS, key=lambda e: e["slug"])
    return render(
        "learn/concepts.j2",
        page_title="Concepts",
        density="reading",        active="learn",
        active_sub="concepts",
        concepts=concepts,
    )
