from types import SimpleNamespace

from src.ai_selection.models import PrefilterCandidate
from src.ai_selection.scoring import SelectionScorer
from src.ai_selection.service import AISelectionService


def make_result(code: str, *, sentiment=70, trend=75, risks=None, decision="buy"):
    return SimpleNamespace(
        code=code,
        name=f"股票{code}",
        sentiment_score=sentiment,
        decision_type=decision,
        operation_advice="买入" if decision == "buy" else "观望",
        confidence_level="高",
        dashboard={
            "data_perspective": {"trend_status": {"trend_score": trend}},
            "intelligence": {"risk_alerts": list(risks or [])},
        },
        risk_warning="",
        model_used="test-model",
        data_sources="test-source",
        success=True,
    )


def test_risk_alerts_reduce_final_score():
    candidate = PrefilterCandidate(
        code="600001",
        name="样例",
        prefilter_score=0.8,
        metrics=("amount", "turnover"),
        change_pct=2.0,
    )
    scorer = SelectionScorer()
    clean = scorer.score(make_result("600001"), candidate)
    risky = scorer.score(make_result("600001", risks=["风险一", "风险二"]), candidate)
    assert risky.final_score < clean.final_score
    assert risky.risk_penalty == 0.08
    assert clean.model_used == "test-model"
    assert clean.data_sources == "test-source"


def test_service_sorts_and_ignores_failed_analysis():
    candidates = [
        PrefilterCandidate("600001", "A", 0.9, ("amount",)),
        PrefilterCandidate("600002", "B", 0.5, ("amount",)),
        PrefilterCandidate("600003", "C", 1.0, ("amount",)),
    ]
    failed = make_result("600003")
    failed.success = False

    run = AISelectionService().rank(
        results=[
            make_result("600002", sentiment=50, trend=40, decision="hold"),
            make_result("600001", sentiment=80, trend=80, decision="buy"),
            failed,
        ],
        prefilter_candidates=candidates,
        source="test",
        top_n=2,
    )

    assert [item.code for item in run.selected] == ["600001", "600002"]
    assert run.analyzed_count == 2
