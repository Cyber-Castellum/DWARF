"""Durable run identities, explicit lifecycle transitions, and legacy import.

Entries live under the configured state directory, independently of evidence
volumes. Readers need no lock: each entry is published by durable replacement.
Recovery is explicit; its caller must establish that running entries before the
chosen cutoff are stale (for example after stopping the previous runner).
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


KINDS = ("scenario", "deployment", "maintenance", "ci", "external")
FINAL_STATES_BY_EXIT_STATUS = {
    "pass": "completed",
    "fail": "failed",
    "error": "failed",
    "precondition_failed": "failed",
    "interrupted": "interrupted",
}
_TRANSITIONS = {
    "queued": {"running", "failed", "interrupted"},
    "running": {"completed", "failed", "interrupted"},
    "completed": set(), "failed": set(), "interrupted": set(),
}
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_SCHEMA_PATH = Path(__file__).parent / "schemas/run-ledger-entry-v1.schema.json"
_VALIDATOR = Draft202012Validator(
    json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")),
    format_checker=FormatChecker(),
)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
        raise ValueError("timestamp must be an ISO date-time with timezone")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_entry(entry):
    """Validate the persisted contract without changing the supplied document."""
    error = next(_VALIDATOR.iter_errors(entry), None)
    if error is not None:
        location = ".".join(str(part) for part in error.absolute_path)
        raise ValueError(f"invalid run ledger entry {location}: {error.message}")
    # jsonschema's RFC3339 checker is optional; timestamps must still fail
    # closed in minimal installations without its extra dependencies.
    for field in ("created_at", "updated_at", "queued_at", "started_at", "ended_at"):
        if entry[field] is not None:
            _parse_timestamp(entry[field])
    return entry


def atomic_write_bytes(path, payload):
    """Flush bytes, replace beside the destination, then flush its directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _legacy_optional_identity(value):
    """Map the historical all-null identity sentinel to canonical JSON null."""
    if isinstance(value, dict) and value.get("id") is None and all(item is None for item in value.values()):
        return None
    return value


