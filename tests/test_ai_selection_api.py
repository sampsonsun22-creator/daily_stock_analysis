import json
from pathlib import Path

from fastapi import HTTPException

from api.v1.endpoints.ai_selection import load_latest_selection


def test_load_latest_selection_validates_payload(tmp_path: Path):
    target = tmp_path / "latest.json"
    target.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "run-1",
                "generated_at": "2026-07-15T18:20:00+08:00",
                "source": "vibe",
                "candidate_count": 1,
                "analyzed_count": 1,
                "selected": [
                    {
                        "code": "600001",
                        "name": "样例",
                        "final_score": 0.8,
                        "prefilter_score": 0.9,
                        "sentiment_score": 0.7,
                        "trend_score": 0.8,
                        "decision_score": 0.9,
                        "confidence_score": 0.9,
                        "risk_penalty": 0.0,
                        "chase_penalty": 0.0,
                        "decision_type": "buy",
                        "operation_advice": "买入",
                        "confidence_level": "高",
                        "model_used": "test-model",
                        "data_sources": "test",
                        "reasons": ["test"],
                        "risk_flags": [],
                    }
                ],
                "metadata": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = load_latest_selection(target)
    assert result.run_id == "run-1"
    assert result.selected[0].code == "600001"


def test_load_latest_selection_returns_404(tmp_path: Path):
    try:
        load_latest_selection(tmp_path / "missing.json")
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("expected HTTPException")
