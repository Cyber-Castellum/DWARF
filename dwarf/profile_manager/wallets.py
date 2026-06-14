from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import replace
from decimal import Decimal
from typing import Any


DEFAULT_QUERY_BASE_URL = "https://preprod.koios.rest/api/v1"
DEFAULT_RECENT_TX_LIMIT = 10


def normalize_wallet(raw: dict[str, Any]) -> dict[str, Any]:
    wallet_id = str(raw.get("id") or "").strip()
    address = str(raw.get("address") or "").strip()
    if not wallet_id:
        raise ValueError("wallet id is required")
    if not address:
        raise ValueError("wallet address is required")
    network = str(raw.get("network") or "preprod").strip().lower()
    return {
        "id": wallet_id,
        "label": str(raw.get("label") or wallet_id).strip(),
        "role": str(raw.get("role") or "unknown").strip(),
        "network": network,
        "address": address,
        "query_base_url": str(raw.get("query_base_url") or _default_query_base_url(network)).rstrip("/"),
    }


def wallet_rows(config) -> list[dict[str, Any]]:
    return [normalize_wallet(item) for item in (config.wallets or [])]


def add_wallet(config, wallet: dict[str, Any]):
    normalized = normalize_wallet(wallet)
    wallets = [item for item in wallet_rows(config) if item["id"] != normalized["id"]]
    wallets.append(normalized)
    return replace(config, wallets=wallets)


def remove_wallet(config, wallet_id: str):
    wallets = [item for item in wallet_rows(config) if item["id"] != wallet_id]
    return replace(config, wallets=wallets)


def lovelace_to_tada(value: Any) -> str:
    lovelace = Decimal(str(value or "0"))
    return f"{lovelace / Decimal(1_000_000):.6f}"


def wallet_statuses(config, timeout: int = 10) -> list[dict[str, Any]]:
    return [query_wallet_status(wallet, timeout=timeout) for wallet in wallet_rows(config)]


def query_wallet_status(wallet: dict[str, Any], timeout: int = 10) -> dict[str, Any]:
    normalized = normalize_wallet(wallet)
    base_url = normalized["query_base_url"]
    payload = {"_addresses": [normalized["address"]]}
    status = {
        **normalized,
        "state": "error",
        "balance_lovelace": None,
        "balance_tada": "unknown",
        "utxo_count": None,
        "recent_transactions": [],
        "queried_at": _now_iso(),
        "error": None,
    }
    try:
        info_rows = _post_json(f"{base_url}/address_info", payload, timeout=timeout)
        tx_rows = _post_json(f"{base_url}/address_txs", payload, timeout=timeout)
        info = info_rows[0] if isinstance(info_rows, list) and info_rows else {}
        if not info:
            status["state"] = "warn"
            status["error"] = "query provider returned no address_info row"
            return status
        balance = int(info.get("balance") or 0)
        transactions = _normalize_transactions(tx_rows)
        status.update({
            "state": "ok" if balance > 0 or transactions else "empty",
            "balance_lovelace": balance,
            "balance_tada": lovelace_to_tada(balance),
            "utxo_count": len(info.get("utxo_set") or []),
            "recent_transactions": transactions[:DEFAULT_RECENT_TX_LIMIT],
        })
        return status
    except Exception as exc:
        status["state"] = "error"
        status["error"] = str(exc)
        return status


def _default_query_base_url(network: str) -> str:
    if network == "preprod":
        return DEFAULT_QUERY_BASE_URL
    if network == "preview":
        return "https://preview.koios.rest/api/v1"
    return DEFAULT_QUERY_BASE_URL


def _normalize_transactions(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append({
            "tx_hash": row.get("tx_hash") or "unknown",
            "block_height": row.get("block_height"),
            "block_time": row.get("block_time"),
        })
    return out


def _post_json(url: str, payload: dict[str, Any], timeout: int = 10) -> Any:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
