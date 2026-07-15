from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Mapping

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


class VibeScreenerError(RuntimeError):
    """Raised when the isolated Vibe-Trading bridge cannot return valid data."""


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
        if metric not in _METRIC_WEIGHTS:
            raise ValueError(f"unsupported screen metric: {metric}")
        if top_n < 1 or top_n > 100:
            raise ValueError("top_n must be in [1, 100]")
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
        if completed.returncode != 0:
            raise VibeScreenerError(
                "Vibe market screener failed: "
                f"returncode={completed.returncode}, stderr={completed.stderr[-1000:]}"
            )

        payload = _parse_json_output(completed.stdout)
        if payload.get("ok") is not True:
            raise VibeScreenerError(f"Vibe market screener rejected request: {payload}")
        rows = payload.get("data", {}).get("rows", [])
        if not isinstance(rows, list):
            raise VibeScreenerError("Vibe payload does not contain data.rows list")
        return [_normalize_row(item) for item in rows if isinstance(item, dict)]


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


def _normalize_row(raw: Mapping[str, object]) -> ScreenRow:
    return ScreenRow(
        code=str(raw.get("code", "")).strip(),
        name=str(raw.get("name", "")).strip(),
        price=_optional_float(raw.get("price")),
        change_pct=_optional_float(raw.get("change_pct")),
        amount=_optional_float(raw.get("amount")),
        turnover_rate=_optional_float(raw.get("turnover_rate")),
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
                price=row.price,
                change_pct=row.change_pct,
                amount=row.amount,
                turnover_rate=row.turnover_rate,
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
