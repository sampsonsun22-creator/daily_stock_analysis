# A股 AI 选股融合运行手册

## 已融合的能力

本功能没有把截图中的项目整体拷入主仓库，而是采用隔离侧车和内部数据契约：

1. **Vibe-Trading** 提供只读 A 股全市场实时榜单；
2. **Tushare Pro** 提供收盘全市场快照兜底，连接 `daily`、`daily_basic` 和 `stock_basic`；
3. 本仓库现有 `StockAnalysisPipeline` 继续负责行情、技术面、基本面、新闻和 LLM/多 Agent 深度分析；
4. `src/ai_selection/` 使用确定性规则做候选融合、最终排序、风险扣分和追高扣分；
5. 结果通过现有通知渠道发布，并保存为带来源和运行 ID 的 JSON。

生产链路：

```text
Vibe 实时榜单
      │ 失败/空结果
      ▼
Tushare 最新完整收盘快照
      │
      ▼
amount / turnover 等指标 reciprocal-rank 融合
      │
      ▼
排除 ST/退市名称、北交所、低流动性、接近涨跌停标的
      │
      ▼
候选 12 只
      │
      ▼
现有 StockAnalysisPipeline + 可选原生 Multi-Agent
      │
      ▼
确定性综合排名
      │
      ▼
Top 6 JSON + 企业微信/飞书/Telegram/邮件
```

`TradingAgents` 没有直接进入生产链。原因是本仓库已经具备 A 股数据工具和原生多 Agent 编排；再引入第二套 LangGraph 运行时会增加依赖冲突、时延和数据口径风险。安装脚本保留 `--with-tradingagents`，仅用于隔离研究环境。

## 1. 安装

主项目按原方式安装：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

安装固定提交的 Vibe-Trading 隔离侧车：

```bash
bash scripts/install_ai_selection_upstreams.sh
```

默认位置：

```text
~/.dsa-ai/upstreams/src/vibe-trading
~/.dsa-ai/upstreams/venvs/vibe
```

指定其他位置：

```bash
DSA_AI_UPSTREAM_ROOT=/opt/dsa/upstreams \
  bash scripts/install_ai_selection_upstreams.sh
```

安装脚本会在干净目录校验 Vibe 的 `src` 包，避免它与本仓库同名的顶层 `src` 包发生导入冲突。

可选安装 TradingAgents 研究实验室：

```bash
bash scripts/install_ai_selection_upstreams.sh --with-tradingagents
```

上游提交固定在 `ai_selection_upstreams.lock.json`。升级前必须重新执行许可证、接口和回归测试。

## 2. 数据源配置

推荐同时配置两个来源：

```env
# 主仓库原有配置；market 模式的生产兜底需要该 Token
TUSHARE_TOKEN=your_token

# 可选；不设置时按默认安装目录自动推导
VIBE_PYTHON=/home/user/.dsa-ai/upstreams/venvs/vibe/bin/python
```

`market` 模式的行为：

```text
每个筛选指标先调用 Vibe
→ Vibe 失败或返回空结果时调用 Tushare
→ 某一个指标失败但其他指标成功时降级运行并记录错误
→ 所有指标、所有提供方都失败时停止，不使用旧名单或伪造候选
```

Tushare 会从当天向前查找最近一个有完整 `daily` 数据的交易日，因此在供应商尚未完成当日收盘数据更新时，会明确使用最近完整交易日，并把日期写入候选来源，例如：

```text
tushare:20260714
```

## 3. 首次运行

推荐生产模式：

```bash
python ai_select.py \
  --source market \
  --metrics amount,turnover \
  --screen-per-metric 60 \
  --candidate-limit 12 \
  --top-n 6
```

发送到现有通知渠道：

```bash
python ai_select.py --source market --notify
```

严格只使用 Vibe，主要用于诊断：

```bash
python ai_select.py --source vibe --top-n 6
```

严格只使用 Tushare 收盘快照：

```bash
python ai_select.py --source tushare --top-n 6
```

使用现有自选股验证：

```bash
python ai_select.py --source watchlist --top-n 5
```

手动指定：

```bash
python ai_select.py --source manual --stocks 600519,300750,688981 --top-n 3
```

非交易日默认跳过。测试时显式加入：

```bash
python ai_select.py --source watchlist --force-run
```

## 4. 启用现有多 Agent 深度分析

生产链默认复用仓库现有单 Agent 分析。完成成本和时延评估后，可启用原生多 Agent：

