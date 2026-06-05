"""Health-check + Prometheus metrics for the dashboard.

Two operational hooks for production observability:

- ``healthz_payload()`` returns a JSON dict with a ``status`` field and
  per-component ok/fail breakdown so a load-balancer or supervisor can
  probe liveness without pulling the full /api/health blob.
- ``prometheus_exposition()`` returns the Prometheus text exposition
  format (https://prometheus.io/docs/instrumenting/exposition_formats/)
  with request counters, run outcome counters, assertion totals,
  substrate compose mode counters, bundle size sum/count, and uptime.

Counter state lives at module scope and is mutated by the dashboard
request handler (``record_request``) and lazily refreshed from disk
when ``/metrics`` is hit (cheap glob over ``runs/`` directory).
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any


_START_TIME = time.monotonic()
_REQUEST_LOCK = threading.Lock()
_REQUEST_COUNTS: Counter[tuple[str, str, int]] = Counter()


def _runs_dir() -> Path:
    env = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "runs"


def _state_dir(runs_dir: Path) -> Path:
    env = os.environ.get("ADA2_DWARF_STATE_DIR")
    if env:
        return Path(env)
    return runs_dir.parent / "state"


def _path_bucket(path: str) -> str:
    """Collapse ID-bearing paths so the request-counter cardinality
    stays bounded. Without this every distinct ``/operate/runs/<id>``
    would emit a unique label set, swamping Prometheus."""
    base = path.split("?", 1)[0]
    if base.startswith("/operate/runs/") and base.count("/") >= 3:
        return "/operate/runs/<id>"
    if base.startswith("/operate/compare/runs"):
        return "/operate/compare/runs"
    if base.startswith("/runs/") and base != "/runs/":
        return "/runs/<id>"
    if base.startswith("/static/"):
        return "/static/*"
    if base.startswith("/api/"):
        return base  # /api/* is a fixed enumerable set
    return base


def record_request(method: str, path: str, status: int) -> None:
    """Mutate the in-process request counter. Called from the dashboard
    handler after each response. Cheap (single dict update behind a
    lock) and the cardinality is bounded by the path-bucket function."""
    bucket = _path_bucket(path)
    with _REQUEST_LOCK:
        _REQUEST_COUNTS[(method, bucket, status)] += 1


def _check_dir_exists(p: Path) -> str:
    return "ok" if p.is_dir() else "fail"


def _check_dir_writable(p: Path) -> str:
    if not p.is_dir():
        return "fail"
    return "ok" if os.access(str(p), os.W_OK) else "fail"


def _check_dir_readable(p: Path) -> str:
    if not p.is_dir():
        return "fail"
    return "ok" if os.access(str(p), os.R_OK) else "fail"


def healthz_payload(*, runs_dir: Path | None = None) -> dict[str, Any]:
    """Light-weight liveness payload. ``status`` is "ok" iff every
    component is "ok"; anything else degrades to "fail". Designed to
    be cheap enough that load-balancer probes can hit it every few
    seconds without measurable impact."""
    base = Path(runs_dir) if runs_dir is not None else _runs_dir()
    state = _state_dir(base)
    components = {
        "dashboard": "ok",
        "runs_dir": _check_dir_writable(base),
        "state_dir": _check_dir_readable(state),
    }
    overall = "ok" if all(v == "ok" for v in components.values()) else "fail"
    return {
        "status": overall,
        "components": components,
        "uptime_seconds": time.monotonic() - _START_TIME,
    }


def _scan_run_outcomes(base: Path) -> dict[str, int]:
    """Walk runs/<id>/manifest.json and bucket exit_status. Cheap enough
    for a /metrics probe — single mtime-stat per dir + one JSON read.
    On large bundle directories this could be cached, but for the
    current workspace (hundreds of runs) it stays fast."""
    counts: dict[str, int] = {}
    if not base.is_dir():
        return counts
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        manifest = entry / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        outcome = (payload.get("exit_status") or "other") or "other"
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def _scan_assertion_totals(base: Path) -> dict[str, int]:
    """Sum per-bundle assertion_summary.{pass,fail,total} across runs."""
    pass_total = 0
    fail_total = 0
    if not base.is_dir():
        return {"pass": 0, "fail": 0}
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        manifest = entry / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        summary = payload.get("assertion_summary") or {}
        pass_total += int(summary.get("pass") or 0)
        fail_total += int(summary.get("fail") or 0)
    return {"pass": pass_total, "fail": fail_total}


def _scan_substrate_compose_modes(base: Path) -> dict[str, int]:
    """Walk runs/<id>/outputs/substrate-compose/compose-report.json,
    bucket each by 'mode' (host / docker / multi-host). Mode is read
    verbatim from the report; missing values bucket as 'unknown'."""
    counts: dict[str, int] = {}
    if not base.is_dir():
        return counts
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        report = entry / "outputs" / "substrate-compose" / "compose-report.json"
        if not report.is_file():
            continue
        try:
            payload = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        mode = payload.get("mode") or "unknown"
        counts[mode] = counts.get(mode, 0) + 1
    return counts


def _scan_bundle_sizes(base: Path) -> tuple[int, int]:
    """Return (total_bytes, count) over runs/<id>/<file> trees."""
    total = 0
    count = 0
    if not base.is_dir():
        return (0, 0)
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        size = 0
        for f in entry.rglob("*"):
            if f.is_file():
                try:
                    size += f.stat().st_size
                except OSError:
                    continue
        total += size
        count += 1
    return total, count


def _esc(value: str) -> str:
    """Prometheus label-value escaping: backslash, quote, newline."""
    return value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")


def _format_counter(name: str, help_text: str, samples: list[tuple[dict[str, str], float]]) -> str:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} counter"]
    for labels, value in samples:
        if labels:
            label_str = ",".join(f'{k}="{_esc(v)}"' for k, v in sorted(labels.items()))
            lines.append(f"{name}{{{label_str}}} {value}")
        else:
            lines.append(f"{name} {value}")
    return "\n".join(lines)


def _format_gauge(name: str, help_text: str, value: float) -> str:
    return f"# HELP {name} {help_text}\n# TYPE {name} gauge\n{name} {value}"


def prometheus_exposition(*, runs_dir: Path | None = None) -> bytes:
    """Render the full /metrics payload as Prometheus text format."""
    base = Path(runs_dir) if runs_dir is not None else _runs_dir()

    # Snapshot the request counter under the lock so concurrent
    # increments don't tear the iteration.
    with _REQUEST_LOCK:
        request_samples = [
            ({"method": method, "path": path, "status": str(status)}, count)
            for (method, path, status), count in _REQUEST_COUNTS.items()
        ]

    outcomes = _scan_run_outcomes(base)
    assertions = _scan_assertion_totals(base)
    compose_modes = _scan_substrate_compose_modes(base)
    bundle_total, bundle_count = _scan_bundle_sizes(base)

    parts: list[str] = []
    parts.append(_format_counter(
        "dwarf_dashboard_requests_total",
        "Count of HTTP requests served by the dashboard, bucketed by method/path/status.",
        request_samples,
    ))
    parts.append(_format_counter(
        "dwarf_runs_total",
        "Count of recorded runs by manifest.exit_status outcome.",
        [({"outcome": outcome}, n) for outcome, n in outcomes.items()],
    ))
    parts.append(_format_counter(
        "dwarf_assertions_pass_total",
        "Total assertions across all runs whose result is pass.",
        [({}, assertions["pass"])],
    ))
    parts.append(_format_counter(
        "dwarf_assertions_fail_total",
        "Total assertions across all runs whose result is fail.",
        [({}, assertions["fail"])],
    ))
    parts.append(_format_counter(
        "dwarf_substrate_compose_total",
        "Count of substrate-compose runs by mode (host / docker / multi-host / unknown).",
        [({"mode": mode}, n) for mode, n in compose_modes.items()],
    ))
    parts.append(_format_gauge(
        "dwarf_bundle_size_bytes",
        "Sum of bytes across all run-bundle directories.",
        bundle_total,
    ))
    parts.append(_format_gauge(
        "dwarf_bundle_count",
        "Count of run-bundle directories observed.",
        bundle_count,
    ))
    parts.append(_format_gauge(
        "dwarf_dashboard_uptime_seconds",
        "Seconds since the dashboard process started serving.",
        time.monotonic() - _START_TIME,
    ))
    body = "\n".join(parts) + "\n"
    return body.encode("utf-8")
