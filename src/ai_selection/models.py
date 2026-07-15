from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ScreenRow:
    """One normalized row returned by a read-only market screener."""

    code: str
    name: str
    price: float | None
    change_pct: float | None
    amount: float | None
    turnover_rate: float | None
    volume: float | None = None
    source: str = "unknown"


@dataclass(frozen=True)
class PrefilterCandidate:
    """A-share candidate produced before expensive LLM analysis."""

    code: str
    name: str
    prefilter_score: float
    metrics: tuple[str, ...]
    sources: tuple[str, ...] = ()
    price: float | None = None
    change_pct: float | None = None
    amount: float | None = None
    turnover_rate: float | None = None
    volume: float | None = None


@dataclass(frozen=True)
class RankedSelection:
    """Final deterministic rank built from market and analysis evidence."""

    code: str
    name: str
    final_score: float
    prefilter_score: float
    sentiment_score: float
    trend_score: float
    decision_score: float
    confidence_score: float
    risk_penalty: float
    chase_penalty: float
    decision_type: str
    operation_advice: str
    confidence_level: str
    model_used: str | None
    data_sources: str
    reasons: tuple[str, ...]
    risk_flags: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SelectionRun:
    """Auditable output of one production selection run."""

    run_id: str
    generated_at: datetime
    source: str
    candidate_count: int
    analyzed_count: int
    selected: tuple[RankedSelection, ...]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "generated_at": self.generated_at.isoformat(),
            "source": self.source,
            "candidate_count": self.candidate_count,
            "analyzed_count": self.analyzed_count,
            "selected": [item.to_dict() for item in self.selected],
            "metadata": self.metadata,
        }