```env
AGENT_MODE=true
AGENT_ARCH=multi
AGENT_ORCHESTRATOR_MODE=standard
AGENT_RISK_OVERRIDE=true
```

不需要为生产安装 TradingAgents。先保持 `candidate-limit=8~12`，只有在前向评测证明多 Agent 存在增量价值后再扩大。

## 5. 输出与审计

默认写入：

```text
data/ai_selection/latest.json
data/ai_selection/selection_YYYYMMDD_HHMMSS_<run_id>.json
```

每次结果包含：

- 候选池完整快照、指标和数据源；
- 每个指标实际使用的提供方；
- 上游失败原因和降级记录；
- 入选股票及综合分；
- 市场预筛、趋势、AI 分析、决策和置信度分项；
- 风险扣分和追高扣分；
- 模型名称、数据来源、上游提交锁；
- 运行 ID 和生成时间。

核心评分权重：

```text
市场预筛       35%
技术趋势       30%
AI 综合分析    20%
决策类型       10%
置信度          5%
- 风险扣分
- 追高扣分
```

LLM 不能单独把一只股票推到第一名。

## 6. GitHub Actions

工作流 `A股 AI 选股` 在北京时间工作日 18:20 运行：

1. 安装主项目依赖；
2. 安装固定提交的 Vibe-Trading；
3. 以 `market` 模式构建真实候选池；
4. 调用现有 AI 分析；
5. 推送选股报告；
6. 上传 JSON 和日志 Artifact。

复用现有仓库 Secrets，其中 `TUSHARE_TOKEN` 是 Vibe 公共端点不可用时的生产兜底。建议增加 Repository Variables：

```text
AI_SELECTION_CANDIDATE_LIMIT=12
AI_SELECTION_TOP_N=6
AI_SELECTION_AGENT_MODE=false
AI_SELECTION_AGENT_ARCH=single
AI_SELECTION_ORCHESTRATOR_MODE=standard
```

单模型分析本身已经是 AI 分析。确认成本、时延和稳定性后，再把 `AI_SELECTION_AGENT_MODE` 切为 `true`。

另有 `AI Selection Integration Smoke`：

- 固定提交安装和隔离导入失败会阻断 PR；
- Vibe 公共端点会被真实探测并上传响应证据；
- 公共端点临时 5xx 不会被误判为安装失败；
- Tushare 兜底、来源留痕和多源切换由离线测试覆盖。

## 7. API 读取

本地服务或 Docker 定时运行后，通过现有 FastAPI 获取最新结果：

```http
GET /api/v1/ai-selection/latest
```

默认读取 `data/ai_selection/latest.json`。自定义目录时同时设置：

```env
AI_SELECTION_OUTPUT_DIR=/var/lib/dsa/ai-selection
```

## 8. 生产边界

- Vibe 仅被允许读取市场数据，不接触通知密钥、账户密钥或券商接口；
- Vibe 到 Tushare 的切换是显式、可审计的，不是静默回退；
- 所有提供方均不可用时 fail-closed；
- 免费数据适合个人内部使用和候选发现，商业交付应换成已授权供应商，并保持同一 `ScreenRow` 内部契约；
- 本次功能交付的是每日真实选股与发布，不包含券商自动下单；下单必须接独立 OMS，并另行完成账户、权限、T+1、价格、数量、限速和程序化交易风控。

## 9. 故障处理

### Vibe 返回 502/限流

无需修改选股逻辑。确认：

```text
TUSHARE_TOKEN 已配置
--source market
```

结果的 `metadata.screening.provider_by_metric` 会显示是否切换到 Tushare；`provider_failures` 会保存 Vibe 错误。

### Vibe 安装后导入了错误的 src 包

重新运行最新版安装脚本：

```bash
bash scripts/install_ai_selection_upstreams.sh
```

脚本会在临时目录中清除 `PYTHONPATH/PYTHONHOME` 后校验隔离导入。

### Tushare 当日数据为空

系统会向前寻找最近完整交易日。若超过回看窗口仍为空，则停止运行并记录错误，不使用未来数据。

## 10. 验证

```bash
python -m pytest -q \
  tests/test_ai_selection_screening.py \
  tests/test_ai_selection_scoring.py \
  tests/test_ai_selection_api.py

python -m py_compile \
  ai_select.py \
  src/ai_selection/*.py \
  scripts/vibe_market_screen.py \
  api/v1/endpoints/ai_selection.py \
  api/v1/schemas/ai_selection.py
```
