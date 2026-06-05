#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


TAG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def infer_runs_dir(explicit_runs_dir: str | None) -> Path | None:
    if explicit_runs_dir:
        return Path(explicit_runs_dir)
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir).parent
    env_runs_dir = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env_runs_dir:
        return Path(env_runs_dir)
    return None


def _default_output_dir() -> Path:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        return Path(run_dir) / "outputs" / "bundle-tag"
    return Path.cwd() / "outputs" / "bundle-tag"


def _relative_artifact_path(artifact_path: Path) -> str:
    run_dir = os.environ.get("ADA2_DWARF_RUN_DIR")
    if run_dir:
        try:
            return str(artifact_path.relative_to(Path(run_dir)))
        except ValueError:
            pass
    parts = artifact_path.parts
    if "outputs" in parts:
        index = parts.index("outputs")
        return str(Path(*parts[index:]))
    return str(artifact_path)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _normalize_tags(tags: list[str]) -> list[str]:
    normalized = []
    seen = set()
    for tag in tags:
        value = str(tag).strip()
        if not value:
            continue
        if not TAG_RE.fullmatch(value):
            raise ValueError(f"invalid tag {value!r}; expected slug shape [a-z0-9-]+")
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    if not normalized:
        raise ValueError("expected at least one valid tag")
    return normalized


def run_bundle_tag(*, runs_dir: Path, output_dir: Path, target_run_id: str, tags: list[str], signing_actor: str) -> dict:
    target_run_dir = runs_dir / target_run_id
    if not target_run_dir.is_dir():
        raise FileNotFoundError(f"missing run dir: {target_run_dir}")
    scenario_path = target_run_dir / "scenario.yaml"
    if not scenario_path.is_file():
        raise FileNotFoundError(f"missing scenario.yaml for target run: {scenario_path}")
    tags_added = _normalize_tags(tags)
    manifest = _load_json(target_run_dir / "manifest.json")
    payload = {
        "schema_version": "v1",
        "target_run_id": target_run_id,
        "target_run_dir": str(target_run_dir),
        "target_scenario_id": manifest.get("scenario", {}).get("id") or manifest.get("scenario_id"),
        "scenario_path": "scenario.yaml",
        "scenario_yaml_sha256": _sha256_file(scenario_path),
        "hash_anchor": _sha256_file(scenario_path),
        "tags_added": tags_added,
        "tagged_at_utc": utc_timestamp(),
        "signing_actor": signing_actor,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    tags_path = output_dir / "tags.json"
    tags_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    payload["tags_relpath"] = _relative_artifact_path(tags_path)
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Attach operator-defined slug tags to an existing bundle")
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--target-run-id", required=True)
    parser.add_argument("--tag", action="append", dest="tags", required=True)
    parser.add_argument("--signing-actor", default="dwarf")
    args = parser.parse_args(argv[1:])

    runs_dir = infer_runs_dir(args.runs_dir)
    if runs_dir is None:
        print("missing runs dir", file=sys.stderr)
        return 1
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()
    try:
        result = run_bundle_tag(
            runs_dir=runs_dir,
            output_dir=output_dir,
            target_run_id=str(args.target_run_id),
            tags=list(args.tags or []),
            signing_actor=str(args.signing_actor),
        )
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        "target_run_id={target_run_id} tags_count={tags_count} tags_relpath={tags_relpath}".format(
            target_run_id=result["target_run_id"],
            tags_count=len(result["tags_added"]),
            tags_relpath=result["tags_relpath"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
