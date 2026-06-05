"""Crash-triage data extractor for /operate/crashes.

Walks every ``crashes/`` directory under runs/ that matches the AFL++ output
convention, parses the per-crash filename
grammar, groups by (signal, mutation operator) for dedup, and
projects one row per signature with count + first/last-seen + the
exemplar filename + contributing run-ids.

AFL++ crash filenames follow the documented schema, e.g.
``id:000004,sig:11,src:000004,time:4,execs:40,op:splice``:

- ``id``     monotonic per-run crash-id
- ``sig``    POSIX signal number (06=SIGABRT, 11=SIGSEGV, 04=SIGILL,
             07=SIGBUS, 08=SIGFPE, 31=SIGSYS)
- ``src``    seed input that mutated into this crash
- ``time``   milliseconds from campaign start
- ``execs``  fuzzer exec count at the moment of crash
- ``op``     mutation operator that produced the crash (havoc /
             splice / arith8 / bitflip / interest / extras / etc.)

Dedup key: ``(sig, op)``. This is the cheapest stable signature —
crashes from the same op + same signal are very likely the same
underlying bug. Stack-hash dedup is finer-grained but requires
running the binary against each input, which the dashboard's
read-only data layer doesn't do.

The extractor reads from
runs/<id>/outputs/{cargo-fuzz,afl,aflpp,aflnet}/default/crashes/.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


_FILENAME_RE = re.compile(
    r"^id:(?P<id>\d+),"
    r"(?:sig:(?P<sig>\d+),)?"
    r"src:(?P<src>\d+),"
    r"(?:time:(?P<time>\d+),)?"
    r"(?:execs:(?P<execs>\d+),)?"
    r"op:(?P<op>[a-z0-9_-]+)"
    r"(?:,.*)?$"
)

# Map signal numbers to human-readable names. Operators read these on
# the page far more often than the raw integer.
_SIGNAL_NAMES = {
    1: "SIGHUP", 2: "SIGINT", 3: "SIGQUIT", 4: "SIGILL",
    6: "SIGABRT", 7: "SIGBUS", 8: "SIGFPE", 9: "SIGKILL",
    10: "SIGUSR1", 11: "SIGSEGV", 13: "SIGPIPE", 14: "SIGALRM",
    15: "SIGTERM", 17: "SIGCHLD", 19: "SIGSTOP", 22: "SIGFPE",
    24: "SIGXCPU", 25: "SIGXFSZ", 31: "SIGSYS",
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _runs_root() -> Path:
    env = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env:
        return Path(env)
    return _project_root() / "dwarf" / "runs"


def _signal_label(sig: int | None) -> str:
    if sig is None:
        return "—"
    name = _SIGNAL_NAMES.get(sig)
    return f"{name} ({sig})" if name else f"sig {sig}"


def _read_exemplar_preview(path: Path, *, max_bytes: int = 64) -> dict[str, Any]:
    """Read up to ``max_bytes`` of the exemplar input and return both a
    safe ASCII rendering and the file size. Inputs are arbitrary bytes
    so non-printable characters are escaped as \\xHH."""
    try:
        size = path.stat().st_size
    except OSError:
        return {"size_bytes": None, "preview": ""}
    try:
        with path.open("rb") as fp:
            chunk = fp.read(max_bytes)
    except OSError:
        return {"size_bytes": size, "preview": ""}
    parts = []
    for b in chunk:
        if 32 <= b < 127:
            parts.append(chr(b))
        else:
            parts.append(f"\\x{b:02x}")
    preview = "".join(parts)
    if size > max_bytes:
        preview += "…"
    return {"size_bytes": size, "preview": preview}


def _walk_crash_dirs() -> list[Path]:
    """Return every directory called ``crashes`` under runs/."""
    out: list[Path] = []
    root = _runs_root()
    if not root.is_dir():
        return out
    for path in root.rglob("crashes"):
        if path.is_dir():
            out.append(path)
    return out


def _classify_source(path: Path) -> tuple[str, str]:
    """Return (kind, source_id). For runs the source_id is the run-id;
    for unknown layouts it falls back to the path string."""
    parts = path.parts
    try:
        if "runs" in parts:
            i = parts.index("runs")
            return ("run", parts[i + 1])
    except (ValueError, IndexError):
        pass
    return ("unknown", str(path))


def _parse_crash_file(path: Path) -> dict[str, Any] | None:
    m = _FILENAME_RE.match(path.name)
    if not m:
        return None
    g = m.groupdict()
    sig = int(g["sig"]) if g.get("sig") else None
    return {
        "id": int(g["id"]),
        "sig": sig,
        "src": int(g["src"]),
        "time_ms": int(g["time"]) if g.get("time") else None,
        "execs": int(g["execs"]) if g.get("execs") else None,
        "op": g["op"],
        "filename": path.name,
        "path": str(path),
    }


def operate_crashes_payload() -> dict[str, Any]:
    """Build the render-ready payload for /operate/crashes."""
    crashes_by_signature: dict[tuple[int | None, str], list[dict[str, Any]]] = {}
    sources: set[tuple[str, str]] = set()
    total = 0

    for crash_dir in _walk_crash_dirs():
        kind, source_id = _classify_source(crash_dir)
        for entry in crash_dir.iterdir():
            if not entry.is_file():
                continue
            # AFL++ writes a README.txt into crashes/ — ignore it.
            if entry.name in ("README.txt", "README"):
                continue
            parsed = _parse_crash_file(entry)
            if parsed is None:
                continue
            parsed["source_kind"] = kind
            parsed["source_id"] = source_id
            sources.add((kind, source_id))
            key = (parsed["sig"], parsed["op"])
            crashes_by_signature.setdefault(key, []).append(parsed)
            total += 1

    # Project per-signature rows; sort by total count desc, then last-find desc.
    groups: list[dict[str, Any]] = []
    for (sig, op), entries in crashes_by_signature.items():
        entries.sort(key=lambda c: (c.get("time_ms") or 0))
        first = entries[0]
        last = entries[-1]
        exemplar_preview = _read_exemplar_preview(Path(first["path"]))
        contributing = sorted({(e["source_kind"], e["source_id"]) for e in entries})
        groups.append({
            "signature_key": f"sig{sig if sig is not None else 'X'}-op-{op}",
            "signal": sig,
            "signal_label": _signal_label(sig),
            "op": op,
            "count": len(entries),
            "first_time_ms": first.get("time_ms"),
            "last_time_ms": last.get("time_ms"),
            "first_execs": first.get("execs"),
            "last_execs": last.get("execs"),
            "exemplar_filename": first["filename"],
            "exemplar_size_bytes": exemplar_preview["size_bytes"],
            "exemplar_preview": exemplar_preview["preview"],
            "contributing_sources": [
                {"kind": k, "id": sid} for k, sid in contributing
            ],
            "contributing_count": len(contributing),
        })
    # Sort: highest count first; tie-break by last-find ms descending.
    groups.sort(key=lambda g: (-g["count"], -(g["last_time_ms"] or 0)))

    return {
        "groups": groups,
        "total_crashes": total,
        "total_signatures": len(groups),
        "sources_observed": len(sources),
        "empty": total == 0,
    }
