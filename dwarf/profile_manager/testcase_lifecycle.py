import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

PROMOTION_STATES = {"candidate", "validated", "finding"}


def _stable_case_id(*parts) -> str:
    text = "|".join(str(part) for part in parts)
    return "tc-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def default_state_dir_for_run(run_dir: Path) -> Path:
    return run_dir.parent.parent / "state"


def _normalized_replay_outcome(record: dict) -> str:
    replay_targets = list(record.get("replay_targets", []))
    replay_results = list(record.get("replay_results", []))
    if not replay_targets:
        return "none"
    statuses = {}
    for result in replay_results:
        target = result.get("target")
        exit_status = result.get("exit_status") or "unknown"
        if target:
            statuses[target] = exit_status
    parts = []
    for target in sorted(replay_targets):
        parts.append(f"{target}:{statuses.get(target, 'pending')}")
    return "|".join(parts)


def _normalized_replay_behavior_signatures(record: dict) -> str:
    replay_targets = list(record.get("replay_targets", []))
    replay_results = list(record.get("replay_results", []))
    if not replay_targets:
        return "none"
    signatures = {}
    for result in replay_results:
        target = result.get("target")
        signature = result.get("behavior_summary", {}).get("signature")
        if target and signature:
            signatures[target] = signature
    parts = []
    for target in sorted(replay_targets):
        parts.append(f"{target}:{signatures.get(target, 'pending')}")
    return "|".join(parts)


def _normalized_replay_resource_signatures(record: dict) -> str:
    replay_targets = list(record.get("replay_targets", []))
    replay_results = list(record.get("replay_results", []))
    if not replay_targets:
        return "none"
    signatures = {}
    for result in replay_results:
        target = result.get("target")
        signature = result.get("resource_summary", {}).get("signature")
        if target and signature:
            signatures[target] = signature
    parts = []
    for target in sorted(replay_targets):
        parts.append(f"{target}:{signatures.get(target, 'pending')}")
    return "|".join(parts)


def _normalized_compare_outcome(record: dict) -> str:
    compare_state = record.get("compare_state")
    if compare_state in (None, "none"):
        return "none"
    compare_result = record.get("compare_result")
    if not compare_result:
        return compare_state or "pending"
    return "agreed" if compare_result.get("agreed") else "diverged"


def _normalized_compare_run_outcomes(record: dict) -> str:
    compare_result = record.get("compare_result") or {}
    run_outcomes = compare_result.get("run_outcomes") or {}
    if not run_outcomes:
        return "none"
    parts = []
    for implementation in ("amaru", "cardano-node"):
        if implementation in run_outcomes:
            parts.append(f"{implementation}:{run_outcomes[implementation]}")
    return "|".join(parts) if parts else "none"


def _normalized_compare_behavior_signatures(record: dict) -> str:
    compare_result = record.get("compare_result") or {}
    behavior_summaries = compare_result.get("behavior_summaries") or {}
    if not behavior_summaries:
        return "none"
    parts = []
    for implementation in ("amaru", "cardano-node"):
        signature = (behavior_summaries.get(implementation) or {}).get("signature")
        if signature:
            parts.append(f"{implementation}:{signature}")
    return "|".join(parts) if parts else "none"


def _normalized_compare_resource_signatures(record: dict) -> str:
    compare_result = record.get("compare_result") or {}
    resource_summaries = compare_result.get("resource_summaries") or {}
    if not resource_summaries:
        return "none"
    parts = []
    for implementation in ("amaru", "cardano-node"):
        signature = (resource_summaries.get(implementation) or {}).get("signature")
        if signature:
            parts.append(f"{implementation}:{signature}")
    return "|".join(parts) if parts else "none"


def _normalized_source_signature(record: dict) -> str:
    classification = record.get("classification") or "unknown"
    reason = record.get("triage_reason") or "unknown"
    metadata = record.get("metadata") or {}
    if classification == "crash" and metadata.get("sig"):
        return f"{classification}:{reason}:sig={metadata['sig']}"
    if classification == "hang" and metadata.get("id"):
        return f"{classification}:{reason}:id={metadata['id']}"
    if classification == "queue" and metadata.get("id"):
        return f"{classification}:{reason}:id={metadata['id']}"
    return f"{classification}:{reason}"


def build_bucket_signature(record: dict) -> dict:
    return {
        "version": "v2",
        "producer": record.get("producer"),
        "classification": record.get("classification"),
        "triage_reason": record.get("triage_reason"),
        "source_signature": _normalized_source_signature(record),
        "target_implementation": record.get("target_implementation"),
        "replay_target_id": infer_replay_target_id(record),
        "replay_outcome": _normalized_replay_outcome(record),
        "replay_behavior_signatures": _normalized_replay_behavior_signatures(record),
        "replay_resource_signatures": _normalized_replay_resource_signatures(record),
        "compare_outcome": _normalized_compare_outcome(record),
        "compare_run_outcomes": _normalized_compare_run_outcomes(record),
        "compare_behavior_signatures": _normalized_compare_behavior_signatures(record),
        "compare_resource_signatures": _normalized_compare_resource_signatures(record),
    }


