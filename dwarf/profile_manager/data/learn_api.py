"""REST API reference data (slice 2 of dispatch 8).

Curated endpoint catalog rather than runtime route-table introspection
because many routes mix HTML/JSON or wrap dispatchers; a hand-listed
spec is more accurate AND easier to keep correct than reflection over
the dispatcher.
"""
from __future__ import annotations

from typing import Any


# Each endpoint: path, method(s), kind (json/sse/html-only/binary),
# description, parameters, response, example (str).
ENDPOINTS: list[dict[str, Any]] = [
    {
        "path": "/healthz",
        "aliases": ["/health"],
        "method": "GET",
        "kind": "json",
        "description": "Lightweight liveness probe. 200 when every component is ok; 503 otherwise. Designed for load-balancer / supervisor probes — distinct from /api/health which carries the full substrate payload.",
        "parameters": [],
        "response_schema": {
            "status": "ok | fail",
            "components": {"dashboard": "ok|fail", "runs_dir": "ok|fail", "state_dir": "ok|fail"},
            "uptime_seconds": "number",
        },
        "example": '{"status":"ok","components":{"dashboard":"ok","runs_dir":"ok","state_dir":"ok"},"uptime_seconds":42.18}',
    },
    {
        "path": "/metrics",
        "aliases": [],
        "method": "GET",
        "kind": "prometheus",
        "description": "Prometheus text-exposition format (https://prometheus.io/docs/instrumenting/exposition_formats/). Eight metric families: requests_total, runs_total, assertions_pass_total, assertions_fail_total, substrate_compose_total, bundle_size_bytes, bundle_count, dashboard_uptime_seconds.",
        "parameters": [],
        "response_schema": {"_": "Prometheus text format — see https://prometheus.io/docs/concepts/data_model/"},
        "example": (
            "# HELP dwarf_runs_total Count of recorded runs by manifest.exit_status outcome.\n"
            "# TYPE dwarf_runs_total counter\n"
            "dwarf_runs_total{outcome=\"pass\"} 945\n"
            "dwarf_runs_total{outcome=\"fail\"} 135\n"
            "..."
        ),
    },
    {
        "path": "/api/health",
        "aliases": [],
        "method": "GET",
        "kind": "json",
        "description": "Full substrate-status payload — same shape as /api/status. Carries live SSH-poll output, last-cached evidence, profile metadata. Heavier than /healthz; use this when you need the substrate snapshot, not for probing.",
        "parameters": [],
        "response_schema": {"_": "Full DashboardStatus dict — see profile_manager.dashboard.build_dashboard_status_payload"},
        "example": '{"live": {...}, "profiles": [...], "last_local_health": {...}}',
    },
    {
        "path": "/api/status",
        "aliases": [],
        "method": "GET",
        "kind": "json",
        "description": "Identical payload to /api/health. Predates the rename; kept as a stable alias for the legacy live-runtime card.",
        "parameters": [],
        "response_schema": {"_": "see /api/health"},
        "example": "(same as /api/health)",
    },
    {
        "path": "/api/runs",
        "aliases": [],
        "method": "GET",
        "kind": "json",
        "description": "Recent-runs payload over the local + remote run sources. Backs the /operate/runs page.",
        "parameters": [
            {"name": "limit", "kind": "query", "required": False, "type": "integer", "default": "100"},
        ],
        "response_schema": {"recent_runs": "[{run_id, ended_at, scenario_id, exit_status, runtime, source}, ...]"},
        "example": '{"recent_runs": [{"run_id": "20260427T154920Z-4bdcb76f", "exit_status": "pass", "scenario_id": "honest-baseline-smoke", ...}]}',
    },
    {
        "path": "/operate/runs/<id>/tail",
        "aliases": [],
        "method": "GET",
        "kind": "sse",
        "description": "Server-Sent Events stream of the run's log.ndjson. Emits a `hello` event with the run-id, then every existing log line as a `log` event, then live `log` events as the file grows. Closes with `end` (reason=manifest) when manifest.json appears or `end` (reason=idle_timeout) after 600s of silence. Heartbeat SSE comments every poll.",
        "parameters": [
            {"name": "id", "kind": "path", "required": True, "type": "string", "default": ""},
        ],
        "response_schema": {"_": "text/event-stream — events: hello, log, end, error"},
        "example": "event: hello\ndata: 20260427T154920Z-4bdcb76f\n\nevent: log\ndata: {\"event\":\"started\",...}\n\nevent: end\ndata: manifest\n",
    },
    {
        "path": "/runs/<id>/bundle",
        "aliases": [],
        "method": "GET",
        "kind": "binary",
        "description": "Download a run's forensic bundle as tar.gz. Streams the archive verbatim. Powers the 'Export bundle' button on the run inspector.",
        "parameters": [
            {"name": "id", "kind": "path", "required": True, "type": "string", "default": ""},
        ],
        "response_schema": {"_": "application/gzip — full run-bundle archive"},
        "example": "(binary tar.gz)",
    },
    {
        "path": "/api/bundle/import",
        "aliases": [],
        "method": "POST",
        "kind": "html-result",
        "description": "Multipart upload of a previously-exported bundle.tar.gz. The framework verifies the bundle's hash chain before unpacking it into runs/<run_id>/. Existing run-ids are NOT overwritten.",
        "parameters": [
            {"name": "bundle", "kind": "form-multipart", "required": True, "type": "file", "default": ""},
        ],
        "response_schema": {"_": "HTML result page with helper stdout/stderr"},
        "example": "(HTML)",
    },
    {
        "path": "/runs/<id>/output",
        "aliases": [],
        "method": "GET",
        "kind": "binary",
        "description": "Per-artifact download from a run bundle's outputs/ tree. Path-traversal-guarded: only files under runs/<id>/outputs/ resolve.",
        "parameters": [
            {"name": "id", "kind": "path", "required": True, "type": "string", "default": ""},
            {"name": "path", "kind": "query", "required": True, "type": "string", "default": ""},
        ],
        "response_schema": {"_": "the artifact bytes; content-type sniffed from extension"},
        "example": "(binary)",
    },
    {
        "path": "/api/deploy /api/remove /api/fuzz/run /api/test/smoke/run /api/scenario/run /api/scenario/compare /api/scenario/paste /api/scenario/promote",
        "aliases": [],
        "method": "POST",
        "kind": "sse-or-text",
        "description": "Mutating action endpoints, each gated behind the dashboard token. Long-running ones (deploy, fuzz, scenario_run) stream their stdout/stderr as SSE; short ones (paste, promote) return text. GET on these paths returns 405. A global mutating-lock serializes them so two operators can't deploy simultaneously.",
        "parameters": [
            {"name": "token", "kind": "query", "required": True, "type": "string", "default": ""},
            {"name": "(see CLI reference for per-endpoint params)", "kind": "form-or-query", "required": False, "type": "varies", "default": ""},
        ],
        "response_schema": {"_": "text/event-stream OR text/plain — see cli docs"},
        "example": "(SSE)",
    },
]


