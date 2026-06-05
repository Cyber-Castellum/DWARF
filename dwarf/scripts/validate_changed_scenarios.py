#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def collect_changed_scenarios(paths: list[str], repo_root: Path) -> list[Path]:
    seen: set[Path] = set()
    scenarios: list[Path] = []
    for raw in paths:
        path = (repo_root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        if path.suffix not in {".yaml", ".yml"}:
            continue
        try:
            rel = path.relative_to(repo_root)
        except ValueError:
            continue
        if rel.parts[:2] != ("dwarf", "scenarios"):
            continue
        if path not in seen:
            seen.add(path)
            scenarios.append(path)
    return scenarios


def validate_scenarios(paths: list[Path], repo_root: Path, env: dict[str, str] | None = None) -> int:
    if not paths:
        print("no changed scenarios to validate")
        return 0
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    cardano_profile = repo_root / "dwarf" / "cardano-profile"
    for path in paths:
        cmd = [str(cardano_profile), "scenario", "validate", str(path)]
        result = subprocess.run(cmd, cwd=repo_root / "dwarf", env=merged_env)
        if result.returncode != 0:
            return result.returncode
    return 0


def main(argv: list[str]) -> int:
    scenarios = collect_changed_scenarios(argv, REPO_ROOT)
    return validate_scenarios(scenarios, repo_root=REPO_ROOT)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