def _bucket_id_from_signature(signature: dict) -> str:
    return "tb-" + hashlib.sha256(
        json.dumps(signature, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def _enrich_record_with_bucket(record: dict) -> dict:
    enriched = dict(record)
    signature = build_bucket_signature(enriched)
    enriched["bucket_signature"] = signature
    enriched["bucket_id"] = _bucket_id_from_signature(signature)
    return enriched


def build_bucket_rows(records: list[dict]) -> list[dict]:
    grouped = {}
    for record in records:
        bucket_id = record["bucket_id"]
        row = grouped.setdefault(
            bucket_id,
            {
                "bucket_id": bucket_id,
                "bucket_signature": record["bucket_signature"],
                "case_ids": [],
                "case_count": 0,
            },
        )
        row["case_ids"].append(record["case_id"])
        row["case_count"] += 1
    for row in grouped.values():
        promotion_rows = [record.get("promotion") for record in records if record["bucket_id"] == row["bucket_id"] and record.get("promotion")]
        if promotion_rows:
            first = promotion_rows[0]
            if all(item == first for item in promotion_rows):
                row["promotion"] = dict(first)
                row["promotion"]["case_count"] = len(promotion_rows)
            else:
                states = Counter(item.get("state", "unknown") for item in promotion_rows)
                row["promotion"] = {
                    "state": "mixed",
                    "case_count": len(promotion_rows),
                    "states": dict(sorted(states.items())),
                }
    return sorted(grouped.values(), key=lambda row: row["bucket_id"])


def lifecycle_bundle_namespace(*, producer: str | None) -> str:
    if not producer:
        return "afl"
    if producer == "afl":
        return "afl"
    return producer


def build_testcase_records(
    *,
    run_id: str,
    producer: str,
    target_implementation: str,
    triage: dict,
    source_root: str,
    replay_harness: str,
    replay_target_id: str,
    replay_targets: list[str],
) -> list[dict]:
    records = []
    for case in triage.get("interesting_cases", []):
        classification = case.get("kind")
        artifact_subdir = {
            "queue": "queue",
            "crash": "crashes",
            "hang": "hangs",
        }.get(classification, "")
        case_id = _stable_case_id(
            run_id,
            producer,
            target_implementation,
            classification or "",
            case.get("relative_path", ""),
            case.get("sha256", ""),
        )
        records.append(
            _enrich_record_with_bucket(
                {
                "case_id": case_id,
                "source_run_id": run_id,
                "producer": producer,
                "classification": classification,
                "triage_reason": case.get("reason"),
                "target_implementation": target_implementation,
                "source_artifact_path": (
                    f"{source_root.rstrip('/')}/{artifact_subdir}/{case.get('relative_path')}"
                    if artifact_subdir
                    else f"{source_root.rstrip('/')}/{case.get('relative_path')}"
                ),
                "sha256": case.get("sha256"),
                "size_bytes": case.get("size_bytes"),
                "metadata": dict(case.get("metadata", {})),
                "replay_targets": list(replay_targets),
                "replay_harness": replay_harness,
                "replay_target_id": replay_target_id,
                "minimization_state": "none",
                "replay_state": "pending",
                "compare_state": "pending" if len(replay_targets) > 1 else "none",
                }
            )
        )
    return records


def build_run_issue_record(
    *,
    run_id: str,
    producer: str,
    target_implementation: str,
    classification: str,
    triage_reason: str,
    source_artifact_path: str,
    metadata: dict | None = None,
) -> dict:
    case_id = _stable_case_id(
        run_id,
        producer,
        target_implementation,
        classification,
        triage_reason,
        source_artifact_path,
    )
    return _enrich_record_with_bucket(
        {
            "case_id": case_id,
            "source_run_id": run_id,
            "producer": producer,
            "classification": classification,
            "triage_reason": triage_reason,
            "target_implementation": target_implementation,
            "source_artifact_path": source_artifact_path,
            "sha256": None,
            "size_bytes": None,
            "metadata": dict(metadata or {}),
            "replay_targets": [],
            "replay_harness": None,
            "replay_target_id": None,
            "minimization_state": "none",
            "replay_state": "none",
            "compare_state": "none",
        }
    )


def build_replay_queue(records: list[dict]) -> list[dict]:
    queue = []
    for record in records:
        if record.get("replay_state") != "pending":
            continue
        for target in record.get("replay_targets", []):
            queue_id = "rq-" + hashlib.sha256(
                f"{record.get('case_id')}|{target}|{record.get('replay_harness')}".encode("utf-8")
            ).hexdigest()[:16]
            queue.append(
                {
                    "queue_id": queue_id,
                    "case_id": record.get("case_id"),
                    "source_run_id": record.get("source_run_id"),
                    "producer": record.get("producer"),
                    "target": target,
                    "replay_harness": record.get("replay_harness"),
                    "replay_target_id": rewrite_target_id_for_impl(
                        infer_replay_target_id(record), record.get("target_implementation"), target
                    ),
                    "state": "pending",
                    "priority": "normal",
                    "compare_group": record.get("case_id") if len(record.get("replay_targets", [])) > 1 else None,
                }
            )
    return queue


def build_compare_queue(records: list[dict]) -> list[dict]:
    queue = []
    for record in records:
        if record.get("compare_state") != "pending":
            continue
        if len(record.get("replay_targets", [])) < 2:
            continue
        queue_id = "cq-" + hashlib.sha256(
            f"{record.get('case_id')}|{infer_replay_target_id(record)}|compare".encode("utf-8")
        ).hexdigest()[:16]
        queue.append(
            {
                "queue_id": queue_id,
                "case_id": record.get("case_id"),
                "source_run_id": record.get("source_run_id"),
                "producer": record.get("producer"),
                "replay_target_id": infer_replay_target_id(record),
                "state": "pending",
                "priority": "normal",
            }
        )
    return queue


def infer_replay_target_id(record: dict) -> str | None:
    target_id = record.get("replay_target_id")
    if target_id:
        return target_id
    harness = record.get("replay_harness") or ""
    implementation = record.get("target_implementation", "amaru")
    known = [
        ("afl-block-header", "cbor-decode-block-header"),
        ("afl-tx-body", "cbor-decode-tx-body"),
        ("afl-block", "cbor-decode-block"),
        ("afl-certificate", "cbor-decode-certificate"),
        ("afl-auxiliary-data", "cbor-decode-auxiliary-data"),
    ]
    for needle, suffix in known:
        if needle in harness:
            return f"{implementation}-{suffix}"
    return None


def rewrite_target_id_for_impl(target_id: str | None, from_impl: str | None, to_impl: str) -> str | None:
    if not target_id or not from_impl or from_impl == to_impl:
        return target_id
    prefix = from_impl + "-"
    if target_id.startswith(prefix):
        return to_impl + "-" + target_id[len(prefix):]
    suffix = "-" + from_impl
    if target_id.endswith(suffix):
        return target_id[:-len(suffix)] + "-" + to_impl
    return target_id


def load_case_record(*, state_dir: Path, case_id: str) -> dict:
    path = Path(state_dir) / "testcases" / f"{case_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_bucket_rows(*, state_dir: Path) -> list[dict]:
    path = Path(state_dir) / "testcases" / "buckets.ndjson"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def summarize_buckets(*, state_dir: Path) -> dict:
    rows = load_bucket_rows(state_dir=state_dir)
    return {
        "bucket_count": len(rows),
        "largest_bucket_case_count": max((row.get("case_count", 0) for row in rows), default=0),
        "rows": rows,
    }


def ingest_run_issue(
    *,
    runs_dir: Path,
    state_dir: Path,
    run_id: str,
    classification: str,
    triage_reason: str,
    producer: str = "scenario",
    source_artifact_path: str = "manifest.json",
) -> dict:
    run_dir = Path(runs_dir) / run_id
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata = {
        "scenario_id": ((manifest.get("scenario") or {}).get("id")),
        "runtime": manifest.get("runtime"),
        "exit_status": manifest.get("exit_status"),
        "behavior_signature": summarize_run_behavior(run_dir=run_dir).get("signature"),
        "resource_signature": summarize_run_resources(run_dir=run_dir).get("signature"),
    }
    record = build_run_issue_record(
        run_id=run_id,
        producer=producer,
        target_implementation=((manifest.get("target") or {}).get("implementation") or "unknown"),
        classification=classification,
        triage_reason=triage_reason,
        source_artifact_path=source_artifact_path,
        metadata=metadata,
    )
    lifecycle = write_lifecycle_artifacts(run_dir=run_dir, state_dir=state_dir, records=[record])
    return {
        "case_id": record["case_id"],
        "bucket_id": record["bucket_id"],
        "lifecycle": lifecycle,
    }


def _promotion_entry(*, bucket_id: str, promotion_state: str, summary: str, source: str, actor: str | None) -> dict:
    if promotion_state not in PROMOTION_STATES:
        raise ValueError(f"unsupported promotion state: {promotion_state}")
    entry = {
        "state": promotion_state,
        "summary": summary,
        "source": source,
        "scope": "bucket",
        "bucket_id": bucket_id,
        "promoted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if actor:
        entry["actor"] = actor
    return entry


def promote_bucket(
    *,
    state_dir: Path,
    bucket_id: str,
    promotion_state: str,
    summary: str,
    source: str,
    actor: str | None = None,
) -> dict:
    state_root = _state_root(Path(state_dir))
    records = _load_index_records(state_root)
    updated_records = []
    updated_case_ids = []
    entry = _promotion_entry(
        bucket_id=bucket_id,
        promotion_state=promotion_state,
        summary=summary,
        source=source,
        actor=actor,
    )
    found = False
    for record in records:
        if record.get("bucket_id") == bucket_id:
            found = True
            refreshed = dict(record)
            refreshed["promotion"] = dict(entry)
            history = list(refreshed.get("promotion_history", []))
            history.append(dict(entry))
            refreshed["promotion_history"] = history
            updated_records.append(refreshed)
            updated_case_ids.append(refreshed["case_id"])
            (state_root / f"{refreshed['case_id']}.json").write_text(
                json.dumps(refreshed, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        else:
            updated_records.append(record)
    if not found:
        raise KeyError(f"unknown bucket_id: {bucket_id}")
    _write_index_from_records(state_root, updated_records)
    _write_bucket_rows(state_root, updated_records)
    return {
        "bucket_id": bucket_id,
        "promotion": entry,
        "updated_case_count": len(updated_case_ids),
        "updated_case_ids": updated_case_ids,
    }


def resolve_source_artifact_path(*, runs_dir: Path, record: dict) -> Path:
    base = Path(runs_dir) / record["source_run_id"]
    direct = base / record["source_artifact_path"]
    if direct.exists():
        return direct
    relative = record["source_artifact_path"]
    classification = record.get("classification")
    artifact_subdir = {
        "queue": "queue",
        "crash": "crashes",
        "hang": "hangs",
    }.get(classification, "")
    if artifact_subdir and "/default/" in relative and f"/default/{artifact_subdir}/" not in relative:
        repaired = relative.replace("/default/", f"/default/{artifact_subdir}/", 1)
        candidate = base / repaired
        if candidate.exists():
            return candidate
    return direct


def stage_case_input(*, runs_dir: Path, record: dict, destination: Path) -> Path:
    source = resolve_source_artifact_path(runs_dir=Path(runs_dir), record=record)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())
    return destination


def build_replay_scenario_body(record: dict, *, target_implementation: str, manifests_dir: str, input_path: str) -> dict:
    target_id = rewrite_target_id_for_impl(
        infer_replay_target_id(record),
        record.get("target_implementation"),
        target_implementation,
    )
    return {
        "spec_version": "v1",
        "id": f"{record['case_id']}-{target_implementation}-replay",
        "title": f"Replay {record['case_id']} on {target_implementation}",
        "target": {"implementation": target_implementation, "version": "any"},
        "runtime": "library",
        "related_milestones": ["M3"],
        "evidence_intent": "candidate",
        "promotion_blockers": ["Replay result is candidate evidence until reviewed."],
        "load": [
            {
                "primitive": "cbor_replay_target",
                "target_id": target_id,
                "manifests_dir": manifests_dir,
                "input_path": input_path,
            }
        ],
        "probes": [{"primitive": "parser_exit_status"}],
        "assertions": [{"primitive": "parse_succeeds_or_clean_error"}],
        "teardown": [],
    }


def summarize_run_behavior(*, run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assertion_summary = manifest.get("assertion_summary") or {}
    outcomes = Counter()
    outcome_details = Counter()
    primitive_counts = Counter()
    target_event_count = 0
    target_path = run_dir / "events" / "target.ndjson"
    if target_path.exists():
        for line in target_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            target_event_count += 1
            entry = json.loads(line)
            primitive = entry.get("primitive")
            if primitive:
                primitive_counts[primitive] += 1
            payload = entry.get("payload") or {}
            outcome = payload.get("outcome")
            if outcome:
                outcomes[outcome] += 1
                detail_source = payload.get("stdout_head") or payload.get("stderr_head") or payload.get("stdout") or payload.get("stderr")
                if isinstance(detail_source, str) and detail_source.strip():
                    detail_hash = hashlib.sha256(detail_source.strip().encode("utf-8")).hexdigest()[:8]
                    outcome_details[f"{outcome}#{detail_hash}"] += 1
    target_hook_event_count = 0
    target_hook_path = run_dir / "events" / "target-hooks.ndjson"
    if target_hook_path.exists():
        target_hook_event_count = sum(1 for line in target_hook_path.read_text(encoding="utf-8").splitlines() if line.strip())
    probe_sample_count = 0
    probe_path = run_dir / "probes" / "parser_exit_status.ndjson"
    if probe_path.exists():
        probe_sample_count = sum(1 for line in probe_path.read_text(encoding="utf-8").splitlines() if line.strip())
    signature_parts = [
        f"exit={manifest.get('exit_status', 'unknown')}",
        f"assert_fail={assertion_summary.get('fail', 0)}",
    ]
    if outcomes:
        signature_parts.append(
            "outcomes=" + ",".join(f"{name}:{count}" for name, count in sorted(outcomes.items()))
        )
    else:
        signature_parts.append("outcomes=none")
    if outcome_details:
        signature_parts.append(
            "details=" + ",".join(f"{name}:{count}" for name, count in sorted(outcome_details.items()))
        )
    if primitive_counts:
        signature_parts.append(
            "primitives=" + ",".join(f"{name}:{count}" for name, count in sorted(primitive_counts.items()))
        )
    signature_parts.append(f"hooks={target_hook_event_count}")
    signature_parts.append(f"probe_samples={probe_sample_count}")
    return {
        "exit_status": manifest.get("exit_status", "unknown"),
        "assertion_fail_count": assertion_summary.get("fail", 0),
        "assertion_pass_count": assertion_summary.get("pass", 0),
        "target_event_count": target_event_count,
        "target_hook_event_count": target_hook_event_count,
        "probe_sample_count": probe_sample_count,
        "primitive_counts": dict(sorted(primitive_counts.items())),
        "outcome_counts": dict(sorted(outcomes.items())),
        "outcome_detail_counts": dict(sorted(outcome_details.items())),
        "signature": ";".join(signature_parts),
    }


def _bucket_wall_time(value: float | None) -> str:
    if value is None:
        return "none"
    if value < 0.01:
        return "tiny"
    if value < 0.1:
        return "short"
    if value < 1.0:
        return "medium"
    return "long"


def _bucket_bytes(value: int | None) -> str:
    if value is None:
        return "none"
    if value < 1024 * 1024:
        return "tiny"
    if value < 16 * 1024 * 1024:
        return "small"
    if value < 128 * 1024 * 1024:
        return "medium"
    return "large"


def _bucket_count(value: int | None) -> str:
    if value is None:
        return "none"
    if value < 16:
        return "low"
    if value < 64:
        return "medium"
    return "high"


def summarize_run_resources(*, run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    metrics_summary = {}
    metrics_summary_path = run_dir / "metrics" / "summary.json"
    if metrics_summary_path.exists():
        metrics_summary = json.loads(metrics_summary_path.read_text(encoding="utf-8"))

    wall_time_seconds = ((manifest.get("resource_snapshot") or {}).get("wall_time_seconds"))
    process_samples_path = run_dir / "metrics" / "process" / "self.ndjson"
    host_samples_path = run_dir / "metrics" / "host" / "load.ndjson"

    peak_rss_bytes = None
    peak_fd_count = None
    peak_socket_count = None
    process_sample_count = 0
    if process_samples_path.exists():
        for line in process_samples_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            process_sample_count += 1
            value = (json.loads(line).get("value") or {})
            rss_bytes = value.get("rss_bytes")
            fd_count = value.get("fd_count")
            socket_count = value.get("socket_count")
            if rss_bytes is not None:
                peak_rss_bytes = max(peak_rss_bytes or rss_bytes, rss_bytes)
            if fd_count is not None:
                peak_fd_count = max(peak_fd_count or fd_count, fd_count)
            if socket_count is not None:
                peak_socket_count = max(peak_socket_count or socket_count, socket_count)

    host_sample_count = 0
    if host_samples_path.exists():
        host_sample_count = sum(1 for line in host_samples_path.read_text(encoding="utf-8").splitlines() if line.strip())

    runtime_metric_series_count = 0
    runtime_metric_sample_count = 0
    runtime_metric_names = []
    runtime_dir = run_dir / "metrics" / "runtime"
    if runtime_dir.exists():
        for path in sorted(runtime_dir.glob("*.ndjson")):
            runtime_metric_series_count += 1
            runtime_metric_names.append(path.stem)
            runtime_metric_sample_count += sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())

    signature_parts = [
        f"wall={_bucket_wall_time(wall_time_seconds)}",
        f"rss={_bucket_bytes(peak_rss_bytes)}",
        f"fd={_bucket_count(peak_fd_count)}",
        f"sockets={_bucket_count(peak_socket_count)}",
        f"runtime_series={runtime_metric_series_count}",
        "runtime_keys=" + (",".join(runtime_metric_names) if runtime_metric_names else "none"),
        f"process_samples={process_sample_count}",
    ]
    return {
        "wall_time_seconds": wall_time_seconds,
        "peak_rss_bytes": peak_rss_bytes,
        "peak_fd_count": peak_fd_count,
        "peak_socket_count": peak_socket_count,
        "host_sample_count": host_sample_count,
        "process_sample_count": process_sample_count,
        "runtime_metric_series_count": runtime_metric_series_count,
        "runtime_metric_sample_count": runtime_metric_sample_count,
        "runtime_metric_names": runtime_metric_names,
        "observer_event_count": metrics_summary.get("observer_event_count"),
        "target_event_count": metrics_summary.get("target_event_count"),
        "signature": ";".join(signature_parts),
    }


def write_lifecycle_artifacts(*, run_dir: Path, state_dir: Path, records: list[dict]) -> dict:
    records = [
        record if "bucket_id" in record and "bucket_signature" in record else _enrich_record_with_bucket(record)
        for record in records
    ]
    producers = {record.get("producer") for record in records}
    if len(producers) > 1:
        raise ValueError(f"write_lifecycle_artifacts requires a single producer per run, got {sorted(producers)}")
    bundle_namespace = lifecycle_bundle_namespace(producer=next(iter(producers), "afl"))
    bundle_path = run_dir / "outputs" / bundle_namespace / "testcases.ndjson"
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    replay_queue = build_replay_queue(records)
    compare_queue = build_compare_queue(records)
    bucket_rows = build_bucket_rows(records)
    bundle_queue_path = bundle_path.parent / "replay-queue.ndjson"
    bundle_queue_path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in replay_queue),
        encoding="utf-8",
    )
    bundle_compare_queue_path = bundle_path.parent / "compare-queue.ndjson"
    bundle_compare_queue_path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in compare_queue),
        encoding="utf-8",
    )
    bundle_buckets_path = bundle_path.parent / "buckets.ndjson"
    bundle_buckets_path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in bucket_rows),
        encoding="utf-8",
    )

    state_root = state_dir / "testcases"
    state_root.mkdir(parents=True, exist_ok=True)
    index_path = state_root / "index.ndjson"
    index_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    state_queue_path = state_root / "replay-queue.ndjson"
    state_queue_path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in replay_queue),
        encoding="utf-8",
    )
    state_compare_queue_path = state_root / "compare-queue.ndjson"
    state_compare_queue_path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in compare_queue),
        encoding="utf-8",
    )
    state_buckets_path = state_root / "buckets.ndjson"
    state_buckets_path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in bucket_rows),
        encoding="utf-8",
    )
    for record in records:
        (state_root / f"{record['case_id']}.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    return {
        "bundle_namespace": bundle_namespace,
        "bundle_path": str(bundle_path),
        "bundle_replay_queue_path": str(bundle_queue_path),
        "bundle_compare_queue_path": str(bundle_compare_queue_path),
        "bundle_buckets_path": str(bundle_buckets_path),
        "state_root": str(state_root),
        "index_path": str(index_path),
        "state_replay_queue_path": str(state_queue_path),
        "state_compare_queue_path": str(state_compare_queue_path),
        "state_buckets_path": str(state_buckets_path),
        "record_count": len(records),
        "replay_queue_count": len(replay_queue),
        "compare_queue_count": len(compare_queue),
        "bucket_count": len(bucket_rows),
    }


