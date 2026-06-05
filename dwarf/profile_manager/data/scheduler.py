"""Scheduler tick loop for the schedule store.

Item #19 — pure logic separated from threading so the same code is
exercised by tests (synchronous, single-tick) and by the dashboard
process (background daemon thread).

Firing model:
  - On each tick, snapshot every entry, filter to ``cron.due_entries``,
    and for each due entry call ``fire_entry``.
  - ``fire_entry`` wraps the per-fire dance:
      mark_running → run subprocess → derive run_id → record_fire.
  - The CLI command is built with the same builder dispatch_mutating_request
    uses, so a scheduled run is byte-identical to the manual /api/scenario/run
    code path.
  - The global mutating lock is honored: a tick that can't acquire it
    skips this round and the entry fires on the next tick instead. This
    prevents the scheduler from racing manual runs.

Run-id derivation:
  The CLI prints the run-id on stdout, but parsing varies by version.
  The robust approach taken here: snapshot the runs/ directory before
  the subprocess starts, snapshot after, and pick the new directory.
  If multiple appear (unlikely under the mutating lock) we take the
  lexicographically largest — the timestamp prefix gives chronology.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from profile_manager.data import schedule_store
from profile_manager.data.cron import due_entries


CommandBuilder = Callable[..., list[str]]


def _runs_dir() -> Path:
    env = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "dwarf" / "runs"


def _list_run_ids(runs_dir: Path) -> set[str]:
    if not runs_dir.is_dir():
        return set()
    return {p.name for p in runs_dir.iterdir() if p.is_dir()}


def _derive_new_run_id(before: set[str], runs_dir: Path) -> str | None:
    """Pick the run-id that appeared between the before and after
    snapshots. Returns None when nothing new appeared."""
    after = _list_run_ids(runs_dir)
    new = after - before
    if not new:
        return None
    return sorted(new, reverse=True)[0]


def _default_runner(cmd: list[str]) -> int:
    """Default subprocess runner — separated so tests can inject a stub."""
    proc = subprocess.run(cmd, capture_output=True, timeout=60 * 30)
    return proc.returncode


def fire_entry(
    entry: dict[str, Any],
    *,
    command_builder: CommandBuilder,
    runner: Callable[[list[str]], int] | None = None,
    now_epoch: float | None = None,
    runs_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Run a single scheduled entry to completion. Returns the updated
    record after record_fire, or None if the entry vanished mid-fire.

    ``runner`` is resolved at call time (not at definition time) so
    tests can monkeypatch ``_default_runner`` on this module."""
    schedule_store.mark_running(entry["id"])
    base = runs_dir if runs_dir is not None else _runs_dir()
    before = _list_run_ids(base)
    cmd = command_builder("scenario_run", scenario_path=entry["scenario_path"])
    fired_at = now_epoch if now_epoch is not None else time.time()
    actual_runner = runner if runner is not None else _default_runner
    try:
        actual_runner(cmd)
    except Exception:  # noqa: BLE001
        # Any failure (timeout, exec error) still ends the fire — we
        # record the attempt so the entry isn't stuck in 'running'.
        pass
    run_id = _derive_new_run_id(before, base)
    return schedule_store.record_fire(
        entry["id"], run_id=run_id, fired_at_epoch=fired_at,
    )


def tick(
    *,
    command_builder: CommandBuilder,
    runner: Callable[[list[str]], int] | None = None,
    now_epoch: float | None = None,
    lock_acquire: Callable[[], bool] | None = None,
    lock_release: Callable[[], None] | None = None,
    runs_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Run one scheduler tick. Returns the list of entries that were
    fired this tick (post-record_fire). Honors the global mutating lock
    via the ``lock_acquire`` / ``lock_release`` callables — tests pass
    no-op lambdas; production passes the real dashboard pair."""
    now = now_epoch if now_epoch is not None else time.time()
    snapshot = schedule_store.list_entries()
    due = due_entries(snapshot, now)
    fired: list[dict[str, Any]] = []
    for e in due:
        if lock_acquire is not None and not lock_acquire():
            # Can't hold the mutating lock right now — leave the entry
            # for the next tick.
            break
        try:
            updated = fire_entry(
                e,
                command_builder=command_builder,
                runner=runner,
                now_epoch=now,
                runs_dir=runs_dir,
            )
            if updated is not None:
                fired.append(updated)
        finally:
            if lock_release is not None:
                lock_release()
    return fired
