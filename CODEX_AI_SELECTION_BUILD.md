# Codex 任务：复现、部署并验收 A 股 AI 选股生产链路

## 任务目标

请在 ChatGPT Codex cloud 中基于本分支，完整搭建、复现并验收已经合入仓库的 A 股 AI 全市场选股链路。不要另起一套平行实现；先阅读现有代码与文档，发现缺口后做最小修复，并通过本 Pull Request 交付。

当前主实现已包含：

- `ai_select.py`
- `src/ai_selection/`
- `scripts/install_ai_selection_upstreams.sh`
- `scripts/vibe_market_screen.py`
- `.github/workflows/ai_selection.yml`
- `.github/workflows/ai_selection_smoke.yml`
- `GET /api/v1/ai-selection/latest`
- `docs/ai-selection-fusion.md`
- `ai_selection_upstreams.lock.json`

## 开始前必须阅读

1. 根目录 `AGENTS.md`，并严格遵守仓库开发与验证规范。
2. `docs/ai-selection-fusion.md`。
3. `ai_select.py`、`src/ai_selection/screening.py`、`scoring.py`、`service.py`。
4. `.github/workflows/ai_selection.yml` 与 `ai_selection_smoke.yml`。
5. `api/v1/endpoints/ai_selection.py` 和对应 Schema、测试。

## 目标架构

```text
Vibe-Trading 实时 A 股全市场榜单
        │ 失败、超时、5xx 或空结果
        ▼
Tushare 最近完整收盘全市场快照
        │
        ▼
amount / turnover 多指标 reciprocal-rank 融合
        │
        ▼
过滤 ST / 退市名称 / 北交所 / 低流动性 / 近涨跌停
        │
        ▼
现有 StockAnalysisPipeline
行情 + 技术 + 基本面 + 新闻 + LLM / 原生 Multi-Agent
        │
        ▼
确定性评分 + 风险扣分 + 追高扣分
        │
        ▼
Top N JSON + 通知 + FastAPI
```

## Codex 环境要求

- Python 3.12。
- 允许访问 GitHub、PyPI、Vibe-Trading 固定上游和必要的公开行情端点。
- 将敏感值放在 Codex environment secrets，不得写入代码、日志、Issue 或 PR：
  - `TUSHARE_TOKEN`
  - 至少一个可用 LLM 配置，例如 `GEMINI_API_KEY`、`DEEPSEEK_API_KEY`、`OPENAI_API_KEY` 或现有 LiteLLM 配置
  - 通知渠道为可选；首次验收不要发送通知
- 不得接入券商下单，不得提交真实订单。

## 执行步骤

### 1. 搭建依赖与隔离侧车

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
bash scripts/install_ai_selection_upstreams.sh
```

确认：

- Vibe-Trading 固定 SHA 与 lock 文件一致；
- Vibe 使用独立虚拟环境；
- 主仓库与 Vibe 同名顶层 `src` 包不会串包；
- 安装过程不读取主应用密钥。

### 2. 运行确定性验证

```bash
python scripts/check_ai_assets.py
python -m py_compile \
  ai_select.py \
  src/ai_selection/*.py \
  scripts/vibe_market_screen.py \
  api/v1/endpoints/ai_selection.py \
  api/v1/schemas/ai_selection.py

python -m pytest -q \
  tests/test_ai_selection_screening.py \
  tests/test_ai_selection_scoring.py \
  tests/test_ai_selection_api.py

./scripts/ci_gate.sh
```

### 3. 验证各候选源

无全市场密钥的基本路径：

```bash
python ai_select.py \
  --source watchlist \
  --candidate-limit 3 \
  --top-n 2 \
  --force-run
```

Vibe 严格诊断：

```bash
python ai_select.py \
  --source vibe \
  --metrics amount,turnover \
  --screen-per-metric 10 \
  --candidate-limit 5 \
  --top-n 3 \
  --force-run
```

有 `TUSHARE_TOKEN` 时验证 Tushare：

```bash
python ai_select.py \
  --source tushare \
  --metrics amount,turnover \
  --screen-per-metric 10 \
  --candidate-limit 5 \
  --top-n 3 \
  --force-run
```

最终生产模式，不发送通知：

```bash
python ai_select.py \
  --source market \
  --metrics amount,turnover \
  --screen-per-metric 20 \
  --candidate-limit 8 \
  --top-n 5 \
  --force-run
```

### 4. 验证输出契约

确认生成：

```text
data/ai_selection/latest.json
data/ai_selection/selection_*.json
```

逐项检查：

- `schema_version`、`run_id`、北京时间生成时间；
- 候选数、成功分析数和最终入选数；
- 每只候选的预筛来源与实际数据日期；
- `provider_by_metric` 和 provider failure 记录；
- 分项评分、风险扣分和追高扣分；
- Vibe/Eastmoney 价格、涨跌幅、换手率和成交量单位归一化正确；
- Tushare `amount` 从千元转为元，`vol` 从手转为股；
- 所有数据源失败时 fail-closed，不使用陈旧名单或伪造数据。

### 5. 验证 API

启动服务：

```bash
uvicorn server:app --host 127.0.0.1 --port 8000
```

验证：

```text
GET /api/v1/ai-selection/latest
```

要求：

- 正常文件返回 200 且通过 Pydantic Schema；
- 文件不存在返回 404；
- JSON 损坏或 Schema 不兼容返回明确错误；
- 不泄露文件系统绝对路径或密钥。

### 6. 审计 GitHub Actions

检查 `A股 AI 选股` 工作流：

- 北京时间工作日 18:20；
- 默认 `--source market`；
- 候选 12、Top 6、并发 2；
- 首次人工验收时 `notify=false`；
- Artifact 包含 JSON 与运行日志；
- Secrets 只通过环境变量注入；
- 公开 Vibe 端点故障时能够显式回退到 Tushare；
- 所有提供方都失败时工作流失败并保留诊断证据。

## 缺口修复原则

只修复真实发现的问题。优先级：

1. 数据正确性和单位契约；
2. 密钥与权限隔离；
3. provider 超时、重试、fallback 和 fail-closed；
4. 评分可重复性；
5. API Schema 兼容；
6. CI、Docker 和运行文档。

禁止：

- 让 LLM 单独决定最终排名；
- 静默回退到 `STOCK_LIST`；
- 将 TradingAgents、TensorTrade 或券商执行链强行塞进生产主进程；
- 写死密钥、Token、端口或本机路径；
- 未经测试的大范围重构。

## 验收标准

- 新增/修改代码有对应测试；
- `./scripts/ci_gate.sh` 通过；
- AI Selection smoke 通过；
- Docker 构建与关键导入 smoke 通过；
- 至少成功完成一次 `watchlist` 端到端运行；
- 环境有密钥时，至少成功完成一次 `market` 端到端运行；
- API 契约验证通过；
- PR 中明确列出：改动、原因、测试证据、未验证项、风险、回滚方式；
- 不自动合并，由人工审阅后决定。

若现有实现全部满足要求，不要为了产生 diff 而改代码；请在 PR 中提交基于真实命令输出的验收报告，并说明无需代码变更的证据。
