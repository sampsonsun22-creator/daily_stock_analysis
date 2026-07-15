import json
from datetime import datetime

import pandas as pd

from src.ai_selection.models import ScreenRow
from src.ai_selection.screening import (
    CompositeMarketScreener,
    TushareEodScreener,
    _parse_json_output,
    build_candidate_pool,
)


def test_candidate_pool_fuses_metrics_and_filters_risky_rows():
    screens = {
        "amount": [
            ScreenRow("600001", "正常沪股", 10.0, 2.0, 500_000_000.0, 3.0, source="vibe"),
            ScreenRow("300001", "正常创业板", 20.0, 5.0, 400_000_000.0, 5.0, source="tushare"),
            ScreenRow("600002", "*ST示例", 5.0, 1.0, 600_000_000.0, 4.0),
            ScreenRow("688001", "近涨停", 30.0, 19.7, 700_000_000.0, 8.0),
        ],
        "turnover": [
            ScreenRow("300001", "正常创业板", 20.0, 5.0, 400_000_000.0, 5.0, source="tushare"),
            ScreenRow("600001", "正常沪股", 10.0, 2.0, 500_000_000.0, 3.0, source="vibe"),
        ],
    }

    pool = build_candidate_pool(screens, limit=10, min_amount=100_000_000.0)

    assert [item.code for item in pool] == ["600001", "300001"]
    assert set(pool[0].metrics) == {"amount", "turnover"}
    assert pool[0].sources == ("vibe",)
    assert pool[0].prefilter_score == 1.0


def test_candidate_pool_excludes_beijing_and_low_liquidity():
    screens = {
        "amount": [
            ScreenRow("920001", "北交所", 10.0, 1.0, 500_000_000.0, 3.0),
            ScreenRow("600001", "低成交", 10.0, 1.0, 5_000_000.0, 1.0),
        ]
    }
    assert build_candidate_pool(screens, limit=10, min_amount=100_000_000.0) == []


def test_vibe_output_parser_uses_last_json_line():
    payload = _parse_json_output(
        "notice\n" + json.dumps({"ok": True, "data": {"rows": []}})
    )
    assert payload["ok"] is True


class FakeTusharePro:
    def trade_cal(self, **kwargs):
        return pd.DataFrame(
            [
                {"cal_date": "20260714", "is_open": 1},
                {"cal_date": "20260715", "is_open": 1},
            ]
        )

    def stock_basic(self, **kwargs):
        return pd.DataFrame(
            [
                {"ts_code": "600001.SH", "symbol": "600001", "name": "沪股A"},
                {"ts_code": "300001.SZ", "symbol": "300001", "name": "创股B"},
            ]
        )

    def daily(self, *, trade_date):
        if trade_date == "20260715":
            return pd.DataFrame()
        return pd.DataFrame(
            [
                {
                    "ts_code": "600001.SH",
                    "close": 10.0,
                    "pct_chg": 2.0,
                    "amount": 500_000.0,
                    "vol": 1_000_000.0,
                },
                {
                    "ts_code": "300001.SZ",
                    "close": 20.0,
                    "pct_chg": 3.0,
                    "amount": 300_000.0,
                    "vol": 800_000.0,
                },
            ]
        )

    def daily_basic(self, *, trade_date, fields):
        assert trade_date == "20260714"
        return pd.DataFrame(
            [
                {"ts_code": "600001.SH", "turnover_rate": 2.5},
                {"ts_code": "300001.SZ", "turnover_rate": 8.0},
            ]
        )


def test_tushare_eod_screener_uses_latest_populated_trade_date():
    screener = TushareEodScreener(
        token="test",
        pro_client=FakeTusharePro(),
        now_factory=lambda: datetime(2026, 7, 15, 18, 20),
    )

    amount_rows = screener.screen(metric="amount", top_n=2)
    turnover_rows = screener.screen(metric="turnover", top_n=2)

    assert screener.trade_date == "20260714"
    assert [row.code for row in amount_rows] == ["600001", "300001"]
    assert amount_rows[0].amount == 500_000_000.0
    assert amount_rows[0].source == "tushare:20260714"
    assert [row.code for row in turnover_rows] == ["300001", "600001"]


class FailingScreener:
    def screen(self, *, metric, top_n):
        raise RuntimeError("provider unavailable")


class StaticScreener:
    def screen(self, *, metric, top_n):
        return [ScreenRow("600001", "样例", 10.0, 1.0, 200_000_000.0, 2.0)]


def test_composite_screener_fails_over_and_records_reason():
    screener = CompositeMarketScreener(
        (("primary", FailingScreener()), ("fallback", StaticScreener()))
    )

    rows = screener.screen(metric="amount", top_n=5)

    assert rows[0].code == "600001"
    assert screener.provider_by_metric["amount"] == "fallback"
    assert "primary=RuntimeError" in screener.failures_by_metric["amount"][0]
