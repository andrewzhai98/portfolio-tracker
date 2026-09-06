# Trading 212 Portfolio Tracker v2.4

这是一个用于从多个 Trading 212 API 账号采集资产数据、写入 Supabase，并提供 Supabase 直连 Dashboard 的个人资产管理 MVP。

v2.4 重点更新：

1. **Dashboard 改为读取 Supabase**：`dashboard/index.html` 通过 Supabase JS 调用只读 RPC，不再依赖本地 CSV。
2. **新增 Dashboard RPC**：`sql/dashboard_rpc.sql` 提供资产趋势、现金流趋势、持仓分布和 AI 报告上下文。
3. **保留 CSV 为可选导出**：`scripts/export_csv.py` 仍可用于备份、调试和离线分析，但不再是 Dashboard 主链路。
4. **自动化主流程明确**：推荐使用 GitHub Actions 每天运行 `python scripts/run_sync.py` 写入 Supabase。
5. **安全边界明确**：前端只能使用 Supabase anon key；`SUPABASE_SERVICE_ROLE_KEY` 只能放在本地 `.env`、GitHub Secrets 或后端环境变量。

## 这个项目能做什么

当前版本支持：

1. 多个 Trading 212 API 账号配置，例如 Invest 和 Stocks ISA。
2. 采集账户概览、当前持仓、当天交易流水。
3. 写入 Supabase PostgreSQL。
4. 保存原始 API 响应 `raw_payload`，方便后续重新解析和分析。
5. 生成每日指标 `daily_metrics`。
6. 聚合每日现金流 `daily_cash_flows`。
7. 清理旧版同步造成的 `unknown_position_*` 脏数据。
8. 导出 CSV：
   - `daily_metrics_*.csv`
   - `daily_cash_flows_*.csv`
   - `account_summary_raw_*.csv`
   - `latest_positions_*.csv`
9. Supabase 直连 Dashboard。
10. 支持 GitHub Actions 每日自动运行。
11. 提供 AI 报告读取 RPC：`get_ai_report_context(p_days)`。

> 说明：本项目只做个人资产数据采集、整理和可视化前置数据准备，不构成投资建议。

## 项目结构

```text
portfolio-tracker-v2/
  src/
    config.py
    trading212_client.py
    supabase_store.py
    metrics.py
    sync.py
    export_csv.py
  sql/
    schema.sql
    dashboard_rpc.sql
    cleanup_dirty_data.sql
    README.md
  scripts/
    run_sync.py
    export_csv.py
    cleanup_dirty_data.py
  dashboard/
    index.html
    styles.css
    app.js
    config.example.js
    README.md
  .github/
    workflows/
      daily-sync.yml
  .env.example
  requirements.txt
  README.md
```

## 推荐架构

正式主链路：

```text
Trading 212 API
    ↓
GitHub Actions / 本地定时任务
    ↓
python scripts/run_sync.py
    ↓
Supabase PostgreSQL
    ↓
只读 RPC
    ↓
Dashboard / AI 报告
```

CSV 变成旁路：

```text
Supabase
    ↓
python scripts/export_csv.py
    ↓
exports/*.csv
```

也就是说：

- Supabase 是唯一正式数据源。
- Dashboard 不再读本地 CSV。
- CSV 只用于备份、调试、Excel 分析或临时给其他工具使用。

## 快速开始

### 1. 创建或更新 Supabase 表

在 Supabase 项目里打开 SQL Editor，先执行：

```text
sql/schema.sql
```

这会创建/更新以下表：

- `accounts`
- `sync_runs`
- `account_snapshots`
- `position_snapshots`
- `transactions`
- `daily_cash_flows`
- `daily_metrics`

以及基础 RPC：

- `get_latest_positions()`
- `get_latest_dashboard_summary()`

### 2. 创建 Dashboard / AI 报告 RPC

继续在 Supabase SQL Editor 执行：

```text
sql/dashboard_rpc.sql
```

这会新增：

- `get_portfolio_timeseries(p_days integer)`：资产趋势。
- `get_cash_flow_timeseries(p_days integer)`：现金流趋势。
- `get_position_allocation()`：当前持仓权重。
- `get_ai_report_context(p_days integer)`：给 AI 或自动报告读取的压缩 JSON 上下文。

### 3. 配置环境变量

复制环境变量模板：

```bash
cp .env.example .env
```

然后填写 `.env`：

