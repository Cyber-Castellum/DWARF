"""Pure data extractors for /operate/static-analysis.

Slice 33 surfaces ada3's three static-analysis primitives (clippy, audit,
deny) as a top-level dashboard surface so operators can see at a glance
whether each tool's most recent run was clean, what the on-disk record
says, and which crate it covered. The tools share a uniform output
contract:

    outputs/static-analysis-<tool>/findings.json
    outputs/static-analysis-<tool>/stdout.log
    outputs/static-analysis-<tool>/stderr.log

findings.json carries: tool, crate_dir, command, tool_exit_code,
tool_status, findings (array), findings_count, executed_at_utc,
plus *_relpath fields pointing at the sibling logs.

This module reads those JSON blobs verbatim — Dwarf does not interpret
or re-categorize. Pass-through field names (`findings`, `findings_count`,
`tool_status`) are preserved because they are part of ada3's primitive
schema; the page's own prose uses neutral labels.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


TOOLS: tuple[str, ...] = ("clippy", "audit", "deny")


def _forensic_runs_dir() -> Path:
    env = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "runs"


def _bundle_inspector_url(run_id: str) -> str:
    return f"/operate/runs/{run_id}"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_text_tail(path: Path, *, max_bytes: int = 4096) -> str:
    """Read up to max_bytes from the end of the file. The page renders
    these in collapsed <details> elements so a tail keeps the payload
    bounded without losing the "what went wrong" signal."""
    if not path.is_file():
        return ""
    try:
        with path.open("rb") as fp:
            try:
                fp.seek(0, 2)
                size = fp.tell()
                if size > max_bytes:
                    fp.seek(size - max_bytes)
                else:
                    fp.seek(0)
                data = fp.read()
            except OSError:
                return ""
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def _tool_artifacts(run_dir: Path, tool: str) -> dict[str, Any] | None:
    """Detect the per-tool output directory in a run. Returns None when
    absent so the caller can skip the run."""
    sd = run_dir / "outputs" / f"static-analysis-{tool}"
    if not sd.is_dir():
        return None
    findings = sd / "findings.json"
    stdout = sd / "stdout.log"
    stderr = sd / "stderr.log"
    return {
        "dir": sd,
        "findings_path": findings,
        "stdout_path": stdout,
        "stderr_path": stderr,
        "findings_relpath": f"outputs/static-analysis-{tool}/findings.json",
        "stdout_relpath": f"outputs/static-analysis-{tool}/stdout.log",
        "stderr_relpath": f"outputs/static-analysis-{tool}/stderr.log",
        "has_findings": findings.is_file(),
        "has_stdout": stdout.is_file(),
        "has_stderr": stderr.is_file(),
    }


def _pill_state(payload: dict[str, Any]) -> str:
    """Map the tool's own status + exit code to one of three render
    states: ok / stale / error. Used only for the result-pill colour;
    the literal tool_status string is rendered alongside so operators
    see the verbatim verdict."""
    status = (payload.get("tool_status") or "").lower()
    if status == "clean" and payload.get("tool_exit_code") == 0:
        return "ok"
    if status == "tool_unavailable":
        return "stale"
    if payload.get("tool_exit_code") == 0:
        return "ok"
    return "error"


def _run_summary(run_id: str, run_dir: Path, tool: str, artifacts: dict[str, Any]) -> dict[str, Any]:
    payload = _read_json(artifacts["findings_path"]) or {}
    findings = payload.get("findings") or []
    return {
        "run_id": run_id,
        "run_url": _bundle_inspector_url(run_id),
        "tool": tool,
        "executed_at_utc": payload.get("executed_at_utc"),
        "tool_exit_code": payload.get("tool_exit_code"),
        "tool_status": payload.get("tool_status"),
        "pill_state": _pill_state(payload),
        "crate_dir": payload.get("crate_dir"),
        "command": list(payload.get("command") or []),
        "parse_error": payload.get("parse_error"),
        "parsed_line_count": payload.get("parsed_line_count"),
        "findings_count": payload.get("findings_count", len(findings)),
        "findings": list(findings),
        "findings_url": (
            f"/runs/{run_id}/output?path={artifacts['findings_relpath']}"
            if artifacts["has_findings"] else None
        ),
        "stdout_url": (
            f"/runs/{run_id}/output?path={artifacts['stdout_relpath']}"
            if artifacts["has_stdout"] else None
        ),
        "stderr_url": (
            f"/runs/{run_id}/output?path={artifacts['stderr_relpath']}"
            if artifacts["has_stderr"] else None
        ),
        "stderr_tail": _read_text_tail(artifacts["stderr_path"]),
    }


def latest_per_tool(*, runs_dir: Path | None = None) -> dict[str, dict[str, Any] | None]:
    """For each of clippy/audit/deny, return the most recent run that
    produced an outputs/static-analysis-<tool>/ directory, or None for
    tools that have never been run."""
    base = Path(runs_dir) if runs_dir is not None else _forensic_runs_dir()
    out: dict[str, dict[str, Any] | None] = {tool: None for tool in TOOLS}
    if not base.is_dir():
        return out
    candidates_by_tool: dict[str, list[tuple[str, Path, dict[str, Any]]]] = {tool: [] for tool in TOOLS}
    for p in base.iterdir():
        if not p.is_dir():
            continue
        for tool in TOOLS:
            artifacts = _tool_artifacts(p, tool)
            if artifacts is None:
                continue
            candidates_by_tool[tool].append((p.name, p, artifacts))
    for tool, candidates in candidates_by_tool.items():
        if not candidates:
            continue
        candidates.sort(key=lambda c: c[0], reverse=True)
        run_id, run_dir, artifacts = candidates[0]
        out[tool] = _run_summary(run_id, run_dir, tool, artifacts)
    return out


def history_per_tool(*, runs_dir: Path | None = None, limit_per_tool: int = 5) -> dict[str, list[dict[str, Any]]]:
    """Per-tool history (newest first), for the small history tables on
    each tool's column."""
    base = Path(runs_dir) if runs_dir is not None else _forensic_runs_dir()
    out: dict[str, list[dict[str, Any]]] = {tool: [] for tool in TOOLS}
    if not base.is_dir():
        return out
    by_tool: dict[str, list[tuple[str, Path, dict[str, Any]]]] = {tool: [] for tool in TOOLS}
    for p in base.iterdir():
        if not p.is_dir():
            continue
        for tool in TOOLS:
            artifacts = _tool_artifacts(p, tool)
            if artifacts is None:
                continue
            by_tool[tool].append((p.name, p, artifacts))
    for tool, candidates in by_tool.items():
        candidates.sort(key=lambda c: c[0], reverse=True)
        for run_id, run_dir, artifacts in candidates[:limit_per_tool]:
            payload = _read_json(artifacts["findings_path"]) or {}
            out[tool].append({
                "run_id": run_id,
                "run_url": _bundle_inspector_url(run_id),
                "executed_at_utc": payload.get("executed_at_utc"),
                "tool_status": payload.get("tool_status"),
                "tool_exit_code": payload.get("tool_exit_code"),
                "findings_count": payload.get("findings_count", len(payload.get("findings") or [])),
                "pill_state": _pill_state(payload),
                "crate_dir": payload.get("crate_dir"),
            })
    return out
