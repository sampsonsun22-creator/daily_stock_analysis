# -*- coding: utf-8 -*-
"""Schemas for persisted AI-selection results."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AISelectionItem(BaseModel):
    code: str
    name: str
    final_score: float = Field(ge=0.0, le=1.0)
    prefilter_score: float = Field(ge=0.0, le=1.0)
    sentiment_score: float = Field(ge=0.0, le=1.0)
    trend_score: float = Field(ge=0.0, le=1.0)
    decision_score: float = Field(ge=0.0, le=1.0)
    confidence_score: float = Field(ge=0.0, le=1.0)
    risk_penalty: float = Field(ge=0.0)
    chase_penalty: float = Field(ge=0.0)
    decision_type: str
    operation_advice: str
    confidence_level: str
    model_used: str | None = None
    data_sources: str = ""
    reasons: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


class AISelectionRunResponse(BaseModel):
    schema_version: int = 1
    run_id: str
    generated_at: datetime
    source: str
    candidate_count: int = Field(ge=0)
    analyzed_count: int = Field(ge=0)
    selected: list[AISelectionItem] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
