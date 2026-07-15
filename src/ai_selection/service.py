from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .models import PrefilterCandidate, RankedSelection, SelectionRun
from .scoring import SelectionScorer

_CN_TZ = timezone(timedelta(hours=8))


class AISelectionService:
    def __init__(self, scorer: SelectionScorer | None = None) -> None:
        self.scorer = scorer or SelectionScorer()

    def rank(
        self,
        *,
        results: Iterable[Any],
        prefilter_candidates: Iterable[PrefilterCandidate],
        source: str,
        top_n: int,
        metadata: Mapping[str, Any] | None = None,
    ) -> SelectionRun:
        if top_n < 1:
            raise ValueError("top_n must be positive")
        prefilter_map = {item.code: item for item in prefilter_candidates}
        ranked: list[RankedSelection] = []
        for result in results:
            code = str(getattr(result, "code", "")).strip()
            if not code or code not in prefilter_map:
                continue
            if getattr(result, "success", True) is False:
                continue
            ranked.append(self.scorer.score(result, prefilter_map[code]))

        ranked.sort(key=lambda item: (-item.final_score, item.code))
        selected = tuple(ranked[:top_n])
        generated_at = datetime.now(_CN_TZ)
        manifest = {
            "generated_at": generated_at.isoformat(),
            "source": source,
            "candidate_codes": sorted(prefilter_map),
            "selected": [(item.code, item.final_score) for item in selected],
        }
        run_id = hashlib.sha256(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:20]
        run_metadata = dict(metadata or {})
        run_metadata.setdefault("scoring_version", "ai-selection-v1")

        return SelectionRun(
            run_id=run_id,
            generated_at=generated_at,
            source=source,
            candidate_count=len(prefilter_map),
            analyzed_count=len(ranked),
            selected=selected,
            metadata=run_metadata,
        )

    @staticmethod
    def persist(run: SelectionRun, output_dir: str | Path) -> tuple[Path, Path]:
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        date_name = run.generated_at.strftime("%Y%m%d_%H%M%S")
        dated_path = directory / f"selection_{date_name}_{run.run_id}.json"
        latest_path = directory / "latest.json"
        payload = json.dumps(run.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

        for target in (dated_path, latest_path):
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, target)
        return dated_path, latest_path

    @staticmethod
    def format_markdown(run: SelectionRun) -> str:
        lines = [
            f"# 🤖 A股 AI 选股结果 {run.generated_at.strftime('%Y-%m-%d %H:%M')}",
            "",
            f"运行ID：`{run.run_id}`",
            f"候选池：{run.candidate_count}；成功分析：{run.analyzed_count}；入选：{len(run.selected)}",
            "",
            "| 排名 | 股票 | 综合分 | 建议 | 置信度 | 风险数 |",
            "|---:|---|---:|---|---|---:|",
        ]
        for index, item in enumerate(run.selected, start=1):
            lines.append(
                f"| {index} | {item.name}({item.code}) | {item.final_score:.3f} | "
                f"{item.operation_advice} | {item.confidence_level} | {len(item.risk_flags)} |"
            )
        lines.extend(
            [
                "",
                "> 结果由流动性/活跃度预筛、技术结构和 AI 分析共同排序；不构成收益承诺或个性化投资建议。",
            ]
        )
        return "\n".join(lines)
