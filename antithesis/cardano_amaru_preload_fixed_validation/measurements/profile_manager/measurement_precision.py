"""Exact timing conversion shared by patched-node collectors."""
from __future__ import annotations

import math
from typing import Any, Mapping


def precise_microseconds(
    fields: Mapping[str, Any],
    *,
    nanos_field: str,
    micros_field: str,
) -> tuple[int | None, float | int | None]:
    """Prefer an integer nanosecond value and retain legacy microseconds.

    A present but invalid precise value is an invalid record. The collector
    must not silently fall back to a less precise compatibility value.
    """
    if nanos_field in fields:
        nanos = fields.get(nanos_field)
        if isinstance(nanos, bool) or not isinstance(nanos, int) or nanos < 0:
            raise ValueError("invalid-nanosecond-timing")
        return nanos, nanos / 1000

    if micros_field not in fields:
        return None, None
    micros = fields.get(micros_field)
    if isinstance(micros, bool) or not isinstance(micros, (int, float)):
        raise ValueError("invalid-microsecond-timing")
    converted = float(micros)
    if not math.isfinite(converted) or converted < 0:
        raise ValueError("invalid-microsecond-timing")
    return None, micros
