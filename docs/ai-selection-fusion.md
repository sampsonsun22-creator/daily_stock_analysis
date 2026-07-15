# A股 AI 选股融合运行手册

## 已融合的能力

本功能没有把截图中的项目整体拷入主仓库，而是采用隔离侧车：

1. **Vibe-Trading** 提供只读的 A 股全市场预筛；
2. 本仓库现有 `StockAnalysisPipeline` 负责行情、技术面、基本面、新闻和 LLM/多 Agent 深度分析；
3. `src/ai_selection/` 使用确定性规则做最终排序、风险扣分和追高扣分；
4. 结果通过现有通知渠道发布，并保存为可追溯 JSON。

生产链路：

```text
Vibe 全市场 amount/turnover 排名
        ↓
排除 ST/退市名称、北交所、低流动性、接近涨跌停标的
        ↓
候选 12 只
        ↓
现有 StockAnalysisPipeline + 可选原生 Multi-Agent
        ↓
确定性综合排名
        ↓
Top 6 JSON + 企业微信/飞书/Telegram/邮件
```

`TradingAgents` 没有直接进入生产链。原因是本仓库已经具备 A 股数据工具和原生多 Agent 编排；强行再引入第二套 LangGraph 运行时会增加依赖冲突和数据口径风险。安装脚本保留 `--with-tradingagents`，仅用于隔离研究环境。

## 1. 安装

主项目按原方式安装：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

安装 Vibe-Trading 隔离侧车：

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

可选安装 TradingAgents 研究实验室：

```bash
bash scripts/install_ai_selection_upstreams.sh --with-tradingagents
```

上游提交固定在 `ai_selection_upstreams.lock.json`，升级前必须重新执行许可证、接口和回归测试。

## 2. 首次运行

使用全市场预筛：

```bash
python ai_select.py \
  --source vibe \
  --metrics amount,turnover \
  --screen-per-metric 60 \
  --candidate-limit 12 \
  --top-n 6
```

发送到现有通知渠道：

```bash
python ai_select.py --source vibe --notify
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

## 3. 启用现有多 Agent 深度分析

不需要安装 TradingAgents。使用仓库原生配置：

```env
AGENT_MODE=true
AGENT_ARCH=multi
AGENT_ORCHESTRATOR_MODE=standard
AGENT_RISK_OVERRIDE=true
```

为控制成本，先保持 `candidate-limit=8~12`。只有在前向评测证明增量有效后再扩大。

## 4. 输出

默认写入：

```text
data/ai_selection/latest.json
data/ai_selection/selection_YYYYMMDD_HHMMSS_<run_id>.json
```

每次结果包含：

- 候选池完整快照、来源和筛选参数；
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

## 5. GitHub Actions

新增工作流 `A股 AI 选股`，北京时间工作日 18:20 运行。它会：

1. 安装主项目依赖；
2. 安装固定提交的 Vibe-Trading；
3. 构建真实当日候选池；
4. 调用现有 AI 分析；
5. 推送选股报告；
6. 上传 JSON 结果 Artifact。

复用现有仓库 Secrets。建议增加 Repository Variables：

```text
AI_SELECTION_CANDIDATE_LIMIT=12
AI_SELECTION_TOP_N=6
AI_SELECTION_AGENT_MODE=false
AI_SELECTION_AGENT_ARCH=single
AI_SELECTION_ORCHESTRATOR_MODE=standard
```

单模型分析本身已经是 AI 分析。确认成本、时延和稳定性后，再把 `AI_SELECTION_AGENT_MODE` 切为 `true`。

## 6. API 读取

本地服务或 Docker 定时运行后，可以通过现有 FastAPI 获取最新结果：

```http
GET /api/v1/ai-selection/latest
```

默认读取 `data/ai_selection/latest.json`。自定义目录时同时设置：

```env
AI_SELECTION_OUTPUT_DIR=/var/lib/dsa/ai-selection
```

## 7. 生产边界

- Vibe 仅被允许读取市场数据，不接触通知密钥、账户密钥或券商接口；
- 侧车失败时任务 fail-closed，不会悄悄回退成另一个股票池；
- 免费数据适合个人内部使用和候选发现，商业交付应换成已授权供应商，并保持同一内部契约；
- 本次功能交付的是每日真实选股与发布，不包含券商自动下单；下单必须接独立 OMS，并另行完成账户、权限、T+1、价格、数量、限速和程序化交易风控。

## 8. 验证

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
