"""Production-oriented AI stock-selection integration helpers."""

from .models import PrefilterCandidate, RankedSelection, SelectionRun
from .scoring import SelectionScorer
from .screening import (
    CompositeMarketScreener,
    TushareEodScreener,
    VibeMarketScreener,
    build_candidate_pool,
)
from .service import AISelectionService

__all__ = [
    "AISelectionService",
    "CompositeMarketScreener",
    "PrefilterCandidate",
    "RankedSelection",
    "SelectionRun",
    "SelectionScorer",
    "TushareEodScreener",
    "VibeMarketScreener",
    "build_candidate_pool",
]
