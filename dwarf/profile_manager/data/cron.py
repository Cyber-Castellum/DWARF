"""Minimal 5-field POSIX cron parser + next-fire calculator.

Item #19. Five whitespace-separated fields:

    minute (0-59)  hour (0-23)  day-of-month (1-31)  month (1-12)  day-of-week (0-6, Sun=0)

Each field accepts:
  - ``*``         every value
  - ``N``         a literal number
  - ``N-M``       inclusive range
  - ``N,M,O``     a comma-separated list of any of the above
  - ``*/S``       step over the whole range
  - ``N-M/S``     stepped range

Day-of-month and day-of-week interact like cron(8): when *both* are
restricted (neither is ``*``), the entry fires when *either* matches.
When one is ``*``, only the other constrains.

Day-of-week uses 0..6 with 0==Sunday, matching POSIX cron and Python's
``datetime.weekday() + 1 mod 7`` (Mon=0..Sun=6, so we shift).

This is intentionally a small, pure-Python implementation — the codebase
has no third-party cron lib, and the dashboard runs on stock Python 3.
Scope: fixed 5-field syntax, no ``@yearly`` shorthands, no seconds.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


_FIELD_BOUNDS: tuple[tuple[int, int], ...] = (
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day of month
    (1, 12),  # month
    (0, 6),   # day of week (Sun=0..Sat=6)
)


def _parse_field(spec: str, lo: int, hi: int) -> set[int]:
    """Expand one cron field into the explicit set of matching ints."""
    spec = spec.strip()
    if not spec:
        raise ValueError("empty cron field")
    out: set[int] = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            raise ValueError(f"empty cron sub-expression in {spec!r}")
        step = 1
        if "/" in chunk:
            base, _, step_s = chunk.partition("/")
            try:
                step = int(step_s)
            except ValueError as exc:
                raise ValueError(f"bad cron step {chunk!r}") from exc
            if step <= 0:
                raise ValueError(f"non-positive cron step in {chunk!r}")
        else:
            base = chunk
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, _, b = base.partition("-")
            try:
                start, end = int(a), int(b)
            except ValueError as exc:
                raise ValueError(f"bad cron range {base!r}") from exc
        else:
            try:
                start = end = int(base)
            except ValueError as exc:
                raise ValueError(f"bad cron literal {base!r}") from exc
        if start < lo or end > hi or start > end:
            raise ValueError(f"cron field {base!r} outside [{lo},{hi}]")
        for v in range(start, end + 1, step):
            out.add(v)
    return out


def parse_cron(expr: str) -> tuple[set[int], ...]:
    """Validate + expand a 5-field cron expression. Raises ValueError on
    malformed input. Returns the tuple of value-sets per field."""
    if not isinstance(expr, str):
        raise ValueError("cron expression must be a string")
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron must be 5 fields; got {len(parts)} in {expr!r}")
    return tuple(
        _parse_field(p, lo, hi) for p, (lo, hi) in zip(parts, _FIELD_BOUNDS)
    )


def _dow(dt: datetime) -> int:
    """0=Sun..6=Sat to match POSIX cron."""
    # Python: Mon=0..Sun=6 → POSIX: Sun=0..Sat=6 → shift by +1 % 7.
    return (dt.weekday() + 1) % 7


def matches(expr: str, when: datetime) -> bool:
    """True if the (UTC) datetime matches the cron expression at minute
    granularity. ``when`` is rounded down to the start of its minute."""
    minutes, hours, doms, months, dows = parse_cron(expr)
    if when.minute not in minutes:
        return False
    if when.hour not in hours:
        return False
    if when.month not in months:
        return False
    dom_match = when.day in doms
    dow_match = _dow(when) in dows
    raw = expr.split()
    dom_unrestricted = raw[2].strip() == "*"
    dow_unrestricted = raw[4].strip() == "*"
    if dom_unrestricted and dow_unrestricted:
        return True
    if dom_unrestricted:
        return dow_match
    if dow_unrestricted:
        return dom_match
    # Both restricted: cron(8) OR semantics.
    return dom_match or dow_match


def next_fire_after(expr: str, after_epoch: float, *,
                    horizon_minutes: int = 60 * 24 * 366) -> str | None:
    """Return the ISO-8601 UTC timestamp of the next minute strictly
    after ``after_epoch`` that matches ``expr``, or None if no match
    falls within ``horizon_minutes``.

    The horizon caps the search at ~one year so a malformed expression
    that never matches cannot wedge the calculation in an infinite loop.
    """
    parse_cron(expr)  # syntax-check up front
    cur = datetime.fromtimestamp(after_epoch, timezone.utc).replace(
        second=0, microsecond=0
    ) + timedelta(minutes=1)
    for _ in range(horizon_minutes):
        if matches(expr, cur):
            return cur.strftime("%Y-%m-%dT%H:%M:%SZ")
        cur += timedelta(minutes=1)
    return None


def is_due(entry: dict, now_epoch: float) -> bool:
    """True if ``entry["next_fire_at"]`` is at or before ``now_epoch``
    AND the entry is enabled. The scheduler tick uses this to filter
    the union of every persisted entry down to the firing set.
    """
    if not entry.get("enabled", False):
        return False
    if entry.get("status") == "running":
        return False
    next_iso = entry.get("next_fire_at")
    if not next_iso:
        return False
    try:
        nxt = datetime.strptime(next_iso, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except ValueError:
        return False
    return nxt <= now_epoch


def due_entries(entries: list[dict], now_epoch: float) -> list[dict]:
    """Filter a snapshot of entries down to those due to fire."""
    return [e for e in entries if is_due(e, now_epoch)]
