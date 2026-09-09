"""Server-side Cardano attack-cost data with a bounded snapshot fallback."""
from __future__ import annotations

import json
import math
import threading
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen


_SNAPSHOT = {
    "epoch": 642,
    "circulating_ada": 36_474_473_805.5,
    "active_ada": 21_392_411_062.6,
    "price_usd": 0.157322,
}
_CACHE_TTL_SECONDS = 300.0
_CACHE_LOCK = threading.Lock()
_CACHE: dict = {}


def reset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _fetch_json(url: str, timeout: float):
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "DWARF-dashboard/1"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _positive_number(value, field: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field} must be a positive finite number")
    return number


def _live_values(timeout: float) -> dict:
    totals = _fetch_json("https://api.koios.rest/api/v1/totals", timeout)
    if not isinstance(totals, list) or not totals:
        raise ValueError("Koios totals response is empty")
    epoch = int(totals[0]["epoch_no"])
    if epoch < 0:
        raise ValueError("epoch number must be non-negative")
    epoch_info = _fetch_json(
        f"https://api.koios.rest/api/v1/epoch_info?_epoch_no={epoch}", timeout
    )
    if not isinstance(epoch_info, list) or not epoch_info:
        raise ValueError("Koios epoch response is empty")
    price = _fetch_json(
        "https://api.coingecko.com/api/v3/simple/price?ids=cardano&vs_currencies=usd",
        timeout,
    )
    return {
        "epoch": epoch,
        "circulating_ada": _positive_number(totals[0]["circulation"], "circulation") / 1_000_000,
        "active_ada": _positive_number(epoch_info[0]["active_stake"], "active stake") / 1_000_000,
        "price_usd": _positive_number(price["cardano"]["usd"], "ADA price"),
    }


def _compact_number(value: float, *, currency: bool = False) -> str:
    prefix = "$" if currency else ""
    if value >= 1_000_000_000:
        return f"{prefix}{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{prefix}{value / 1_000_000:.1f}M"
    return f"{prefix}{value:,.0f}"


def _render_payload(values: dict, *, source: str, fetched_at: float) -> dict:
    active = values["active_ada"]
    circulating = values["circulating_ada"]
    price = values["price_usd"]
    forty = active * 0.40
    fifty = active * 0.50
    return {
        **values,
        "source": source,
        "source_detail": (
            "live · Koios stake/supply + CoinGecko ADA/USD · cached up to 5 minutes"
            if source == "live"
            else "snapshot · upstream data unavailable · epoch 642 reference values"
        ),
        "fetched_at": datetime.fromtimestamp(fetched_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "cost_40": _compact_number(forty * price, currency=True),
        "ada_40": _compact_number(forty),
        "ada_50": _compact_number(fifty),
        "active_display": _compact_number(active),
        "supply_pct_40": f"{100 * forty / circulating:.1f}%",
        "staking_ratio": f"{100 * active / circulating:.1f}%",
        "price_display": f"${price:.4f}",
        "row_40": f"{_compact_number(forty)} ADA · {_compact_number(forty * price, currency=True)} · {100 * forty / circulating:.1f}% supply",
        "row_50": f"{_compact_number(fifty)} ADA · {_compact_number(fifty * price, currency=True)} · {100 * fifty / circulating:.1f}% supply",
    }


def attack_cost_payload(*, now: float | None = None, timeout: float = 4.0) -> dict:
    timestamp = time.time() if now is None else float(now)
    with _CACHE_LOCK:
        if _CACHE and timestamp - _CACHE["cached_at"] < _CACHE_TTL_SECONDS:
            return dict(_CACHE["payload"])
    try:
        values = _live_values(min(float(timeout), 4.0))
        payload = _render_payload(values, source="live", fetched_at=timestamp)
    except (OSError, TimeoutError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        payload = _render_payload(dict(_SNAPSHOT), source="snapshot", fetched_at=timestamp)
    with _CACHE_LOCK:
        _CACHE.update(cached_at=timestamp, payload=payload)
    return dict(payload)
