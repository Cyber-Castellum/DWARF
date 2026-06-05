#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _decode(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _parse_clippy(stdout: str) -> dict:
    findings = []
    line_count = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        line_count += 1
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("reason") != "compiler-message":
            continue
        message = payload.get("message") or {}
        spans = message.get("spans") or []
        first_span = spans[0] if spans else {}
        findings.append(
            {
                "level": message.get("level"),
                "code": ((message.get("code") or {}).get("code")),
                "message": message.get("message"),
                "rendered": message.get("rendered"),
                "target_src_path": ((payload.get("target") or {}).get("src_path")),
                "span_file_name": first_span.get("file_name"),
                "span_line_start": first_span.get("line_start"),
            }
        )
    return {
        "findings": findings,
        "findings_count": len(findings),
        "parsed_line_count": line_count,
    }


def _parse_audit(stdout: str) -> dict:
    body = json.loads(stdout) if stdout.strip() else {}
    findings = []
    vulnerabilities = body.get("vulnerabilities") or {}
    for item in vulnerabilities.get("list") or []:
        advisory = item.get("advisory") or {}
        findings.append(
            {
                "id": advisory.get("id"),
                "package": ((item.get("package") or {}).get("name")),
                "title": advisory.get("title"),
                "severity": advisory.get("severity"),
                "url": advisory.get("url"),
            }
        )
    return {
        "raw": body,
        "findings": findings,
        "findings_count": len(findings),
    }


def _parse_deny(stdout: str) -> dict:
    stdout = stdout.strip()
    if not stdout:
        return {"raw": None, "findings": [], "findings_count": 0}
    try:
        body = json.loads(stdout)
        messages = body if isinstance(body, list) else [body]
    except json.JSONDecodeError:
        messages = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        body = messages
    findings = []
    for item in messages:
        fields = item.get("fields") or {}
        findings.append(
            {
                "type": item.get("type"),
                "severity": fields.get("severity") or item.get("severity"),
                "code": fields.get("code"),
                "message": fields.get("message") or item.get("message"),
            }
        )
    return {
        "raw": body,
        "findings": findings,
        "findings_count": len(findings),
    }


TOOLS = {
    "clippy": {
        "command": ["cargo", "clippy", "--message-format=json", "--quiet"],
        "parser": _parse_clippy,
    },
    "audit": {
        "command": ["cargo", "audit", "--json"],
        "parser": _parse_audit,
    },
    "deny": {
        "command": ["cargo", "deny", "check", "--format", "json"],
        "parser": _parse_deny,
    },
}


def run_tool(*, tool: str, crate_dir: Path, output_dir: Path) -> dict:
    if tool not in TOOLS:
        raise ValueError(f"unsupported static analysis tool: {tool}")
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = output_dir / "stdout.log"
    stderr_path = output_dir / "stderr.log"
    findings_path = output_dir / "findings.json"
    command = TOOLS[tool]["command"]
    parser = TOOLS[tool]["parser"]
    env = os.environ.copy()
    cargo_bin = Path.home() / ".cargo" / "bin"
    entries = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry]
    if str(cargo_bin) not in entries:
        env["PATH"] = os.pathsep.join([str(cargo_bin), *entries]) if entries else str(cargo_bin)

    proc = subprocess.run(
        command,
        cwd=crate_dir,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    stdout = _decode(proc.stdout)
    stderr = _decode(proc.stderr)
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")

    tool_status = "clean"
    parse_error = None
    parsed = {"findings": [], "findings_count": 0}
    if "no such command:" in stderr:
        tool_status = "tool_unavailable"
    else:
        try:
            parsed = parser(stdout)
            if proc.returncode != 0:
                tool_status = "findings"
        except Exception as exc:  # noqa: BLE001
            tool_status = "parse_error"
            parse_error = str(exc)

    payload = {
        "tool": tool,
        "crate_dir": str(crate_dir),
        "command": command,
        "tool_exit_code": proc.returncode,
        "tool_status": tool_status,
        "parse_error": parse_error,
        "stdout_relpath": str(stdout_path),
        "stderr_relpath": str(stderr_path),
        "findings_relpath": str(findings_path),
        **parsed,
    }
    findings_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload
