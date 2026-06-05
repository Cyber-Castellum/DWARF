"""Views for /learn/glossary, /learn/faq, /learn/troubleshooting (slice 3 of dispatch 8)."""
from __future__ import annotations

from profile_manager.data.learn_docs import docs_payload
from profile_manager.templating import render


def render_learn_glossary() -> str:
    return render(
        "learn/glossary.j2",
        page_title="Glossary",
        density="reading",
        active="learn",
        active_sub="glossary",
        terms=docs_payload()["glossary"],
    )


def render_learn_faq() -> str:
    return render(
        "learn/faq.j2",
        page_title="FAQ",
        density="reading",
        active="learn",
        active_sub="faq",
        items=docs_payload()["faq"],
    )


def render_learn_troubleshooting() -> str:
    return render(
        "learn/troubleshooting.j2",
        page_title="Troubleshooting",
        density="reading",
        active="learn",
        active_sub="troubleshooting",
        items=docs_payload()["troubleshooting"],
    )