def _state_root(state_dir: Path) -> Path:
    root = Path(state_dir) / "testcases"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_index_from_records(state_root: Path, records: list[dict]) -> None:
    (state_root / "index.ndjson").write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _load_index_records(state_root: Path) -> list[dict]:
    index_path = state_root / "index.ndjson"
    if not index_path.exists():
        return []
    return [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_replay_queue(state_root: Path) -> list[dict]:
    queue_path = state_root / "replay-queue.ndjson"
    if not queue_path.exists():
        return []
    return [json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_replay_queue(state_root: Path, queue: list[dict]) -> None:
    (state_root / "replay-queue.ndjson").write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in queue),
        encoding="utf-8",
    )


def _load_compare_queue(state_root: Path) -> list[dict]:
    queue_path = state_root / "compare-queue.ndjson"
    if not queue_path.exists():
        return []
    return [json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_compare_queue(state_root: Path, queue: list[dict]) -> None:
    (state_root / "compare-queue.ndjson").write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in queue),
        encoding="utf-8",
    )


def _write_bucket_rows(state_root: Path, records: list[dict]) -> None:
    bucket_rows = build_bucket_rows(records)
    (state_root / "buckets.ndjson").write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in bucket_rows),
        encoding="utf-8",
    )


def _rewrite_case_record(state_root: Path, record: dict) -> None:
    refreshed = _enrich_record_with_bucket(record)
    (state_root / f"{refreshed['case_id']}.json").write_text(
        json.dumps(refreshed, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _rewrite_index_record(state_root: Path, record: dict) -> list[dict]:
    records = _load_index_records(state_root)
    refreshed_records = []
    for item in records:
        if item.get("case_id") == record.get("case_id"):
            refreshed_records.append(_enrich_record_with_bucket(record))
        else:
            refreshed_records.append(item if "bucket_id" in item and "bucket_signature" in item else _enrich_record_with_bucket(item))
    _write_index_from_records(state_root, refreshed_records)
    _write_bucket_rows(state_root, refreshed_records)
    return refreshed_records


def repair_state(*, state_dir: Path) -> dict:
    state_root = _state_root(Path(state_dir))
    case_paths = sorted(state_root.glob("tc-*.json"))
    repaired_records = []
    repaired_count = 0
    for path in case_paths:
        record = json.loads(path.read_text(encoding="utf-8"))
        before_bucket_id = record.get("bucket_id")
        before_signature = record.get("bucket_signature")
        inferred_target_id = infer_replay_target_id(record)
        if record.get("replay_target_id") is None and inferred_target_id is not None:
            record["replay_target_id"] = inferred_target_id
        refreshed = _enrich_record_with_bucket(record)
        if refreshed.get("bucket_id") != before_bucket_id or refreshed.get("bucket_signature") != before_signature or refreshed.get("replay_target_id") != record.get("replay_target_id"):
            repaired_count += 1
        path.write_text(json.dumps(refreshed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        repaired_records.append(refreshed)
    if repaired_records:
        _write_index_from_records(state_root, repaired_records)
        _write_bucket_rows(state_root, repaired_records)
        _write_replay_queue(state_root, build_replay_queue(repaired_records))
        _write_compare_queue(state_root, build_compare_queue(repaired_records))
    return {
        "case_count": len(repaired_records),
        "repaired_count": repaired_count,
        "compare_queue_count": len(build_compare_queue(repaired_records)) if repaired_records else 0,
        "bucket_count": len(build_bucket_rows(repaired_records)) if repaired_records else 0,
    }


def pending_replay_queue_items(*, state_dir: Path, limit: int | None = None, case_id: str | None = None) -> list[dict]:
    state_root = _state_root(Path(state_dir))
    queue = _load_replay_queue(state_root)
    items = [
        item
        for item in queue
        if item.get("state") == "pending" and (case_id is None or item.get("case_id") == case_id)
    ]
    if limit is not None:
        return items[:limit]
    return items


def pending_compare_queue_items(*, state_dir: Path, limit: int | None = None, case_id: str | None = None) -> list[dict]:
    state_root = _state_root(Path(state_dir))
    queue = _load_compare_queue(state_root)
    items = [
        item
        for item in queue
        if item.get("state") == "pending" and (case_id is None or item.get("case_id") == case_id)
    ]
    if limit is not None:
        return items[:limit]
    return items


def record_replay_result(*, state_dir: Path, case_id: str, target: str, run_id: str, exit_status: str, behavior_summary: dict | None = None, resource_summary: dict | None = None) -> None:
    state_root = _state_root(Path(state_dir))
    queue = _load_replay_queue(state_root)
    updated_targets = set()
    for item in queue:
        if item.get("case_id") == case_id and item.get("target") == target:
            item["state"] = "complete"
            item["replay_run_id"] = run_id
            item["exit_status"] = exit_status
            item["behavior_summary"] = dict(behavior_summary or {})
            item["resource_summary"] = dict(resource_summary or {})
        if item.get("case_id") == case_id and item.get("state") == "complete":
            updated_targets.add(item.get("target"))
    _write_replay_queue(state_root, queue)

    record_path = state_root / f"{case_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    replay_results = list(record.get("replay_results", []))
    replay_results.append({
        "target": target,
        "run_id": run_id,
        "exit_status": exit_status,
        "behavior_summary": dict(behavior_summary or {}),
        "resource_summary": dict(resource_summary or {}),
    })
    record["replay_results"] = replay_results
    expected_targets = set(record.get("replay_targets", []))
    if expected_targets and expected_targets.issubset(updated_targets):
        record["replay_state"] = "complete"
    _rewrite_case_record(state_root, record)
    _rewrite_index_record(state_root, record)


def record_compare_result(*, state_dir: Path, case_id: str, comparison_path: str, agreed: bool, runs: dict, run_outcomes: dict | None = None, behavior_summaries: dict | None = None, resource_summaries: dict | None = None) -> None:
    state_root = _state_root(Path(state_dir))
    compare_queue = _load_compare_queue(state_root)
    for item in compare_queue:
        if item.get("case_id") == case_id:
            item["state"] = "complete"
            item["comparison_path"] = comparison_path
            item["agreed"] = bool(agreed)
            item["runs"] = runs
            item["run_outcomes"] = dict(run_outcomes or {})
            item["behavior_summaries"] = dict(behavior_summaries or {})
            item["resource_summaries"] = dict(resource_summaries or {})
    _write_compare_queue(state_root, compare_queue)
    record_path = state_root / f"{case_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["compare_state"] = "complete"
    record["compare_result"] = {
        "agreed": bool(agreed),
        "comparison_path": comparison_path,
        "runs": runs,
        "run_outcomes": dict(run_outcomes or {}),
        "behavior_summaries": dict(behavior_summaries or {}),
        "resource_summaries": dict(resource_summaries or {}),
    }
    _rewrite_case_record(state_root, record)
    _rewrite_index_record(state_root, record)


def record_minimization_result(*, state_dir: Path, case_id: str, target: str, minimized_path: str, original_size: int, minimized_size: int, tool: str) -> None:
    state_root = _state_root(Path(state_dir))
    record_path = state_root / f"{case_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    results = list(record.get("minimization_results", []))
    results.append(
        {
            "status": "complete",
            "target": target,
            "tool": tool,
            "minimized_path": minimized_path,
            "original_size": original_size,
            "minimized_size": minimized_size,
        }
    )
    record["minimization_results"] = results
    record["minimization_state"] = "complete"
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    records = _load_index_records(state_root)
    for item in records:
        if item.get("case_id") == case_id:
            item["minimization_results"] = results
            item["minimization_state"] = "complete"
    _write_index_from_records(state_root, records)


def record_minimization_failure(*, state_dir: Path, case_id: str, target: str, tool: str, error: str) -> None:
    state_root = _state_root(Path(state_dir))
    record_path = state_root / f"{case_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    results = list(record.get("minimization_results", []))
    results.append(
        {
            "status": "failed",
            "target": target,
            "tool": tool,
            "error": error,
        }
    )
    record["minimization_results"] = results
    record["minimization_state"] = "failed"
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    records = _load_index_records(state_root)
    for item in records:
        if item.get("case_id") == case_id:
            item["minimization_results"] = results
            item["minimization_state"] = "failed"
    _write_index_from_records(state_root, records)


def _normalize_oracle_result(returncode: int, stdout: str) -> str:
    first_line = stdout.splitlines()[0] if stdout else ""
    if returncode == 0 and first_line.startswith("OK"):
        return "ok"
    if returncode == 1 and first_line.startswith("ERR "):
        return "clean_error"
    return f"other:{returncode}:{first_line[:80]}"


def _run_oracle_target(*, binary: str, input_format: str, data: bytes, timeout_seconds: float = 2.0) -> str:
    if input_format != "stdin_bytes":
        raise ValueError(f"unsupported oracle input_format: {input_format}")
    proc = subprocess.run(
        [binary],
        input=data,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    return _normalize_oracle_result(
        proc.returncode,
        proc.stdout.decode("utf-8", errors="replace"),
    )


def _shrink_bytes_with_oracle(data: bytes, oracle: Callable[[bytes], str], expected: str) -> bytes:
    if not data:
        return data
    current = data
    chunk_size = max(1, len(current) // 2)
    while chunk_size >= 1:
        changed = False
        start = 0
        while start < len(current):
            candidate = current[:start] + current[start + chunk_size :]
            if candidate and oracle(candidate) == expected:
                current = candidate
                changed = True
                start = 0
                continue
            start += chunk_size
        if not changed:
            chunk_size //= 2
    return current


def _find_afl_tmin() -> str:
    tool = shutil.which("afl-tmin")
    if tool:
        return tool
    raise FileNotFoundError("afl-tmin not found in PATH")


def build_minimization_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env or os.environ)
    env.setdefault("AFL_NO_UI", "1")
    env.setdefault("AFL_SKIP_CPUFREQ", "1")
    env.setdefault("AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES", "1")
    env.setdefault("AFL_NO_FORKSRV", "1")
    return env


def build_minimization_command(
    *,
    tmin: str,
    input_path: str,
    output_path: str,
    target_binary: str,
    target_input_path: str,
) -> list[str]:
    return [
        tmin,
        "-i",
        input_path,
        "-o",
        output_path,
        "-f",
        target_input_path,
        "--",
        target_binary,
        "@@",
    ]


def infer_minimization_harness(record: dict) -> str | None:
    harness = record.get("replay_harness", "")
    known = {
        "amaru-afl-tx-body": "amaru-afl-tmin-tx-body",
        "amaru-afl-block-header": "amaru-afl-tmin-block-header",
    }
    return known.get(harness)


def resolve_minimization_binary(*, record: dict, target_implementation: str, manifests_dir: str):
    try:
        from profile_manager.primitives import _resolve_runtime_path, load_target_manifest
    except ModuleNotFoundError:
        from dwarf.profile_manager.primitives import _resolve_runtime_path, load_target_manifest

    manifests_dir = Path(manifests_dir)
    if (
        record.get("producer") == "afl"
        and target_implementation == record.get("target_implementation")
        and record.get("replay_harness")
    ):
        preferred_harnesses = []
        minimization_harness = infer_minimization_harness(record)
        if minimization_harness:
            preferred_harnesses.append(minimization_harness)
        preferred_harnesses.append(record["replay_harness"])
        candidate_roots = [
            manifests_dir.parent,
            manifests_dir.parent.parent / "dwarf" / "targets",
        ]
        for root in candidate_roots:
            for harness in preferred_harnesses:
                afl_candidate = root / target_implementation / "target" / "release" / harness
                if afl_candidate.exists():
                    return afl_candidate

    target_id = rewrite_target_id_for_impl(
        infer_replay_target_id(record),
        record.get("target_implementation"),
        target_implementation,
    )
    manifest = load_target_manifest(target_id, manifests_dir=manifests_dir)
    return Path(str(_resolve_runtime_path(manifest.binary)))


def run_minimize_case(*, case_id: str, target_implementation: str, runs_dir: Path, state_dir: Path, manifests_dir: str):
    return run_minimize_case_with_backend(
        case_id=case_id,
        target_implementation=target_implementation,
        runs_dir=runs_dir,
        state_dir=state_dir,
        manifests_dir=manifests_dir,
        backend="oracle",
    )


def run_minimize_case_with_backend(
    *,
    case_id: str,
    target_implementation: str,
    runs_dir: Path,
    state_dir: Path,
    manifests_dir: str,
    backend: str,
):
    record = load_case_record(state_dir=Path(state_dir), case_id=case_id)
    input_path = resolve_source_artifact_path(runs_dir=Path(runs_dir), record=record)
    if backend == "oracle":
        return run_oracle_minimize_case(
            case_id=case_id,
            target_implementation=target_implementation,
            state_dir=Path(state_dir),
            manifests_dir=manifests_dir,
            record=record,
            input_path=input_path,
        )
    if backend != "afl-tmin":
        raise ValueError(f"unknown minimization backend: {backend}")
    binary = str(
        resolve_minimization_binary(
            record=record,
            target_implementation=target_implementation,
            manifests_dir=manifests_dir,
        )
    )
    tmin = _find_afl_tmin()

    minimized_root = _state_root(Path(state_dir)) / "minimized"
    minimized_root.mkdir(parents=True, exist_ok=True)
    output_path = minimized_root / f"{case_id}-{target_implementation}.bin"
    target_input_path = minimized_root / f"{case_id}-{target_implementation}.input"

    env = build_minimization_env()
    shutil.copy2(input_path, target_input_path)
    proc = subprocess.run(
        build_minimization_command(
            tmin=tmin,
            input_path=str(input_path),
            output_path=str(output_path),
            target_binary=str(binary),
            target_input_path=str(target_input_path),
        ),
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        message = f"afl-tmin failed with exit {proc.returncode}: {proc.stdout}{proc.stderr}"
        record_minimization_failure(
            state_dir=Path(state_dir),
            case_id=case_id,
            target=target_implementation,
            tool="afl-tmin",
            error=message,
        )
        raise RuntimeError(message)
    if not output_path.exists():
        raise RuntimeError("afl-tmin did not produce an output file")
    record_minimization_result(
        state_dir=Path(state_dir),
        case_id=case_id,
        target=target_implementation,
        minimized_path=str(output_path),
        original_size=input_path.stat().st_size,
        minimized_size=output_path.stat().st_size,
        tool="afl-tmin",
    )
    return {
        "case_id": case_id,
        "target": target_implementation,
        "tool": "afl-tmin",
        "input_path": str(input_path),
        "output_path": str(output_path),
        "original_size": input_path.stat().st_size,
        "minimized_size": output_path.stat().st_size,
    }


def run_oracle_minimize_case(
    *,
    case_id: str,
    target_implementation: str,
    state_dir: Path,
    manifests_dir: str,
    record: dict,
    input_path: Path,
):
    try:
        from profile_manager.primitives import _resolve_runtime_path, load_target_manifest
    except ModuleNotFoundError:
        from dwarf.profile_manager.primitives import _resolve_runtime_path, load_target_manifest

    target_id = rewrite_target_id_for_impl(
        infer_replay_target_id(record),
        record.get("target_implementation"),
        target_implementation,
    )
    manifest = load_target_manifest(target_id, manifests_dir=Path(manifests_dir))
    binary = str(_resolve_runtime_path(manifest.binary))
    original = input_path.read_bytes()
    expected = _run_oracle_target(binary=binary, input_format=manifest.input_format, data=original)
    minimized = _shrink_bytes_with_oracle(
        original,
        lambda data: _run_oracle_target(binary=binary, input_format=manifest.input_format, data=data),
        expected,
    )
    minimized_root = _state_root(Path(state_dir)) / "minimized"
    minimized_root.mkdir(parents=True, exist_ok=True)
    output_path = minimized_root / f"{case_id}-{target_implementation}.bin"
    output_path.write_bytes(minimized)
    record_minimization_result(
        state_dir=Path(state_dir),
        case_id=case_id,
        target=target_implementation,
        minimized_path=str(output_path),
        original_size=len(original),
        minimized_size=len(minimized),
        tool="oracle-ddmin",
    )
    return {
        "case_id": case_id,
        "target": target_implementation,
        "tool": "oracle-ddmin",
        "input_path": str(input_path),
        "output_path": str(output_path),
        "original_size": len(original),
        "minimized_size": len(minimized),
    }


def run_replay_case(*, case_id: str, target_implementation: str, runs_dir: Path, state_dir: Path, scenario_module, registry_path=None, manifests_dir=None):
    record = load_case_record(state_dir=Path(state_dir), case_id=case_id)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        input_path = stage_case_input(
            runs_dir=Path(runs_dir),
            record=record,
            destination=tmp_root / "case.bin",
        )
        body = build_replay_scenario_body(
            record,
            target_implementation=target_implementation,
            manifests_dir=str(manifests_dir or "dwarf/targets/manifests"),
            input_path=str(input_path),
        )
        scenario_path = tmp_root / "replay-scenario.yaml"
        scenario_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        handle = scenario_module.run_scenario(
            scenario_path,
            runs_dir=Path(runs_dir),
            state_dir=Path(state_dir),
            registry_path=registry_path,
        )
    manifest = json.loads((handle.run_dir / "manifest.json").read_text(encoding="utf-8"))
    record_replay_result(
        state_dir=Path(state_dir),
        case_id=case_id,
        target=target_implementation,
        run_id=handle.run_id,
        exit_status=manifest.get("exit_status", "unknown"),
        behavior_summary=summarize_run_behavior(run_dir=handle.run_dir),
        resource_summary=summarize_run_resources(run_dir=handle.run_dir),
    )
    return handle


def compare_replay_case(*, case_id: str, runs_dir: Path, state_dir: Path, scenario_module, registry_path=None, amaru_manifests_dir=None, cardano_node_manifests_dir=None):
    record = load_case_record(state_dir=Path(state_dir), case_id=case_id)
    original_impl = record.get("target_implementation", "amaru")
    manifests_dir = amaru_manifests_dir or "dwarf/targets/manifests"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        input_path = stage_case_input(
            runs_dir=Path(runs_dir),
            record=record,
            destination=tmp_root / "case.bin",
        )
        body = build_replay_scenario_body(
            record,
            target_implementation=original_impl,
            manifests_dir=str(manifests_dir),
            input_path=str(input_path),
        )
        scenario_path = tmp_root / "compare-scenario.yaml"
        scenario_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        result = scenario_module.compare_run(
            scenario_path,
            runs_dir=Path(runs_dir),
            state_dir=Path(state_dir),
            registry_path=registry_path,
            implementation_manifest_dirs={
                "amaru": str(amaru_manifests_dir or manifests_dir),
                "cardano-node": str(cardano_node_manifests_dir or manifests_dir),
            },
        )
    record_compare_result(
        state_dir=Path(state_dir),
        case_id=case_id,
        comparison_path=str(result.comparison_path),
        agreed=result.agreed,
        runs={impl: handle.run_id for impl, handle in result.runs.items()},
        run_outcomes=dict(result.run_outcomes or {}),
        behavior_summaries=dict(result.behavior_summaries or {}),
        resource_summaries=dict(result.resource_summaries or {}),
    )
    return result


def run_replay_queue(
    *,
    runs_dir: Path,
    state_dir: Path,
    scenario_module,
    registry_path=None,
    manifests_dir: str | None = None,
    amaru_manifests_dir: str | None = None,
    cardano_node_manifests_dir: str | None = None,
    limit: int | None = None,
    case_id: str | None = None,
):
    items = pending_replay_queue_items(state_dir=Path(state_dir), limit=limit, case_id=case_id)
    results = []
    for item in items:
        target = item["target"]
        resolved_manifests = manifests_dir
        if target == "amaru" and amaru_manifests_dir:
            resolved_manifests = amaru_manifests_dir
        if target == "cardano-node" and cardano_node_manifests_dir:
            resolved_manifests = cardano_node_manifests_dir
        handle = run_replay_case(
            case_id=item["case_id"],
            target_implementation=target,
            runs_dir=Path(runs_dir),
            state_dir=Path(state_dir),
            scenario_module=scenario_module,
            registry_path=registry_path,
            manifests_dir=resolved_manifests,
        )
        results.append(
            {
                "queue_id": item.get("queue_id"),
                "case_id": item["case_id"],
                "target": target,
                "run_id": handle.run_id,
            }
        )
    return {
        "processed": len(results),
        "items": results,
    }


def run_compare_queue(
    *,
    runs_dir: Path,
    state_dir: Path,
    scenario_module,
    registry_path=None,
    amaru_manifests_dir: str | None = None,
    cardano_node_manifests_dir: str | None = None,
    limit: int | None = None,
    case_id: str | None = None,
):
    items = pending_compare_queue_items(state_dir=Path(state_dir), limit=limit, case_id=case_id)
    results = []
    for item in items:
        result = compare_replay_case(
            case_id=item["case_id"],
            runs_dir=Path(runs_dir),
            state_dir=Path(state_dir),
            scenario_module=scenario_module,
            registry_path=registry_path,
            amaru_manifests_dir=amaru_manifests_dir,
            cardano_node_manifests_dir=cardano_node_manifests_dir,
        )
        results.append(
            {
                "queue_id": item.get("queue_id"),
                "case_id": item["case_id"],
                "agreed": result.agreed,
                "comparison_path": str(result.comparison_path),
                "amaru_run_id": result.runs["amaru"].run_id,
                "cardano_node_run_id": result.runs["cardano-node"].run_id,
            }
        )
    return {
        "processed": len(results),
        "items": results,
    }
