import json

from src.ai_selection.models import ScreenRow
from src.ai_selection.screening import _parse_json_output, build_candidate_pool


def test_candidate_pool_fuses_metrics_and_filters_risky_rows():
    screens = {
        "amount": [
            ScreenRow("600001", "正常沪股", 10.0, 2.0, 500_000_000.0, 3.0),
            ScreenRow("300001", "正常创业板", 20.0, 5.0, 400_000_000.0, 5.0),
            ScreenRow("600002", "*ST示例", 5.0, 1.0, 600_000_000.0, 4.0),
            ScreenRow("688001", "近涨停", 30.0, 19.7, 700_000_000.0, 8.0),
        ],
        "turnover": [
            ScreenRow("300001", "正常创业板", 20.0, 5.0, 400_000_000.0, 5.0),
            ScreenRow("600001", "正常沪股", 10.0, 2.0, 500_000_000.0, 3.0),
        ],
    }

    pool = build_candidate_pool(screens, limit=10, min_amount=100_000_000.0)

    assert [item.code for item in pool] == ["600001", "300001"]
    assert set(pool[0].metrics) == {"amount", "turnover"}
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