```bash
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key
SUPABASE_ANON_KEY=your_supabase_anon_key

TRADING212_BASE_URL=https://live.trading212.com
TRADING212_ACCOUNTS=invest:your_invest_api_key:your_invest_api_secret:GBP,stock_isa:your_isa_api_key:your_isa_api_secret:GBP

TRADING212_ACCOUNT_CASH_PATH=/api/v0/equity/account/summary
TRADING212_PORTFOLIO_PATH=/api/v0/equity/positions
TRADING212_TRANSACTIONS_PATH=/api/v0/equity/history/transactions

EXPORT_DIR=exports
SYNC_TRANSACTIONS=true
TRANSACTION_LOOKBACK_DAYS=1
TRANSACTION_MAX_PAGES=1
TRANSACTION_PAGE_DELAY_SECONDS=15
```

`TRADING212_ACCOUNTS` 格式是：

```text
account_key:api_key:api_secret:base_currency
```

多账号用英文逗号分隔：

```text
invest:api_key_1:api_secret_1:GBP,stock_isa:api_key_2:api_secret_2:GBP
```

不要把 `.env` 提交到 Git。

### 4. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 5. 如有旧脏数据，先清理

如果你已经用旧版本同步过数据，建议先跑一次清理脚本：

```bash
python scripts/cleanup_dirty_data.py
```

确认没问题后执行：

```bash
python scripts/cleanup_dirty_data.py --yes
```

这个脚本会：

1. 删除 `position_snapshots.ticker like 'unknown_position_%'` 的旧 fallback 行。
2. 删除受影响日期的旧 `daily_metrics`。
3. 基于剩余干净持仓重新计算受影响日期的 `daily_metrics`。

### 6. 执行同步

```bash
python scripts/run_sync.py
```

你应该能看到类似输出：

```text
Fetched 12 positions for invest
Fetched 3 transactions for invest
Fetched 8 positions for stock_isa
Fetched 1 transactions for stock_isa
Sync completed
```

v2.4 同步时仍会先删除当前 account/date 的旧 positions，再写入最新 positions，所以同一天不会出现旧 fallback ticker 和新真实 ticker 共存的问题。

### 7. 配置 Supabase Dashboard

复制前端配置：

```bash
cp dashboard/config.example.js dashboard/config.js
```

编辑 `dashboard/config.js`：

```js
window.PORTFOLIO_SUPABASE_CONFIG = {
  url: 'https://your-project-id.supabase.co',
  anonKey: 'your_supabase_anon_key',
};
```

注意：这里必须使用 Supabase anon key，不能使用 service role key。

然后打开：

```text
dashboard/index.html
```

Dashboard 会读取：

- `get_latest_dashboard_summary()`
- `get_position_allocation()`
- `get_portfolio_timeseries(90)`

### 8. 可选导出 CSV

如果你想离线分析或备份，再运行：

```bash
python scripts/export_csv.py
```

导出文件会放在：

```text
exports/
```

新版会导出四类文件：

1. `daily_metrics_*.csv`：每日汇总指标，适合做资产趋势、账户对比、集中度。
2. `daily_cash_flows_*.csv`：每日现金流聚合，包含买入、卖出、入金、出金、分红、利息、费用、净投入和交易笔数。
3. `account_summary_raw_*.csv`：每个账号的账户概览，包含标准字段和 Trading 212 summary 原始字段展开后的 `raw_*` 列。
4. `latest_positions_*.csv`：最新持仓明细，包含标准字段和 Trading 212 positions 原始字段展开后的 `raw_*` 列。

## 推荐运行顺序

第一次升级到 v2.4 后：

```bash
source .venv/bin/activate
python scripts/cleanup_dirty_data.py
python scripts/cleanup_dirty_data.py --yes
python scripts/run_sync.py
```

然后：

1. 在 Supabase SQL Editor 执行 `sql/schema.sql`。
2. 在 Supabase SQL Editor 执行 `sql/dashboard_rpc.sql`。
3. 配置 `dashboard/config.js`。
4. 打开 `dashboard/index.html`。

以后每天自动刷新只需要：

```bash
python scripts/run_sync.py
```

CSV 导出按需运行：

```bash
python scripts/export_csv.py
```

## GitHub Actions 自动运行

项目已经包含：

```text
.github/workflows/daily-sync.yml
```

你需要在 GitHub 仓库里配置 Secrets：

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `TRADING212_ACCOUNTS`

可选 Variables：

