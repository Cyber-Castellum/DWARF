from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class BackendArtifacts:
    """A backend's rendered output: relative-path -> content files plus a summary."""
    backend: str
    files: dict[str, str] = field(default_factory=dict)
    summary: dict = field(default_factory=dict)


class Backend(Protocol):
    name: str

    def render(self, profile, scenario=None) -> "BackendArtifacts":
        ...


def write_artifacts(artifacts: BackendArtifacts, out_dir) -> list[str]:
    """Write every file in `artifacts.files` under out_dir, creating parent dirs.

    Returns the sorted list of relative paths written.
    """
    out = Path(out_dir)
    written: list[str] = []
    for rel, content in artifacts.files.items():
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel)
    return sorted(written)
