#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production daily A-share selector built on the existing analysis pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from src.config import get_config, setup_env

setup_env()

from src.ai_selection.models import PrefilterCandidate
from src.ai_selection.screening import VibeMarketScreener, build_candidate_pool
from src.ai_selection.service import AISelectionService
from src.core.pipeline import StockAnalysisPipeline
from src.core.trading_calendar import get_open_markets_today
from src.logging_config import setup_logging

logger = logging.getLogger(__name__)


def _default_vibe_python() -> Path:
    explicit = os.getenv("VIBE_PYTHON", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    root = Path(os.getenv("DSA_AI_UPSTREAM_ROOT", Path.home() / ".dsa-ai" / "upstreams"))
    unix_path = root / "venvs" / "vibe" / "bin" / "python"
    windows_path = root / "venvs" / "vibe" / "Scripts" / "python.exe"
    return windows_path if os.name == "nt" else unix_path


def _parse_metrics(value: str) -> tuple[str, ...]:
    allowed = {"amount", "turnover", "change_pct", "volume"}
    metrics = tuple(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))
    unknown = sorted(set(metrics) - allowed)
    if unknown:
        raise argparse.ArgumentTypeError(f"unsupported metrics: {', '.join(unknown)}")
    if not metrics:
        raise argparse.ArgumentTypeError("at least one metric is required")
    return metrics


def _manual_candidates(codes: Iterable[str]) -> list[PrefilterCandidate]:
    normalized = list(
        dict.fromkeys(str(code).strip().upper() for code in codes if str(code).strip())
    )
    total = max(1, len(normalized))
    return [
        PrefilterCandidate(
            code=code,
            name=code,
            prefilter_score=round(1.0 - index / (2.0 * total), 6),
            metrics=("manual",),
        )
        for index, code in enumerate(normalized)
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="全市场预筛 → 现有 AI 分析流水线 → 确定性综合排名"
    )
    parser.add_argument("--source", choices=("vibe", "watchlist", "manual"), default="vibe")
    parser.add_argument("--stocks", help="manual 模式股票代码，逗号分隔")
    parser.add_argument("--metrics", type=_parse_metrics, default=("amount", "turnover"))
    parser.add_argument("--screen-per-metric", type=int, default=60)
    parser.add_argument("--candidate-limit", type=int, default=12)
    parser.add_argument("--top-n", type=int, default=6)
    parser.add_argument("--min-amount", type=float, default=100_000_000.0)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--vibe-python", default=str(_default_vibe_python()))
    parser.add_argument(
        "--output-dir",
        default=os.getenv("AI_SELECTION_OUTPUT_DIR", "data/ai_selection"),
    )
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--force-run", action="store_true", help="非交易日也运行")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def _build_prefilter(args: argparse.Namespace, config) -> list[PrefilterCandidate]:
    if args.source == "manual":
        if not args.stocks:
            raise ValueError("manual source requires --stocks")
        return _manual_candidates(args.stocks.split(","))
    if args.source == "watchlist":
        config.refresh_stock_list()
        return _manual_candidates(config.stock_list)

    bridge = Path(__file__).resolve().parent / "scripts" / "vibe_market_screen.py"
    screener = VibeMarketScreener(
        python_executable=args.vibe_python,
        bridge_script=bridge,
    )
    screens = {
        metric: screener.screen(metric=metric, top_n=args.screen_per_metric)
        for metric in args.metrics
    }
    return build_candidate_pool(
        screens,
        limit=args.candidate_limit,
        min_amount=args.min_amount,
    )


def _load_upstream_lock() -> dict:
    lock_path = Path(__file__).resolve().parent / "ai_selection_upstreams.lock.json"
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "unavailable"}
    return payload if isinstance(payload, dict) else {"status": "invalid"}


def main() -> int:
    args = parse_args()
    config = get_config()
    setup_logging(log_prefix="ai_selection", debug=args.debug, log_dir=config.log_dir)

    if not args.force_run and "cn" not in get_open_markets_today():
        logger.info("A股今日休市，跳过 AI 选股。使用 --force-run 可强制执行。")
        return 0

    try:
        prefilter = _build_prefilter(args, config)
    except Exception as exc:  # noqa: BLE001 - CLI must return a controlled failure
        logger.exception("候选池构建失败: %s", exc)
        return 2

    if not prefilter:
        logger.error("候选池为空，停止运行；不会回退到未经声明的股票列表。")
        return 3

    codes = [item.code for item in prefilter]
    logger.info("AI 选股候选池(%s): %s", len(codes), ", ".join(codes))

    pipeline = StockAnalysisPipeline(
        config=config,
        max_workers=args.workers,
        query_source="ai_selection",
        save_context_snapshot=True,
    )
    results = pipeline.run(
        stock_codes=codes,
        dry_run=False,
        send_notification=False,
        merge_notification=False,
    )

    service = AISelectionService()
    run = service.rank(
        results=results,
        prefilter_candidates=prefilter,
        source=args.source,
        top_n=args.top_n,
        metadata={
            "metrics": list(args.metrics),
            "screen_per_metric": args.screen_per_metric,
            "candidate_limit": args.candidate_limit,
            "min_amount": args.min_amount,
            "agent_mode": bool(getattr(config, "agent_mode", False)),
            "agent_arch": str(getattr(config, "agent_arch", "single")),
            "models_used": sorted(
                {
                    str(getattr(item, "model_used", "") or "").strip()
                    for item in results
                    if str(getattr(item, "model_used", "") or "").strip()
                }
            ),
            "prefilter_candidates": [asdict(item) for item in prefilter],
            "upstream_lock": _load_upstream_lock(),
        },
    )
    dated_path, latest_path = service.persist(run, args.output_dir)
    report = service.format_markdown(run)
    print(report)
    logger.info("AI 选股结果已保存: %s；latest=%s", dated_path, latest_path)

    if args.notify:
        if pipeline.notifier.is_available():
            if not pipeline.notifier.send(report, email_send_to_all=True):
                logger.error("AI 选股通知发送失败")
                return 5
        else:
            logger.warning("未配置通知渠道，仅保存本地结果")

    return 0 if run.selected else 4


if __name__ == "__main__":
    sys.exit(main())
