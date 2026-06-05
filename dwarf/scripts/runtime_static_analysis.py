#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from static_analysis_helpers import run_tool  # noqa: E402
from runtime_telemetry import emit_target_event  # noqa: E402


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _relative_artifact_path(path: Path) -> str:
    run_dir = Path.cwd()
    parts = path.parts
    if "outputs" in parts:
        idx = parts.index("outputs")
        return str(Path(*parts[idx:]))
    try:
        return str(path.relative_to(run_dir))
    except ValueError:
        return str(path)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run a static analysis cargo subcommand and capture findings")
    parser.add_argument("--tool", choices=("clippy", "audit", "deny"), required=True)
    parser.add_argument("--crate-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv[1:])

    crate_dir = Path(args.crate_dir)
    output_dir = Path(args.output_dir)
    emit_target_event(
        primitive=f"runtime_static_analysis_{args.tool}",
        event=f"static_analysis_{args.tool}_started",
        payload={
            "tool": args.tool,
            "crate_dir": str(crate_dir),
            "output_dir": str(output_dir),
        },
    )
    result = run_tool(tool=args.tool, crate_dir=crate_dir, output_dir=output_dir)
    payload = {
        "schema_version": "v1",
        "executed_at_utc": utc_timestamp(),
        **result,
    }
    findings_path = output_dir / "findings.json"
    findings_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["findings_relpath"] = _relative_artifact_path(findings_path)
    emit_target_event(
        primitive=f"runtime_static_analysis_{args.tool}",
        event=f"static_analysis_{args.tool}_completed",
        payload=payload,
    )
    print(
        "tool={tool} tool_status={tool_status} tool_exit_code={tool_exit_code} findings_count={findings_count} findings_relpath={findings_relpath}".format(
            tool=args.tool,
            tool_status=payload["tool_status"],
            tool_exit_code=payload["tool_exit_code"],
            findings_count=payload["findings_count"],
            findings_relpath=payload["findings_relpath"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
