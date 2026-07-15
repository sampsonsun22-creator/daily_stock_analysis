#!/usr/bin/env python3
"""Tiny bridge executed by Vibe-Trading's isolated virtual environment."""

from __future__ import annotations

import argparse
import json
import sys


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
        payload = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 - bridge must return a stable envelope
        payload = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0 if payload.get("ok") is True else 2


if __name__ == "__main__":
    sys.exit(main())