class RunLedger:
    def __init__(self, state_dir, *, create=True):
        configured_root = os.environ.get("ADA2_DWARF_RUN_LEDGER_ROOT", "").strip()
        self.root = (
            Path(configured_root)
            if configured_root
            else Path(state_dir) / "run-ledger"
        )
        self.entries_dir = self.root / "entries"
        if create:
            self.entries_dir.mkdir(parents=True, exist_ok=True)

    @property
    def history_index_path(self):
        return self.root / "history-index-v1.json"

    def _entries_generation(self):
        try:
            return self.entries_dir.stat().st_mtime_ns
        except OSError:
            return 0

    def _read_history_index(self, *, generation=None):
        try:
            value = json.loads(self.history_index_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        expected = self._entries_generation() if generation is None else generation
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != "v1"
            or value.get("entries_generation") != expected
            or not isinstance(value.get("entries"), list)
        ):
            return None
        entries = value["entries"]
        try:
            for entry in entries:
                validate_entry(entry)
        except (TypeError, ValueError):
            return None
        return entries

    def _publish_history_index(self, entries):
        ordered = sorted(
            entries,
            key=lambda entry: (
                entry.get("queued_at")
                or entry.get("started_at")
                or entry.get("ended_at")
                or entry.get("created_at")
                or "",
                entry["run_id"],
            ),
            reverse=True,
        )
        atomic_write_bytes(
            self.history_index_path,
            _json_bytes(
                {
                    "schema_version": "v1",
                    "entries_generation": self._entries_generation(),
                    "entries": ordered,
                }
            ),
        )
        return ordered

    def _write_and_index(self, entry):
        previous_generation = self._entries_generation()
        indexed = self._read_history_index(generation=previous_generation)
        self._write(entry)
        if indexed is None:
            indexed = self.list_entries()
        else:
            by_id = {item["run_id"]: item for item in indexed}
            by_id[entry["run_id"]] = entry
            indexed = list(by_id.values())
        self._publish_history_index(indexed)

    def history_entries(self):
        """Read one crash-detected index instead of reparsing every entry file."""
        indexed = self._read_history_index()
        if indexed is not None:
            return indexed
        if not self.entries_dir.is_dir():
            return []
        with self._lock():
            indexed = self._read_history_index()
            if indexed is not None:
                return indexed
            return self._publish_history_index(self.list_entries())

    @contextmanager
    def _lock(self):
        with (self.root / "write.lock").open("a+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _path(self, run_id):
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValueError("run_id must be a safe canonical run identity")
        return self.entries_dir / f"{run_id}.json"

    def _write(self, entry):
        validate_entry(entry)
        atomic_write_bytes(self._path(entry["run_id"]), _json_bytes(entry))

    def get(self, run_id):
        entry = json.loads(self._path(run_id).read_text(encoding="utf-8"))
        validate_entry(entry)
        if entry["run_id"] != run_id:
            raise ValueError("run ledger filename/identity mismatch")
        return entry

    def list_entries(self):
        """Return valid independent entry snapshots; unfinished temp files are ignored."""
        if not self.entries_dir.is_dir():
            return []
        return [self.get(path.stem) for path in sorted(self.entries_dir.glob("*.json"))]

    def create(self, *, run_id, kind, origin, backend, scenario=None, profile=None,
               topology=None, release_revision=None, state="queued", queued_at=None,
               evidence_path=None, provenance=None):
        """Reserve a new immutable identity before readiness or execution."""
        if state != "queued":
            raise ValueError("new run identity must be queued")
        now = _now()
        entry = {
            "schema_version": "v1", "run_id": run_id, "kind": kind,
            "origin": origin, "backend": backend, "scenario": scenario,
            "profile": profile, "topology": topology,
            "release_revision": release_revision, "state": state,
            "created_at": now, "updated_at": now, "queued_at": queued_at or now,
            "started_at": None, "ended_at": None, "verdict": None,
            "manifest_path": None, "evidence_path": evidence_path,
            "provenance": provenance if provenance is not None else {},
        }
        validate_entry(entry)
        with self._lock():
            if self._path(run_id).exists():
                raise ValueError(f"run identity already exists: {run_id}")
            self._write_and_index(entry)
        return entry

    def _transition(self, entry, state, updates):
        if state not in _TRANSITIONS[entry["state"]]:
            raise ValueError(f"illegal run transition: {entry['state']} -> {state}")
        allowed = {"verdict", "manifest_path", "evidence_path", "provenance", "started_at", "ended_at"}
        if updates.keys() - allowed:
            raise ValueError("transition cannot change run identity")
        now = _now()
        entry = {**entry, **updates, "state": state, "updated_at": now}
        if state == "running":
            entry["started_at"] = updates.get("started_at") or now
        else:
            entry["ended_at"] = updates.get("ended_at") or now
        self._write_and_index(entry)
        return entry

    def transition(self, run_id, state, **updates):
        """Apply one legal transition under the shared writer lock."""
        with self._lock():
            return self._transition(self.get(run_id), state, updates)

    def annotate(self, run_id, **updates):
        """Attach evidence/provenance without fabricating a lifecycle transition."""
        allowed = {"manifest_path", "evidence_path", "provenance"}
        if updates.keys() - allowed:
            raise ValueError("annotation cannot change run identity or lifecycle")
        with self._lock():
            entry = self.get(run_id)
            entry = {**entry, **updates, "updated_at": _now()}
            validate_entry(entry)
            self._write_and_index(entry)
            return entry

    def reconcile_terminal_evidence(
        self, run_id, *, verdict, manifest_path, provenance
    ):
        """Attach a later verified result to an inconclusive terminal run."""
        if verdict not in {"pass", "fail"}:
            raise ValueError("terminal evidence verdict must be pass or fail")
        with self._lock():
            entry = self.get(run_id)
            if entry["state"] not in {"completed", "failed"}:
                raise ValueError("run is not terminal")
            if entry["verdict"] not in {None, "inconclusive", verdict}:
                raise ValueError("terminal verdict cannot be replaced")
            entry = {
                **entry,
                "verdict": verdict,
                "manifest_path": manifest_path,
                "provenance": provenance,
                "updated_at": _now(),
            }
            validate_entry(entry)
            self._write_and_index(entry)
            return entry

    def recover_interrupted(self, *, before):
        """Interrupt stale running entries; queued and terminal entries are retained."""
        cutoff = _parse_timestamp(before)
        recovered = []
        with self._lock():
            for entry in self.list_entries():
                if entry["state"] != "running":
                    continue
                updated = _parse_timestamp(entry["updated_at"])
                if updated <= cutoff:
                    provenance = {**entry["provenance"], "recovery": {"reason": "stale-running", "previous_updated_at": entry["updated_at"], "recovered_at": _now()}}
                    recovered.append(self._transition(entry, "interrupted", {"verdict": None, "provenance": provenance}))
        return recovered

    def backfill_manifest(self, manifest_path, *, kind, backend):
        """Explicitly import a legacy manifest verbatim, retaining source provenance.

        Kind/backend are caller supplied: missing legacy metadata never proves
        that an operational receipt was a scenario or had a passing verdict.
        """
        path = Path(manifest_path).resolve()
        payload = path.read_bytes()
        manifest = json.loads(payload)
        run_id = manifest["run_id"]
        if path.parent.name != run_id:
            raise ValueError("legacy manifest run identity disagrees with directory")
        now = _now()
        status = manifest.get("exit_status")
        if status not in FINAL_STATES_BY_EXIT_STATUS:
            raise ValueError("legacy manifest has no known final outcome")
        provenance = {
            "source": "legacy-manifest", "manifest_path": str(path),
            "manifest_sha256": hashlib.sha256(payload).hexdigest(),
            "imported_at": now, "actor": manifest.get("actor"),
            "legacy_exit_status": status,
            "original_provenance": manifest.get("provenance"),
        }
        execution = manifest.get("execution") or {}
        verdict = execution.get("security_verdict") if isinstance(execution, dict) else None
        if kind in {"scenario", "ci", "external"} and verdict not in {"pass", "fail"}:
            from profile_manager.scenario_verdict import evaluate_scenario_verdict
            verdict = evaluate_scenario_verdict(
                exit_status=status,
                assertions=manifest.get("assertion_summary"),
                measurements=manifest.get("measurements"),
                run_kind=kind,
            ).security_verdict
        entry = {
            "schema_version": "v1", "run_id": run_id, "kind": kind,
            "origin": "legacy-backfill", "backend": backend,
            "scenario": manifest.get("scenario"),
            "profile": _legacy_optional_identity(manifest.get("profile")),
            "topology": _legacy_optional_identity(manifest.get("topology")),
            "release_revision": (manifest.get("framework") or {}).get("commit"),
            "state": FINAL_STATES_BY_EXIT_STATUS[status],
            "created_at": now, "updated_at": now, "queued_at": None,
            "started_at": manifest.get("started_at"), "ended_at": manifest.get("ended_at"),
            "verdict": verdict if kind in {"scenario", "ci", "external"} else None,
            "manifest_path": str(path), "evidence_path": str(path.parent),
            "provenance": provenance,
        }
        validate_entry(entry)
        with self._lock():
            if self._path(run_id).exists():
                previous = self.get(run_id)
                comparable = {**entry, "created_at": previous["created_at"], "updated_at": previous["updated_at"], "provenance": {**provenance, "imported_at": previous["provenance"].get("imported_at")}}
                if previous == comparable:
                    return previous
                raise ValueError(f"legacy import identity collision: {run_id}")
            self._write_and_index(entry)
        return entry


def main(argv=None):
    """Run explicit legacy backfill without changing source manifests."""
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    backfill = subcommands.add_parser("backfill", help="import legacy manifests with provenance")
    backfill.add_argument("--runs-dir", required=True, type=Path)
    backfill.add_argument("--state-dir", required=True, type=Path)
    backfill.add_argument("--kind", required=True, choices=KINDS)
    backfill.add_argument("--backend", required=True)
    args = parser.parse_args(argv)
    if not args.runs_dir.is_dir():
        parser.error("--runs-dir must be an existing runs directory")
    ledger = RunLedger(args.state_dir)
    try:
        entries = [ledger.backfill_manifest(path, kind=args.kind, backend=args.backend) for path in sorted(args.runs_dir.glob("*/manifest.json"))]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"run_ids": [entry["run_id"] for entry in entries], "count": len(entries)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