- `TRADING212_BASE_URL`
- `TRADING212_ACCOUNT_CASH_PATH`
- `TRADING212_PORTFOLIO_PATH`
- `TRADING212_TRANSACTIONS_PATH`
- `SYNC_TRANSACTIONS`
- `TRANSACTION_LOOKBACK_DAYS`
- `TRANSACTION_MAX_PAGES`
- `TRANSACTION_PAGE_DELAY_SECONDS`

默认每天 UTC 06:00 自动运行，也支持手动点 `workflow_dispatch`。

## AI 报告读取方式

如果后续你或其他 AI 想自动分析组合，优先调用 Supabase RPC：

```text
get_ai_report_context(p_days)
```

例如传 `90`，它会返回：

- 当前组合摘要。
- 账户拆分。
- Top holdings。
- 资产趋势。
- 现金流趋势。
- 数据质量提示。
- 最近同步状态。

这样 AI 不需要直接读所有原始表，也不需要理解数据库结构。

## 字段映射说明

Trading 212 的 positions 返回里，很多关键字段是嵌套结构。当前版本会优先读取：

- `instrument.ticker` / `raw_instrument_ticker` → `ticker`
- `instrument.name` / `raw_instrument_name` → `instrument_name`
- `instrument.currency` / `walletImpact.currency` / `raw_*_currency` → `currency`
- `walletImpact.currentValue` / `raw_walletImpact_currentValue` → `market_value`
- `walletImpact.totalCost` / `raw_walletImpact_totalCost` → `cost_basis`
- `walletImpact.unrealizedProfitLoss` / `raw_walletImpact_unrealizedProfitLoss` → `unrealized_pnl`
- `averagePricePaid` → `average_price`

这样可以避免 UK/GBX 标的用 `quantity * current_price` 计算时出现约 100 倍偏差。

## 每日现金流说明

开启：

```bash
SYNC_TRANSACTIONS=true
TRANSACTION_LOOKBACK_DAYS=1
TRANSACTION_MAX_PAGES=1
```

同步脚本会把交易流水聚合为 `daily_cash_flows`：

- `deposit_amount`：入金
- `withdrawal_amount`：出金
- `buy_amount`：买入金额
- `sell_amount`：卖出金额
- `dividend_amount`：分红
- `interest_amount`：利息
- `fee_amount`：交易费用
- `fx_fee_amount`：换汇费用
- `net_contribution`：入金 - 出金
- `net_trading_cash_flow`：卖出 + 分红 + 利息 - 买入 - 费用 - 换汇费用
- `transaction_count`：当天交易笔数

当前版本会按 `dateTime` 判断交易发生日期，只聚合同一天交易；如果 Trading 212 返回最近一页里含多天交易，跨天交易不会被错误算进今天。

如果遇到 Trading 212 `429 Too Many Requests`，建议先保持 `TRANSACTION_MAX_PAGES=1`，必要时临时把 `SYNC_TRANSACTIONS=false`，账户概览和持仓仍会正常同步。

## Dashboard 安全说明

前端可以使用：

```text
SUPABASE_URL
SUPABASE_ANON_KEY
```

前提是：

- 只调用只读 RPC / View。
- 不开放写入权限。
- 不暴露 service role key。
- 如果以后公开部署，建议增加登录鉴权或改成后端代理。

绝对不能放进前端的是：

```text
SUPABASE_SERVICE_ROLE_KEY
TRADING212_API_KEY
TRADING212_API_SECRET
```

这些只能放在：

- 本地 `.env`
- GitHub Secrets
- 后端环境变量
- Secret Manager

不能放在：

- `dashboard/app.js`
- `dashboard/config.js`
- HTML
- GitHub 公开仓库
- 浏览器环境变量
- 前端构建产物

## 当前版本限制

1. 只做数据采集和基础指标，不提供投资建议。
2. 交易流水默认只拉一天，不自动回补历史。
3. Trading 212 字段可能变化，所以本版本会保存并导出原始字段。
4. Dashboard 当前为静态前端直连 Supabase；如果以后要公开分享，建议升级为后端代理模式。
5. 历史已经污染的非 `unknown_position_*` 异常数据不会被脚本盲目修复；如果历史 raw payload 可恢复，建议以后做专门 backfill。

## 下一步建议

1. 连续同步几天，观察 `get_portfolio_timeseries(90)` 趋势数据。
2. 如需自动报告，让 AI 或脚本调用 `get_ai_report_context(90)`。
3. 如果要公开部署 Dashboard，增加 Supabase Auth 或后端代理。
4. 如果要做更深入分析，可以新增资产类别映射表，把 ETF/股票归类为权益、债券、黄金、现金等。
