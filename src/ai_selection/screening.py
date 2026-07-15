from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol
from zoneinfo import ZoneInfo

from .models import PrefilterCandidate, ScreenRow

logger = logging.getLogger(__name__)

_ALLOWED_PREFIXES = (
    "000",
    "001",
    "002",
    "003",
    "300",
    "301",
    "600",
    "601",
    "603",
    "605",
    "688",
)
_METRIC_WEIGHTS = {
    "amount": 1.00,
    "turnover": 0.85,
    "change_pct": 0.20,
    "volume": 0.50,
}


class MarketScreener(Protocol):
    def screen(self, *, metric: str, top_n: int) -> list[ScreenRow]: ...


class MarketScreenerError(RuntimeError):
    """Raised when no configured market screener can return valid rows."""


class VibeScreenerError(MarketScreenerError):
    """Raised when the isolated Vibe-Trading bridge cannot return valid data."""


class TushareScreenerError(MarketScreenerError):
    """Raised when the Tushare EOD market snapshot cannot be loaded."""


class VibeMarketScreener:
    """Call Vibe-Trading through an isolated Python interpreter.

    The main application never imports Vibe's top-level ``src`` package, which
    avoids dependency and package-name conflicts with this repository.
    """

    def __init__(
        self,
        *,
        python_executable: str | Path,
        bridge_script: str | Path,
        timeout_seconds: int = 45,
    ) -> None:
        self.python_executable = Path(python_executable).expanduser().resolve()
        self.bridge_script = Path(bridge_script).expanduser().resolve()
        self.timeout_seconds = max(5, int(timeout_seconds))

    def screen(self, *, metric: str, top_n: int) -> list[ScreenRow]:
        _validate_screen_request(metric, top_n)
        if not self.python_executable.exists():
            raise VibeScreenerError(
                f"Vibe Python not found: {self.python_executable}. "
                "Run scripts/install_ai_selection_upstreams.sh first."
            )
        if not self.bridge_script.exists():
            raise VibeScreenerError(f"Vibe bridge script not found: {self.bridge_script}")

        command = [
            str(self.python_executable),
            str(self.bridge_script),
            "--market",
            "a",
            "--sort-by",
            metric,
            "--top-n",
            str(top_n),
        ]
        clean_env = os.environ.copy()
        # The host repository also has a top-level ``src`` package. Do not let
        # PYTHONPATH/PYTHONHOME make the Vibe interpreter import the host package
        # instead of the independently installed Vibe package.
        clean_env.pop("PYTHONPATH", None)
        clean_env.pop("PYTHONHOME", None)
        completed = subprocess.run(
            command,
            cwd=tempfile.gettempdir(),
            env=clean_env,
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        payload = _parse_json_output(completed.stdout)
        if completed.returncode != 0 or payload.get("ok") is not True:
            raise VibeScreenerError(
                "Vibe market screener failed: "
                f"returncode={completed.returncode}, payload={payload}, "
                f"stderr={completed.stderr[-1000:]}"
            )
        rows = payload.get("data", {}).get("rows", [])
        if not isinstance(rows, list):
            raise VibeScreenerError("Vibe payload does not contain data.rows list")
        source = f"vibe:{str(payload.get('source') or 'unknown')}"
        normalized = [
            _normalize_row(item, source=source)
            for item in rows
            if isinstance(item, dict)
        ]
        if not normalized:
            raise VibeScreenerError("Vibe market screener returned no rows")
        return normalized


class TushareEodScreener:
    """Build a full-market EOD snapshot from Tushare Pro.

    This provider is intended as the production fallback when a public realtime
    ranking endpoint is unavailable. It joins ``daily`` with ``daily_basic`` and
    ``stock_basic`` and searches backward for the latest populated trade date.
    """

    def __init__(
        self,
        *,
        token: str | None,
        pro_client: Any | None = None,
        now_factory: Callable[[], datetime] | None = None,
        lookback_days: int = 14,
    ) -> None:
        self.token = (token or "").strip()
        self._pro_client = pro_client
        self._now_factory = now_factory or (lambda: datetime.now(ZoneInfo("Asia/Shanghai")))
        self.lookback_days = max(5, int(lookback_days))
        self._snapshot: list[ScreenRow] | None = None
        self.trade_date: str | None = None

    def _client(self) -> Any:
        if self._pro_client is not None:
            return self._pro_client
        if not self.token:
            raise TushareScreenerError("TUSHARE_TOKEN is not configured")
        try:
            import tushare as ts
        except ImportError as exc:
            raise TushareScreenerError("tushare package is not installed") from exc
        self._pro_client = ts.pro_api(self.token)
        return self._pro_client

    def _candidate_trade_dates(self, pro: Any) -> list[str]:
        now = self._now_factory()
        end = now.date()
        start = end - timedelta(days=self.lookback_days)
        try:
            calendar = pro.trade_cal(
                exchange="",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                is_open="1",
            )
            if calendar is not None and not calendar.empty and "cal_date" in calendar:
                dates = sorted(
                    {str(value) for value in calendar["cal_date"].tolist() if str(value)},
                    reverse=True,
                )
                if dates:
                    return dates
        except Exception as exc:  # noqa: BLE001 - daily() fallback remains valid
            logger.warning("Tushare trade calendar unavailable; probing dates directly: %s", exc)
        return [
            (end - timedelta(days=offset)).strftime("%Y%m%d")
            for offset in range(self.lookback_days + 1)
        ]

    def _load_snapshot(self) -> list[ScreenRow]:
        if self._snapshot is not None:
            return self._snapshot

        try:
            import pandas as pd
        except ImportError as exc:
            raise TushareScreenerError("pandas is not installed") from exc

        pro = self._client()
        try:
            stock_basic = pro.stock_basic(
                exchange="",
                list_status="L",
                fields="ts_code,symbol,name",
            )
        except Exception as exc:  # noqa: BLE001 - code can be derived from ts_code
            logger.warning("Tushare stock_basic unavailable; names may be missing: %s", exc)
            stock_basic = pd.DataFrame(columns=["ts_code", "symbol", "name"])

        errors: list[str] = []
        for trade_date in self._candidate_trade_dates(pro):
            try:
                daily = pro.daily(trade_date=trade_date)
            except Exception as exc:  # noqa: BLE001 - probe previous trade date
                errors.append(f"daily({trade_date})={type(exc).__name__}: {exc}")
                continue
            if daily is None or daily.empty:
                continue

            try:
                daily_basic = pro.daily_basic(
                    trade_date=trade_date,
                    fields="ts_code,turnover_rate",
                )
            except Exception as exc:  # noqa: BLE001 - amount screen can still operate
                logger.warning(
                    "Tushare daily_basic unavailable for %s; turnover screen disabled: %s",
                    trade_date,
                    exc,
                )
                daily_basic = pd.DataFrame(columns=["ts_code", "turnover_rate"])

            frame = daily.copy()
            if daily_basic is not None and not daily_basic.empty:
                frame = frame.merge(daily_basic, on="ts_code", how="left")
            else:
                frame["turnover_rate"] = None
            if stock_basic is not None and not stock_basic.empty:
                frame = frame.merge(stock_basic, on="ts_code", how="left")

            rows: list[ScreenRow] = []
            for _, item in frame.iterrows():
                ts_code = str(item.get("ts_code", "")).strip()
                code = str(item.get("symbol", "")).strip() or ts_code.split(".", 1)[0]
                close = _optional_float(item.get("close"))
                amount_k_cny = _optional_float(item.get("amount"))
                volume_lots = _optional_float(item.get("vol"))
                rows.append(
                    ScreenRow(
                        code=code,
                        name=str(item.get("name", "") or code).strip(),
                        price=close,
                        change_pct=_optional_float(item.get("pct_chg")),
                        # Tushare daily.amount is reported in thousand CNY.
                        amount=(amount_k_cny * 1000.0 if amount_k_cny is not None else None),
                        turnover_rate=_optional_float(item.get("turnover_rate")),
                        # Tushare daily.vol is reported in lots (100 shares).
                        volume=(volume_lots * 100.0 if volume_lots is not None else None),
                        source=f"tushare:{trade_date}",
                    )
                )
            rows = [row for row in rows if row.code and row.price is not None]
            if rows:
                self.trade_date = trade_date
                self._snapshot = rows
                return rows

        detail = "; ".join(errors[-3:]) or "no populated daily snapshot"
        raise TushareScreenerError(f"Tushare returned no usable EOD snapshot: {detail}")

    def screen(self, *, metric: str, top_n: int) -> list[ScreenRow]:
        _validate_screen_request(metric, top_n)
        rows = self._load_snapshot()
        accessor = {
            "amount": lambda row: row.amount,
            "turnover": lambda row: row.turnover_rate,
            "change_pct": lambda row: row.change_pct,
            "volume": lambda row: row.volume,
        }[metric]
        ranked = [row for row in rows if accessor(row) is not None]
        if not ranked:
            raise TushareScreenerError(
                f"Tushare snapshot {self.trade_date or 'unknown'} lacks metric {metric}"
            )
        ranked.sort(key=lambda row: float(accessor(row) or -math.inf), reverse=True)
        return ranked[:top_n]


class CompositeMarketScreener:
    """Try market screeners in explicit order and record provider decisions."""

    def __init__(self, providers: Iterable[tuple[str, MarketScreener]]) -> None:
        self.providers = tuple(providers)
        if not self.providers:
            raise ValueError("at least one market screener provider is required")
        self.provider_by_metric: dict[str, str] = {}
        self.failures_by_metric: dict[str, tuple[str, ...]] = {}

    def screen(self, *, metric: str, top_n: int) -> list[ScreenRow]:
        failures: list[str] = []
        for name, provider in self.providers:
            try:
                rows = provider.screen(metric=metric, top_n=top_n)
            except Exception as exc:  # noqa: BLE001 - explicit provider failover boundary
                message = f"{name}={type(exc).__name__}: {exc}"
                failures.append(message)
                logger.warning("Market screener provider failed for %s: %s", metric, message)
                continue
            if not rows:
                failures.append(f"{name}=empty")
                continue
            self.provider_by_metric[metric] = name
            self.failures_by_metric[metric] = tuple(failures)
            return rows

        self.failures_by_metric[metric] = tuple(failures)
        raise MarketScreenerError(
            f"all market screener providers failed for {metric}: {'; '.join(failures)}"
        )


def _validate_screen_request(metric: str, top_n: int) -> None:
    if metric not in _METRIC_WEIGHTS:
        raise ValueError(f"unsupported screen metric: {metric}")
    if top_n < 1 or top_n > 100:
        raise ValueError("top_n must be in [1, 100]")


def _parse_json_output(stdout: str) -> dict:
    """Parse the last valid JSON line, tolerating harmless dependency notices."""
    for line in reversed([item.strip() for item in stdout.splitlines() if item.strip()]):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise VibeScreenerError(f"Vibe bridge returned no JSON object: {stdout[-1000:]}")


def _optional_float(value: object) -> float | None:
    if value is None or value == "-":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _normalize_row(raw: Mapping[str, object], *, source: str) -> ScreenRow:
    return ScreenRow(
        code=str(raw.get("code", "")).strip(),
        name=str(raw.get("name", "")).strip(),
        price=_optional_float(raw.get("price")),
        change_pct=_optional_float(raw.get("change_pct")),
        amount=_optional_float(raw.get("amount")),
        turnover_rate=_optional_float(raw.get("turnover_rate")),
        volume=_optional_float(raw.get("volume")),
        source=source,
    )


def _board_limit_pct(code: str) -> float:
    return 20.0 if code.startswith(("300", "301", "688")) else 10.0


def _is_eligible(row: ScreenRow, *, min_amount: float, limit_buffer_pct: float) -> bool:
    code = row.code
    name_upper = row.name.upper()
    if len(code) != 6 or not code.isdigit() or not code.startswith(_ALLOWED_PREFIXES):
        return False
    if "ST" in name_upper or "退" in row.name:
        return False
    if row.price is None or row.price <= 0:
        return False
    if row.amount is None or row.amount < min_amount:
        return False
    if row.change_pct is not None:
        max_abs_change = _board_limit_pct(code) - limit_buffer_pct
        if abs(row.change_pct) >= max_abs_change:
            return False
    return True


def build_candidate_pool(
    screens: Mapping[str, Iterable[ScreenRow]],
    *,
    limit: int,
    min_amount: float = 100_000_000.0,
    limit_buffer_pct: float = 0.5,
    reciprocal_rank_k: int = 60,
) -> list[PrefilterCandidate]:
    """Fuse several market screens using weighted reciprocal-rank fusion."""
    if limit < 1:
        raise ValueError("limit must be positive")
    if reciprocal_rank_k < 1:
        raise ValueError("reciprocal_rank_k must be positive")

    score_by_code: dict[str, float] = defaultdict(float)
    metrics_by_code: dict[str, set[str]] = defaultdict(set)
    sources_by_code: dict[str, set[str]] = defaultdict(set)
    best_row_by_code: dict[str, ScreenRow] = {}

    for metric, rows in screens.items():
        weight = _METRIC_WEIGHTS.get(metric)
        if weight is None:
            raise ValueError(f"unsupported screen metric: {metric}")
        for rank, row in enumerate(rows, start=1):
            if not _is_eligible(
                row,
                min_amount=min_amount,
                limit_buffer_pct=limit_buffer_pct,
            ):
                continue
            score_by_code[row.code] += weight / (reciprocal_rank_k + rank)
            metrics_by_code[row.code].add(metric)
            sources_by_code[row.code].add(row.source)
            current = best_row_by_code.get(row.code)
            if current is None or (row.amount or 0.0) > (current.amount or 0.0):
                best_row_by_code[row.code] = row

    if not score_by_code:
        return []

    max_score = max(score_by_code.values()) or 1.0
    candidates = []
    for code, raw_score in score_by_code.items():
        row = best_row_by_code[code]
        candidates.append(
            PrefilterCandidate(
                code=code,
                name=row.name,
                prefilter_score=round(raw_score / max_score, 6),
                metrics=tuple(sorted(metrics_by_code[code])),
                sources=tuple(sorted(sources_by_code[code])),
                price=row.price,
                change_pct=row.change_pct,
                amount=row.amount,
                turnover_rate=row.turnover_rate,
                volume=row.volume,
            )
        )

    candidates.sort(
        key=lambda item: (
            -item.prefilter_score,
            -(item.amount or 0.0),
            item.code,
        )
    )
    return candidates[:limit]
