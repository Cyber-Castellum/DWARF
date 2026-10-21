"""Deterministic aggregation and run-bundle output for DWARF measurements."""
from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _nearest_rank(values: list[float], percentile: float) -> float:
    rank = max(1, math.ceil(percentile * len(values)))
    return sorted(values)[rank - 1]


def distribution_summary(
    samples: Iterable[dict[str, Any]],
    *,
    unit: str | None = None,
    warmup_seconds: float = 0,
    minimum_samples: int = 1,
) -> dict[str, Any]:
    """Summarize accepted numeric samples without treating missing as zero."""
    values: list[float] = []
    inferred_unit = unit
    excluded_warmup = 0
    rejected = Counter()
    for sample in samples:
        elapsed = _number(sample.get("elapsed_seconds"))
        if elapsed is not None and elapsed < warmup_seconds:
            excluded_warmup += 1
            continue
        if sample.get("accepted", True) is False:
            rejected[str(sample.get("rejection_reason") or "rejected")] += 1
            continue
        sample_unit = sample.get("unit")
        if inferred_unit is None and isinstance(sample_unit, str):
            inferred_unit = sample_unit
        if inferred_unit is not None and sample_unit not in {None, inferred_unit}:
            rejected["unit-mismatch"] += 1
            continue
        value = _number(sample.get("value"))
        if value is None:
            rejected[str(sample.get("rejection_reason") or "non-numeric")] += 1
            continue
        values.append(value)

    base = {
        "status": "unavailable" if not values else (
            "available" if len(values) >= minimum_samples else "insufficient-samples"
        ),
        "unit": inferred_unit,
        "sample_count": len(values),
        "excluded_warmup_count": excluded_warmup,
        "rejected_count": sum(rejected.values()),
        "rejection_reasons": dict(sorted(rejected.items())),
        "mean": statistics.fmean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "p95": None,
        "p99": None,
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
    }
    if len(values) >= minimum_samples:
        base["p95"] = _nearest_rank(values, 0.95)
        base["p99"] = _nearest_rank(values, 0.99)
    return base


def rate_summary(
    events: Iterable[dict[str, Any]],
    *,
    duration_seconds: float,
    unit: str,
) -> dict[str, Any]:
    duration = _number(duration_seconds)
    if duration is None or duration <= 0:
        return {
            "status": "unavailable",
            "unit": unit,
            "duration_seconds": duration,
            "reason": "a positive observation duration is required",
        }
    offered = 0.0
    accepted = 0.0
    rejected = 0.0
    reasons: Counter[str] = Counter()
    for event in events:
        amount = _number(event.get("value"))
        if amount is None or amount < 0:
            continue
        offered += amount
        if event.get("accepted", True) is False:
            rejected += amount
            reasons[str(event.get("rejection_reason") or "rejected")] += amount
        else:
            accepted += amount
    return {
        "status": "available",
        "unit": unit,
        "duration_seconds": duration,
        "offered_count": offered,
        "accepted_count": accepted,
        "rejected_count": rejected,
        "offered_rate": offered / duration,
        "accepted_rate": accepted / duration,
        "rejected_rate": rejected / duration,
        "rejection_reasons": dict(sorted(reasons.items())),
    }


def backlog_summary(samples: Iterable[dict[str, Any]]) -> dict[str, Any]:
    points = []
    unit = None
    for sample in samples:
        elapsed = _number(sample.get("elapsed_seconds"))
        value = _number(sample.get("value"))
        if elapsed is None or value is None:
            continue
        if unit is None:
            unit = sample.get("unit")
        points.append((elapsed, value))
    points.sort()
    if not points:
        return {
            "status": "unavailable",
            "unit": unit,
            "peak": None,
            "peak_at_seconds": None,
            "growth_rate_per_second": None,
            "drain_time_seconds": None,
        }
    start_time, baseline = points[0]
    peak_index, (peak_time, peak) = max(enumerate(points), key=lambda item: item[1][1])
    rise_seconds = peak_time - start_time
    growth = (peak - baseline) / rise_seconds if rise_seconds > 0 else None
    drained_at = next(
        (elapsed for elapsed, value in points[peak_index + 1 :] if value <= baseline),
        None,
    )
    return {
        "status": "available",
        "unit": unit,
        "sample_count": len(points),
        "baseline": baseline,
        "peak": peak,
        "peak_at_seconds": peak_time,
        "growth_rate_per_second": growth,
        "drain_time_seconds": (
            drained_at - peak_time if drained_at is not None else None
        ),
    }