def html_route_groups() -> list[dict[str, Any]]:
    """The HTML-only routes the dashboard surfaces, grouped by namespace
    so the /learn/api page can render one card per group instead of
    one giant bullet list."""
    operate = [
        "/operate", "/operate/runs", "/operate/runs/<id>", "/operate/runs/<id>/live",
        "/operate/scenarios", "/operate/scenarios/new",
        "/operate/compare", "/operate/compare/runs",
        "/operate/profiles", "/operate/bundles", "/operate/targets",
        "/operate/status", "/operate/coverage", "/operate/timeline",
        "/operate/static-analysis", "/operate/contract",
        "/operate/plugins", "/operate/config", "/operate/notifications",
    ]
    learn = [
        "/learn", "/learn/getting-started", "/learn/examples",
        "/learn/walkthroughs", "/learn/architecture",
        "/learn/concepts", "/learn/glossary",
        "/learn/api", "/learn/faq", "/learn/troubleshooting",
        "/learn/coverage", "/learn/status", "/learn/cli",
    ]
    top_level = ["/"]
    return [
        {"label": "Operate", "routes": operate, "count": len(operate)},
        {"label": "Learn", "routes": learn, "count": len(learn)},
        {"label": "Top-level", "routes": top_level, "count": len(top_level)},
    ]


def api_payload() -> dict[str, Any]:
    groups = html_route_groups()
    html_count = sum(g["count"] for g in groups)
    # Machine-readable count: strict-JSON + Prometheus (treat /metrics as
    # machine-readable text). Excludes SSE streams and binary downloads.
    machine_readable = sum(
        1 for e in ENDPOINTS if e["kind"] in ("json", "prometheus")
    )
    return {
        "endpoints": ENDPOINTS,
        "html_route_groups": groups,
        "html_count": html_count,
        "machine_readable_count": machine_readable,
        "json_count": sum(1 for e in ENDPOINTS if e["kind"] == "json"),
        "sse_count": sum(1 for e in ENDPOINTS if e["kind"] in ("sse", "sse-or-text")),
        "binary_count": sum(1 for e in ENDPOINTS if e["kind"] == "binary"),
    }
