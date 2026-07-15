from __future__ import annotations

from typing import Any, Mapping

from .models import PrefilterCandidate, RankedSelection


def _clip(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dashboard(result: Any) -> Mapping[str, Any]:
    value = getattr(result, "dashboard", None)
    return value if isinstance(value, Mapping) else {}


def _trend_score(result: Any) -> float:
    dashboard = _dashboard(result)
    data_perspective = dashboard.get("data_perspective", {})
    if not isinstance(data_perspective, Mapping):
        return _clip(_float(getattr(result, "sentiment_score", 50), 50) / 100.0)
    trend_status = data_perspective.get("trend_status", {})
    if not isinstance(trend_status, Mapping):
        return _clip(_float(getattr(result, "sentiment_score", 50), 50) / 100.0)
    return _clip(_float(trend_status.get("trend_score"), 50.0) / 100.0)


def _confidence_score(value: Any) -> float:
    normalized = str(value or "").strip().lower()
    return {
        "高": 0.90,
        "high": 0.90,
        "中": 0.65,
        "medium": 0.65,
        "低": 0.35,
        "low": 0.35,
    }.get(normalized, 0.50)


def _decision_score(value: Any) -> float:
    normalized = str(value or "hold").strip().lower()
    return {
        "strong_buy": 1.0,
        "buy": 0.90,
        "hold": 0.50,
        "watch": 0.45,
        "sell": 0.10,
        "strong_sell": 0.0,
    }.get(normalized, 0.45)


def _risk_flags(result: Any) -> tuple[str, ...]:
    flags: list[str] = []
    dashboard = _dashboard(result)
    intelligence = dashboard.get("intelligence", {})
    if isinstance(intelligence, Mapping):
        alerts = intelligence.get("risk_alerts", [])
        if isinstance(alerts, list):
            flags.extend(str(item).strip() for item in alerts if str(item).strip())
    risk_warning = str(getattr(result, "risk_warning", "") or "").strip()
    if risk_warning and risk_warning not in flags:
        flags.append(risk_warning)
    return tuple(flags)


def _chase_penalty(change_pct: float | None, code: str) -> float:
    if change_pct is None or change_pct <= 0:
        return 0.0
    soft_threshold = 12.0 if code.startswith(("300", "301", "688")) else 6.0
    hard_threshold = 18.0 if code.startswith(("300", "301", "688")) else 9.0
    if change_pct <= soft_threshold:
        return 0.0
    return _clip((change_pct - soft_threshold) / (hard_threshold - soft_threshold), 0.0, 1.0) * 0.12


class SelectionScorer:
    """Deterministically rank expensive AI analyses.

    Market prefilter and technical structure remain the majority of the score;
    free-form LLM conclusions cannot independently push a stock to the top.
    """

    def score(self, result: Any, prefilter: PrefilterCandidate) -> RankedSelection:
        sentiment = _clip(_float(getattr(result, "sentiment_score", 50), 50.0) / 100.0)
        trend = _trend_score(result)
        decision = _decision_score(getattr(result, "decision_type", "hold"))
        confidence = _confidence_score(getattr(result, "confidence_level", "中"))
        risk_flags = _risk_flags(result)
        risk_penalty = min(0.20, 0.04 * len(risk_flags))
        chase_penalty = _chase_penalty(prefilter.change_pct, prefilter.code)

        final_score = (
            0.35 * prefilter.prefilter_score
            + 0.30 * trend
            + 0.20 * sentiment
            + 0.10 * decision
            + 0.05 * confidence
            - risk_penalty
            - chase_penalty
        )
        final_score = _clip(final_score)

        source_text = "/".join(prefilter.sources) if prefilter.sources else "unknown"
        reasons = [
            f"市场预筛={prefilter.prefilter_score:.3f} ({'/'.join(prefilter.metrics)})",
            f"预筛来源={source_text}",
            f"趋势={trend:.3f}",
            f"综合分析={sentiment:.3f}",
            f"决策={decision:.3f}",
        ]
        if prefilter.amount is not None:
            reasons.append(f"成交额={prefilter.amount:.0f}")
        if prefilter.change_pct is not None:
            reasons.append(f"当日涨跌={prefilter.change_pct:.2f}%")

        return RankedSelection(
            code=str(getattr(result, "code", prefilter.code)),
            name=str(getattr(result, "name", prefilter.name) or prefilter.name),
            final_score=round(final_score, 6),
            prefilter_score=round(prefilter.prefilter_score, 6),
            sentiment_score=round(sentiment, 6),
            trend_score=round(trend, 6),
            decision_score=round(decision, 6),
            confidence_score=round(confidence, 6),
            risk_penalty=round(risk_penalty, 6),
            chase_penalty=round(chase_penalty, 6),
            decision_type=str(getattr(result, "decision_type", "hold") or "hold"),
            operation_advice=str(getattr(result, "operation_advice", "观望") or "观望"),
            confidence_level=str(getattr(result, "confidence_level", "中") or "中"),
            model_used=(
                str(getattr(result, "model_used", "") or "").strip() or None
            ),
            data_sources=str(getattr(result, "data_sources", "") or ""),
            reasons=tuple(reasons),
            risk_flags=risk_flags,
        )
