#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from bundle_chain_helpers import walk_bundle_audit_trail  # noqa: E402
from runtime_telemetry import emit_target_event  # noqa: E402


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
        return Path(run_dir) / "outputs" / "forensic-snapshot"
    return Path.cwd() / "outputs" / "forensic-snapshot"


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


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _bundle_manifest(run_dir: Path):
    return _load_json(run_dir / "manifest.json") or {}


def _collect_tag_index(runs_dir: Path) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for artifact_path in sorted(runs_dir.glob("*/outputs/bundle-tag/tags.json")):
        payload = _load_json(artifact_path)
        if not payload:
            continue
        target_run_id = payload.get("target_run_id")
        if not target_run_id:
            continue
        entry = dict(payload)
        entry["artifact_path"] = str(artifact_path)
        index.setdefault(str(target_run_id), []).append(entry)
    return index


def _bundle_tags(tag_records: list[dict]) -> list[str]:
    tags: set[str] = set()
    for record in tag_records:
        for tag in record.get("tags_added") or []:
            if tag:
                tags.add(str(tag))
    return sorted(tags)


def _select_seed_run_ids(*, requested_run_ids: list[str], tag_index: dict[str, list[dict]], tag_filters: list[str]) -> list[str]:
    if not tag_filters:
        return list(requested_run_ids)
    selected = []
    for run_id in requested_run_ids:
        tags = set(_bundle_tags(tag_index.get(run_id, [])))
        if all(tag in tags for tag in tag_filters):
            selected.append(run_id)
    return selected


