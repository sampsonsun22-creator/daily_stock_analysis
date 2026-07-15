#!/usr/bin/env python3
"""Tiny bridge executed by Vibe-Trading's isolated virtual environment."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _scaled(value: Any, multiplier: float) -> Any:
    if value is None or value == "-":
        return value
    try:
        return float(value) * multiplier
    except (TypeError, ValueError):
        return value


def normalize_payload(payload: dict[str, Any], *, market: str) -> dict[str, Any]:
    """Normalize the pinned Vibe/Eastmoney A-share field units.

    At the pinned Vibe commit, the market screener exposes Eastmoney's integer
    transport representation directly: price/change/pct/turnover are hundredths,
    while volume is reported in lots. The host application expects CNY,
    percentage points, and shares.
    """
    if market != "a" or payload.get("ok") is not True:
        return payload
    if str(payload.get("source") or "").lower() != "eastmoney":
        return payload

    data = payload.get("data")
    if not isinstance(data, dict):
        return payload
    rows = data.get("rows")
    if not isinstance(rows, list):
        return payload

    normalized_rows = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row["price"] = _scaled(row.get("price"), 0.01)
        row["change_pct"] = _scaled(row.get("change_pct"), 0.01)
        row["change"] = _scaled(row.get("change"), 0.01)
        row["turnover_rate"] = _scaled(row.get("turnover_rate"), 0.01)
        row["volume"] = _scaled(row.get("volume"), 100.0)
        normalized_rows.append(row)

    data = dict(data)
    data["rows"] = normalized_rows
    data["unit_contract"] = {
        "price": "CNY",
        "change": "CNY",
        "change_pct": "percentage_points",
        "turnover_rate": "percentage_points",
        "volume": "shares",
        "amount": "CNY",
        "normalization_version": "a_share_eastmoney_v1",
    }
    result = dict(payload)
    result["data"] = data
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=("a", "us", "hk"), required=True)
    parser.add_argument(
        "--sort-by",
        choices=("change_pct", "volume", "amount", "turnover"),
        required=True,
    )
    parser.add_argument("--top-n", type=int, default=30)
    args = parser.parse_args()

    try:
        from src.tools.market_screener_tool import MarketScreenerTool

        raw = MarketScreenerTool().execute(
            market=args.market,
            sort_by=args.sort_by,
            top_n=args.top_n,
        )
        payload = normalize_payload(json.loads(raw), market=args.market)
    except Exception as exc:  # noqa: BLE001 - bridge must return a stable envelope
        payload = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0 if payload.get("ok") is True else 2


if __name__ == "__main__":
    sys.exit(main())
