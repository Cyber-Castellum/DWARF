"""Issue-families aggregator for /operate/runs.

Reads the testcase lifecycle state already produced by the framework and
returns a render-ready list of (classification, triage_reason, count)
tuples for the runtime-anomaly subset only — the operator-visible signal
is "what kinds of anomalies are showing up across recent runs?", not the
full bucket catalogue (that lives at /operate/runs/<id> and the future
lifecycle dashboard).

Source of truth: data.lifecycle._summarize_testcase_state.
"""
from __future__ import annotations

from typing import Any


def issue_families(payload_lifecycle: dict[str, Any]) -> list[dict[str, Any]]:
    """Group runtime-anomaly buckets by triage_reason; return sorted rows.

    Returns a list of:
        {
            "triage_reason": str,
            "target_implementation": str,
            "case_count": int,
            "bucket_count": int,
            "pending_replay_count": int,
        }

    sorted by case_count descending then triage_reason ascending. Limit 6.
    """
    if not payload_lifecycle or not payload_lifecycle.get("available"):
        return []
    runtime_buckets = payload_lifecycle.get("runtime_buckets") or []
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for bucket in runtime_buckets:
        reason = bucket.get("triage_reason") or "unknown"
        impl = bucket.get("target_implementation") or "unknown"
        key = (reason, impl)
        row = grouped.setdefault(key, {
            "triage_reason": reason,
            "target_implementation": impl,
            "case_count": 0,
            "bucket_count": 0,
            "pending_replay_count": 0,
        })
        row["case_count"] += int(bucket.get("case_count") or 0)
        row["bucket_count"] += 1
        row["pending_replay_count"] += int(bucket.get("pending_replay_count") or 0)
    rows = list(grouped.values())
    rows.sort(key=lambda r: (-r["case_count"], r["triage_reason"], r["target_implementation"]))
    return rows[:6]
