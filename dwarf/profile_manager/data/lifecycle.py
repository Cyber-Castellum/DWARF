"""Pure testcase-lifecycle helpers for the dashboard data layer."""
from __future__ import annotations

import json
from pathlib import Path

from profile_manager.config import config_exists, load_config
from profile_manager.remote import ssh_command


def _read_ndjson_rows(path):
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _summarize_testcase_state(state_root):
    state_root = Path(state_root)
    if not state_root.exists():
        return {
            "available": False,
            "source": "none",
            "state_root": str(state_root),
            "case_count": 0,
            "bucket_count": 0,
            "runtime_anomaly_count": 0,
            "pending_compare_count": 0,
            "pending_replay_count": 0,
            "top_buckets": [],
            "runtime_buckets": [],
            "fuzz_buckets": [],
            "recent_cases": [],
            "recent_runtime_cases": [],
            "recent_fuzz_cases": [],
        }
    cases = []
    for path in sorted(state_root.glob("tc-*.json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        metadata = obj.get("metadata") or {}
        cases.append(
            {
                "case_id": obj.get("case_id", path.stem),
                "bucket_id": obj.get("bucket_id") or "unbucketed",
                "classification": obj.get("classification") or "unknown",
                "triage_reason": obj.get("triage_reason") or "unknown",
                "target_implementation": obj.get("target_implementation") or "unknown",
                "source_run_id": obj.get("source_run_id") or "",
                "replay_state": obj.get("replay_state") or "none",
                "compare_state": obj.get("compare_state") or "none",
                "minimization_state": obj.get("minimization_state") or "none",
                "scenario_id": metadata.get("scenario_id") or "",
                "runtime": metadata.get("runtime") or "",
                "behavior_signature": metadata.get("behavior_signature") or "",
                "resource_signature": metadata.get("resource_signature") or "",
            }
        )
    bucket_groups = {}
    for case in cases:
        bucket = bucket_groups.setdefault(
            case["bucket_id"],
            {
                "bucket_id": case["bucket_id"],
                "classification": case["classification"],
                "triage_reason": case["triage_reason"],
                "target_implementation": case["target_implementation"],
                "case_count": 0,
                "pending_replay_count": 0,
                "pending_compare_count": 0,
                "complete_minimization_count": 0,
            },
        )
        bucket["case_count"] += 1
        if case["replay_state"] == "pending":
            bucket["pending_replay_count"] += 1
        if case["compare_state"] == "pending":
            bucket["pending_compare_count"] += 1
        if case["minimization_state"] == "complete":
            bucket["complete_minimization_count"] += 1
    top_buckets = sorted(
        bucket_groups.values(),
        key=lambda row: (
            0 if row["classification"] == "runtime_anomaly" else 1,
            -row["case_count"],
            row["triage_reason"],
            row["bucket_id"],
        ),
    )[:8]
    recent_cases = sorted(
        cases,
        key=lambda row: (
            1 if row["classification"] == "runtime_anomaly" else 0,
            row["source_run_id"],
            row["case_id"],
        ),
        reverse=True,
    )[:10]
    runtime_buckets = [row for row in top_buckets if row["classification"] == "runtime_anomaly"]
    fuzz_buckets = [row for row in top_buckets if row["classification"] != "runtime_anomaly"]
    recent_runtime_cases = [row for row in recent_cases if row["classification"] == "runtime_anomaly"][:10]
    recent_fuzz_cases = [row for row in recent_cases if row["classification"] != "runtime_anomaly"][:10]
    compare_rows = _read_ndjson_rows(state_root / "compare-queue.ndjson")
    replay_rows = _read_ndjson_rows(state_root / "replay-queue.ndjson")
    return {
        "available": True,
        "source": "local",
        "state_root": str(state_root),
        "case_count": len(cases),
        "bucket_count": len(bucket_groups),
        "runtime_anomaly_count": sum(1 for case in cases if case["classification"] == "runtime_anomaly"),
        "pending_compare_count": sum(1 for row in compare_rows if row.get("state") == "pending"),
        "pending_replay_count": sum(1 for row in replay_rows if row.get("state") == "pending"),
        "top_buckets": top_buckets,
        "runtime_buckets": runtime_buckets,
        "fuzz_buckets": fuzz_buckets,
        "recent_cases": recent_cases,
        "recent_runtime_cases": recent_runtime_cases,
        "recent_fuzz_cases": recent_fuzz_cases,
    }


def _local_testcase_lifecycle_summary():
    from profile_manager.dashboard import PROJECT_ROOT

    candidates = [
        PROJECT_ROOT / "state" / "testcases",
        PROJECT_ROOT / "dwarf" / "state" / "testcases",
    ]
    for candidate in candidates:
        if candidate.exists():
            return _summarize_testcase_state(candidate)
    return _summarize_testcase_state(candidates[0])


def _live_testcase_lifecycle_summary():
    if not config_exists():
        return _local_testcase_lifecycle_summary()
    cfg = load_config()
    remote_base = Path(cfg.remote_base_path)
    remote_candidates = [
        remote_base.parent / "dwarf-fw" / "state" / "testcases",
        remote_base / "state" / "testcases",
    ]
    remote_command = (
        "python3 - <<'PY'\n"
        "import json, pathlib\n"
        f"candidates = [pathlib.Path(p) for p in {json.dumps([str(path) for path in remote_candidates])}]\n"
        "def read_ndjson_rows(path):\n"
        "    rows = []\n"
        "    if not path.exists():\n"
        "        return rows\n"
        "    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():\n"
        "        line = line.strip()\n"
        "        if not line:\n"
        "            continue\n"
        "        try:\n"
        "            rows.append(json.loads(line))\n"
        "        except json.JSONDecodeError:\n"
        "            continue\n"
        "    return rows\n"
        "state_root = next((path for path in candidates if path.exists()), candidates[0])\n"
        "if not state_root.exists():\n"
        "    print(json.dumps({'available': False, 'source': 'remote', 'state_root': str(state_root), 'case_count': 0, 'bucket_count': 0, 'runtime_anomaly_count': 0, 'pending_compare_count': 0, 'pending_replay_count': 0, 'top_buckets': [], 'recent_cases': []}))\n"
        "    raise SystemExit(0)\n"
        "cases = []\n"
        "for path in sorted(state_root.glob('tc-*.json')):\n"
        "    try:\n"
        "        obj = json.loads(path.read_text(encoding='utf-8', errors='replace'))\n"
        "    except (OSError, json.JSONDecodeError):\n"
        "        continue\n"
        "    metadata = obj.get('metadata') or {}\n"
        "    cases.append({'case_id': obj.get('case_id', path.stem), 'bucket_id': obj.get('bucket_id') or 'unbucketed', 'classification': obj.get('classification') or 'unknown', 'triage_reason': obj.get('triage_reason') or 'unknown', 'target_implementation': obj.get('target_implementation') or 'unknown', 'source_run_id': obj.get('source_run_id') or '', 'replay_state': obj.get('replay_state') or 'none', 'compare_state': obj.get('compare_state') or 'none', 'minimization_state': obj.get('minimization_state') or 'none', 'scenario_id': metadata.get('scenario_id') or '', 'runtime': metadata.get('runtime') or '', 'behavior_signature': metadata.get('behavior_signature') or '', 'resource_signature': metadata.get('resource_signature') or ''})\n"
        "bucket_groups = {}\n"
        "for case in cases:\n"
        "    bucket = bucket_groups.setdefault(case['bucket_id'], {'bucket_id': case['bucket_id'], 'classification': case['classification'], 'triage_reason': case['triage_reason'], 'target_implementation': case['target_implementation'], 'case_count': 0, 'pending_replay_count': 0, 'pending_compare_count': 0, 'complete_minimization_count': 0})\n"
        "    bucket['case_count'] += 1\n"
        "    if case['replay_state'] == 'pending':\n"
        "        bucket['pending_replay_count'] += 1\n"
        "    if case['compare_state'] == 'pending':\n"
        "        bucket['pending_compare_count'] += 1\n"
        "    if case['minimization_state'] == 'complete':\n"
        "        bucket['complete_minimization_count'] += 1\n"
        "top_buckets = sorted(bucket_groups.values(), key=lambda row: (0 if row['classification'] == 'runtime_anomaly' else 1, -row['case_count'], row['triage_reason'], row['bucket_id']))[:8]\n"
        "recent_cases = sorted(cases, key=lambda row: (1 if row['classification'] == 'runtime_anomaly' else 0, row['source_run_id'], row['case_id']), reverse=True)[:10]\n"
        "runtime_buckets = [row for row in top_buckets if row['classification'] == 'runtime_anomaly']\n"
        "fuzz_buckets = [row for row in top_buckets if row['classification'] != 'runtime_anomaly']\n"
        "recent_runtime_cases = [row for row in recent_cases if row['classification'] == 'runtime_anomaly'][:10]\n"
        "recent_fuzz_cases = [row for row in recent_cases if row['classification'] != 'runtime_anomaly'][:10]\n"
        "compare_rows = read_ndjson_rows(state_root / 'compare-queue.ndjson')\n"
        "replay_rows = read_ndjson_rows(state_root / 'replay-queue.ndjson')\n"
        "payload = {'available': True, 'source': 'remote', 'state_root': str(state_root), 'case_count': len(cases), 'bucket_count': len(bucket_groups), 'runtime_anomaly_count': sum(1 for case in cases if case['classification'] == 'runtime_anomaly'), 'pending_compare_count': sum(1 for row in compare_rows if row.get('state') == 'pending'), 'pending_replay_count': sum(1 for row in replay_rows if row.get('state') == 'pending'), 'top_buckets': top_buckets, 'runtime_buckets': runtime_buckets, 'fuzz_buckets': fuzz_buckets, 'recent_cases': recent_cases, 'recent_runtime_cases': recent_runtime_cases, 'recent_fuzz_cases': recent_fuzz_cases}\n"
        "print(json.dumps(payload))\n"
        "PY"
    )
    result = ssh_command(cfg, remote_command, timeout=20)
    if result.returncode != 0:
        summary = _local_testcase_lifecycle_summary()
        summary["remote_error"] = result.stderr.strip() or result.stdout.strip() or "remote summary failed"
        return summary
    try:
        return json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError:
        summary = _local_testcase_lifecycle_summary()
        summary["remote_error"] = "remote summary parse failed"
        return summary