def recovery_summary(
    samples: Iterable[dict[str, Any]],
    *,
    fault_removed_at: float,
    baseline_value: float,
    recovery_fraction: float = 0.9,
    unit: str | None = None,
) -> dict[str, Any]:
    removed = _number(fault_removed_at)
    baseline = _number(baseline_value)
    fraction = _number(recovery_fraction)
    if removed is None or baseline is None or fraction is None or fraction <= 0:
        return {
            "status": "unavailable",
            "unit": unit,
            "recovery_time_seconds": None,
            "reason": "valid fault, baseline, and recovery fraction are required",
        }
    threshold = baseline * fraction
    recovered_at = None
    for sample in sorted(samples, key=lambda item: item.get("elapsed_seconds", 0)):
        elapsed = _number(sample.get("elapsed_seconds"))
        value = _number(sample.get("value"))
        if elapsed is not None and value is not None and elapsed >= removed and value >= threshold:
            recovered_at = elapsed
            break
    return {
        "status": "available" if recovered_at is not None else "not-recovered",
        "unit": unit,
        "baseline": baseline,
        "recovery_fraction": fraction,
        "recovery_threshold": threshold,
        "fault_removed_at_seconds": removed,
        "recovered_at_seconds": recovered_at,
        "recovery_time_seconds": (
            recovered_at - removed if recovered_at is not None else None
        ),
    }


def degradation_summary(
    *, baseline_value: float, hostile_value: float, unit: str | None = None
) -> dict[str, Any]:
    baseline = _number(baseline_value)
    hostile = _number(hostile_value)
    if baseline is None or hostile is None or baseline == 0:
        return {
            "status": "unavailable",
            "unit": unit,
            "absolute_delta": None,
            "percent_delta": None,
            "reason": "finite non-zero baseline and hostile values are required",
        }
    delta = hostile - baseline
    return {
        "status": "available",
        "unit": unit,
        "baseline": baseline,
        "hostile": hostile,
        "absolute_delta": delta,
        "percent_delta": (delta / baseline) * 100,
    }


def _compact_summary(metric: dict[str, Any]) -> dict[str, Any]:
    """Return the row-level summary for a metric.

    Outcome-partitioned metrics carry their aggregate under ``all`` and hold no
    top-level ``status``. Reading the outer dict directly would score them as
    unavailable with zero samples even when the aggregate has data.
    """
    if "status" not in metric and isinstance(metric.get("all"), dict):
        return metric["all"]
    return metric


def _compact_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for name, raw in (report.get("measurements") or {}).items():
        metric = _compact_summary(raw) if isinstance(raw, dict) else {}
        rows.append(
            {
                "metric": name,
                "status": metric.get("status", "unavailable"),
                "unit": metric.get("unit"),
                "sample_count": metric.get("sample_count", 0),
                "mean": metric.get("mean"),
                "median": metric.get("median"),
                "p95": metric.get("p95"),
                "p99": metric.get("p99"),
                "reason": metric.get("reason"),
            }
        )
    return rows


def _readable_report(report: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        "# DWARF measurement report",
        "",
        f"Scenario: {report.get('scenario') or 'unknown'}",
        f"Duration: {report.get('duration_seconds') if report.get('duration_seconds') is not None else 'unavailable'} seconds",
        "",
        "| Metric | Status | Samples | Mean | Median | p95 | p99 | Unit |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        def shown(key: str) -> Any:
            value = row.get(key)
            return value if value is not None else "—"

        lines.append(
            "| {metric} | {status} | {sample_count} | {mean} | {median} | {p95} | {p99} | {unit} |".format(
                metric=row["metric"],
                status=row["status"],
                sample_count=row["sample_count"],
                mean=shown("mean"),
                median=shown("median"),
                p95=shown("p95"),
                p99=shown("p99"),
                unit=shown("unit"),
            )
        )
        if row.get("reason"):
            lines.append(f"  - {row['metric']}: {row['reason']}")
    return "\n".join(lines) + "\n"


def write_measurement_reports(
    run_dir: str | Path, report: dict[str, Any]
) -> dict[str, str]:
    run_dir = Path(run_dir)
    output = run_dir / "measurements"
    output.mkdir(parents=True, exist_ok=True)
    rows = _compact_rows(report)
    compact = {"schema_version": "v1", "rows": rows}
    available = sum(1 for row in rows if row["status"] == "available")
    summary = {
        "schema_version": "v1",
        "scenario": report.get("scenario"),
        "duration_seconds": report.get("duration_seconds"),
        "metric_count": len(rows),
        "available_count": available,
        "unavailable_count": len(rows) - available,
    }
    artifacts = {
        "summary": "measurements/summary.json",
        "report": "measurements/report.json",
        "readable": "measurements/report.md",
        "compact_table": "measurements/compact-table.json",
    }
    (run_dir / artifacts["summary"]).write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (run_dir / artifacts["report"]).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (run_dir / artifacts["compact_table"]).write_text(
        json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (run_dir / artifacts["readable"]).write_text(
        _readable_report(report, rows), encoding="utf-8"
    )
    return artifacts