def _gather_included_run_ids(*, runs_dir: Path, seed_run_ids: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for run_id in seed_run_ids:
        payload = walk_bundle_audit_trail(runs_dir, run_id)
        for node in payload.get("bundles") or []:
            bundle_id = str(node.get("run_id") or "")
            if not bundle_id or bundle_id in seen:
                continue
            seen.add(bundle_id)
            ordered.append(bundle_id)
    return ordered


def _bundle_manifest_entry(*, runs_dir: Path, run_id: str, tag_index: dict[str, list[dict]]) -> dict:
    chain = walk_bundle_audit_trail(runs_dir, run_id)
    node = next((item for item in chain.get("bundles") or [] if item.get("run_id") == run_id), None) or {}
    run_dir = runs_dir / run_id
    manifest = _bundle_manifest(run_dir)
    tag_records = tag_index.get(run_id, [])
    tags = _bundle_tags(tag_records)
    hash_anchor = None
    for record in tag_records:
        hash_anchor = record.get("hash_anchor")
        if hash_anchor:
            break
    if not hash_anchor:
        hash_anchor = node.get("scenario_yaml_sha256")
    return {
        "run_id": run_id,
        "scenario_id": node.get("scenario_id") or manifest.get("scenario", {}).get("id") or manifest.get("scenario_id"),
        "started_at": manifest.get("started_at"),
        "exit_status": manifest.get("exit_status"),
        "parent_run_id": node.get("parent_run_id"),
        "attestation_present": node.get("attestation_present"),
        "attestation_verdict": node.get("attestation_verdict"),
        "scenario_yaml_sha256": node.get("scenario_yaml_sha256"),
        "dwarf_source_sha256": node.get("dwarf_source_sha256"),
        "tooling_versions": node.get("tooling_versions"),
        "signing_key_fingerprint": node.get("signing_key_fingerprint"),
        "hash_anchor": hash_anchor,
        "tags": tags,
        "tag_records": [
            {
                "tagging_run_id": Path(record["artifact_path"]).parts[-4] if "artifact_path" in record else None,
                "tags_added": record.get("tags_added") or [],
                "tagged_at_utc": record.get("tagged_at_utc"),
                "signing_actor": record.get("signing_actor"),
                "hash_anchor": record.get("hash_anchor"),
            }
            for record in tag_records
        ],
    }


def _render_readme(payload: dict) -> str:
    lines = [
        "# Dwarf Forensic Snapshot",
        "",
        f"- Generated at: {payload['generated_at_utc']}",
        f"- Included bundles: {payload['included_bundle_count']}",
        f"- Requested bundles: {', '.join(payload['requested_run_ids']) or 'none'}",
        f"- Seed bundles after tag filter: {', '.join(payload['seed_run_ids']) or 'none'}",
        f"- Tarball root: snapshot/runs/",
        "",
        "## Verification",
        "",
        "1. Extract `snapshot.tar.gz` into a working directory.",
        "2. The included bundle tree will be under `snapshot/runs/`.",
        "3. To verify any included bundle's provenance chain, run:",
        "",
        "```bash",
        "./cardano-profile bundle audit-trail <run-id> --runs-dir <extracted>/snapshot/runs",
        "```",
        "",
        "4. Cross-check bundle hashes and attestation verdicts against `snapshot-manifest.json`.",
        "",
        "## Included Bundles",
        "",
        "| run_id | scenario_id | attestation | tags |",
        "| --- | --- | --- | --- |",
    ]
    for bundle in payload["bundles"]:
        lines.append(
            f"| {bundle['run_id']} | {bundle.get('scenario_id') or 'unknown'} | "
            f"{bundle.get('attestation_verdict') or 'unknown'} | {', '.join(bundle.get('tags') or []) or '-'} |"
        )
    return "\n".join(lines) + "\n"


def _write_tarball(*, snapshot_root: Path, tarball_path: Path, runs_dir: Path, included_run_ids: list[str], manifest_path: Path, readme_path: Path) -> None:
    with tarfile.open(tarball_path, "w:gz") as archive:
        archive.add(manifest_path, arcname=str(snapshot_root / "snapshot-manifest.json"))
        archive.add(readme_path, arcname=str(snapshot_root / "README.md"))
        for run_id in included_run_ids:
            archive.add(runs_dir / run_id, arcname=str(snapshot_root / "runs" / run_id), recursive=True)


def run_forensic_snapshot(
    *,
    runs_dir: Path,
    output_dir: Path,
    run_ids: list[str],
    tag_filters: list[str],
    output_format: str,
) -> dict:
    if not run_ids:
        raise ValueError("expected at least one run id")
    if output_format != "tar.gz":
        raise ValueError(f"unsupported output format: {output_format}")

    tag_index = _collect_tag_index(runs_dir)
    seed_run_ids = _select_seed_run_ids(
        requested_run_ids=list(run_ids),
        tag_index=tag_index,
        tag_filters=list(tag_filters),
    )
    if not seed_run_ids:
        raise ValueError("no run ids matched the requested tag filter")

    included_run_ids = _gather_included_run_ids(runs_dir=runs_dir, seed_run_ids=seed_run_ids)
    bundles = [_bundle_manifest_entry(runs_dir=runs_dir, run_id=run_id, tag_index=tag_index) for run_id in included_run_ids]

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "snapshot-manifest.json"
    readme_path = output_dir / "README.md"
    tarball_path = output_dir / "snapshot.tar.gz"
    payload = {
        "schema_version": "v1",
        "generated_at_utc": utc_timestamp(),
        "output_format": output_format,
        "requested_run_ids": list(run_ids),
        "seed_run_ids": list(seed_run_ids),
        "tag_filters": list(tag_filters),
        "included_run_ids": list(included_run_ids),
        "included_bundle_count": len(included_run_ids),
        "bundles": bundles,
    }
    readme_text = _render_readme(payload)
    readme_path.write_text(readme_text, encoding="utf-8")
    payload["readme_relpath"] = _relative_artifact_path(readme_path)
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    snapshot_root = Path("snapshot")
    _write_tarball(
        snapshot_root=snapshot_root,
        tarball_path=tarball_path,
        runs_dir=runs_dir,
        included_run_ids=included_run_ids,
        manifest_path=manifest_path,
        readme_path=readme_path,
    )
    payload["snapshot_relpath"] = _relative_artifact_path(tarball_path)
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Capture a frozen audit-handoff snapshot for selected Dwarf bundles")
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--run-id", action="append", dest="run_ids", required=True)
    parser.add_argument("--tag-filter", action="append", default=[])
    parser.add_argument("--output-format", default="tar.gz")
    args = parser.parse_args(argv[1:])

    runs_dir = infer_runs_dir(args.runs_dir)
    if runs_dir is None:
        print("missing runs dir", file=sys.stderr)
        return 1
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()

    emit_target_event(
        primitive="runtime_forensic_snapshot",
        event="forensic_snapshot_started",
        payload={
            "runs_dir": str(runs_dir),
            "output_dir": str(output_dir),
            "run_ids": list(args.run_ids or []),
            "tag_filters": list(args.tag_filter or []),
            "output_format": args.output_format,
        },
    )
    try:
        result = run_forensic_snapshot(
            runs_dir=runs_dir,
            output_dir=output_dir,
            run_ids=list(args.run_ids or []),
            tag_filters=list(args.tag_filter or []),
            output_format=str(args.output_format),
        )
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    emit_target_event(
        primitive="runtime_forensic_snapshot",
        event="forensic_snapshot_completed",
        payload=result,
    )
    print(
        "included_bundle_count={included_bundle_count} snapshot_relpath={snapshot_relpath} manifest_relpath={manifest_relpath}".format(
            included_bundle_count=result["included_bundle_count"],
            snapshot_relpath=result["snapshot_relpath"],
            manifest_relpath=_relative_artifact_path(output_dir / "snapshot-manifest.json"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
