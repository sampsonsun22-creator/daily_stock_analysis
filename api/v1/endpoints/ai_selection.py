# -*- coding: utf-8 -*-
"""Read-only API for the latest production AI-selection run."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from api.v1.schemas.ai_selection import AISelectionRunResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def get_latest_selection_path() -> Path:
    output_dir = Path(os.getenv("AI_SELECTION_OUTPUT_DIR", "data/ai_selection"))
    return output_dir.expanduser() / "latest.json"


def load_latest_selection(path: Path | None = None) -> AISelectionRunResponse:
    target = path or get_latest_selection_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": "尚未生成 AI 选股结果，请先运行 python ai_select.py",
            },
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("读取 AI 选股结果失败: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "invalid_selection_file", "message": str(exc)},
        ) from exc

    try:
        return AISelectionRunResponse.model_validate(payload)
    except ValidationError as exc:
        logger.error("AI 选股结果 Schema 校验失败: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "invalid_selection_schema",
                "message": "AI 选股结果文件不符合当前 Schema",
            },
        ) from exc


@router.get(
    "/latest",
    response_model=AISelectionRunResponse,
    summary="获取最新 AI 选股结果",
    description="读取 ai_select.py 最近一次生成并通过 Schema 校验的选股结果。",
)
def get_latest_ai_selection() -> AISelectionRunResponse:
    return load_latest_selection()
