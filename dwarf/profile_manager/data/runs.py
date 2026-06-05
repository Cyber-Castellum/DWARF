"""Pure forensic-run helpers for the dashboard data layer."""
from __future__ import annotations

import json
import os
import re as _re
import subprocess
from pathlib import Path


def _forensic_runs_dir():
    env = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env:
        return Path(env)
    # data/runs.py -> data/ -> profile_manager/ -> dwarf/
    return Path(__file__).resolve().parents[2] / "runs"


def recent_runs_payload(*, runs_dir=None, limit=20):
    runs_dir = Path(runs_dir) if runs_dir is not None else _forensic_runs_dir()
    runs = list_recent_runs_with_remote(local_runs_dir=runs_dir, limit=limit)
    return {"recent_runs": runs}


def humanize_decode_error(message):
    """Translate a shim's stderr/stdout error string into plain English for non-experts.

    Returns None if the message is not an error (e.g. starts with OK).
    Returns the original message wrapped in a generic preamble if no pattern matches.
    """
    if not message or not isinstance(message, str):
        return None
    s = message.strip()
    if s.startswith("OK"):
        return None
    body = s.removeprefix("ERR ").strip()

    m = _re.search(r'DecoderErrorDeserialiseFailure\s+"([^"]+)"\s*\(.*?"([^"]+)"', body)
    if m:
        type_name, reason = m.group(1), m.group(2)
        return (
            f"Input was malformed CBOR — the {type_name} parser correctly rejected it "
            f"({reason})."
        )
    if "DecoderError" in body or "DeserialiseFailure" in body:
        return f"Input was malformed CBOR — the parser correctly rejected it ({body[:120]})."
    m = _re.search(r"unexpected type (\S+) at position (\d+): expected (\S+)", body)
    if m:
        return (
            f"Input was malformed CBOR at byte {m.group(2)} — expected a "
            f"{m.group(3)} but got {m.group(1)}; parser correctly rejected it."
        )
    if "stdin read failed" in body:
        return "The shim could not read its input from stdin."
    return f"Parser rejected the input. (raw: {body[:120]})"


def parse_remote_sources(env_var="ADA2_DWARF_REMOTE_SOURCES"):
    """Parse a comma-separated list of remote-source specs.

    Each spec is `name=ssh://user@host/abs/runs_dir`. Returns
    [{"name", "user", "host", "runs_dir"}, ...].
    """
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return []
    out = []
    for spec in raw.split(","):
        spec = spec.strip()
        if not spec or "=" not in spec:
            continue
        name, url = spec.split("=", 1)
        m = _re.match(r"ssh://([^@]+)@([^/]+)(/.+)$", url.strip())
        if not m:
            continue
        out.append({
            "name": name.strip(),
            "user": m.group(1),
            "host": m.group(2),
            "runs_dir": m.group(3),
        })
    return out


def _ssh_remote_lister(source, *, limit):
    """Default remote-lister: SSH to the source host and read manifest.json files."""
    cmd = [
        "ssh", "-n", "-o", "BatchMode=yes",
        f"{source['user']}@{source['host']}",
        f"for d in {source['runs_dir']}/*/; do "
        f"  rid=$(basename \"$d\"); "
        f"  m=\"$d/manifest.json\"; "
        f"  [ -f \"$m\" ] && python3 -c "
        f"\"import json,sys; d=json.load(open('$m')); d['_run_id']='$rid'; "
        f"print(json.dumps(d, separators=(',',':')))\"; "
        f"done | head -n " + str(int(limit)),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    entries = []
    for line in result.stdout.splitlines():
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        entries.append({
            "run_id": m.get("_run_id") or m.get("run_id"),
            "scenario_id": (m.get("scenario") or {}).get("id"),
            "runtime": m.get("runtime"),
            "exit_status": m.get("exit_status"),
            "started_at": m.get("started_at"),
            "ended_at": m.get("ended_at"),
            "actor": m.get("actor"),
            "assertion_summary": m.get("assertion_summary"),
            "resource_snapshot": m.get("resource_snapshot"),
        })
    return entries


def list_recent_runs_with_remote(*, local_runs_dir, remote_sources=None, limit=20, remote_lister=None):
    """Merge local + remote run listings, label each entry with its source."""
    from profile_manager import forensic
    local = forensic.list_recent_runs(runs_dir=local_runs_dir, limit=limit)
    for entry in local:
        entry.setdefault("source", "local")
    out = list(local)
    sources = remote_sources or parse_remote_sources()
    lister = remote_lister or _ssh_remote_lister
    for source in sources:
        try:
            remote_entries = lister(source, limit=limit)
        except Exception:
            remote_entries = []
        for entry in remote_entries:
            entry["source"] = source["name"]
            out.append(entry)
    out.sort(key=lambda e: (e.get("ended_at") or "", e.get("run_id") or ""), reverse=True)
    return out[:limit]
