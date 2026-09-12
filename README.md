# Trading 212 Portfolio Tracker v2.5.4

这是一个用于从多个 Trading 212 API 账号采集资产数据、写入 Supabase，并提供可选 Dashboard / CSV 导出的个人资产数据仓库项目。

v2.5 的重点不是可视化，而是补齐数仓验证链路：**订单明细、Raw API 归档、同步告警、本地导出审计文件**。

> 说明：本项目只做个人资产数据采集、整理和分析前置数据准备，不构成投资建议。

## v2.5.4 修复内容

v2.5.4 把 v2.5.3 的现金流修复逻辑合并进正常同步链路：以后运行 `python scripts/run_sync.py` 时，会自动根据本次 lookback window 里抓到的已成交订单，按真实成交日期写入 / 更新 `daily_cash_flows` 的买入、卖出和交易现金流，不需要每天再单独跑现金流 repair 脚本。

本版本新增 / 调整：

1. `scripts/run_sync.py` 正常同步时会从 transactions 和 orders 的实际日期生成现金流日期集合，不再只写当天 `snapshot_date` 一行。
2. `daily_cash_flows.buy_amount` / `sell_amount` 会优先使用已成交买入 / 卖出订单计算；取消、失败、非成交订单不会再把现金流来源误判为 orders。
3. 对历史现金流日期，如果当天已有旧行，会保留已有 `opening_cash`、`closing_cash`、`cash_change`，避免因为历史日期没有账户 snapshot 而覆盖成空值。
4. `raw_payload` 会记录 `buy_amount_source = order_history`、`computed_from_order_history = true`、订单数量和排除数量，方便后续审计。

### 从 v2.5.3 升级后只需要执行

```bash
python scripts/run_sync.py
python scripts/export_csv.py
```

`repair_daily_cash_flows_from_orders.py` 仍保留，用于旧数据的手动一次性修复；日常同步不再需要单独执行它。

## v2.5.3 修复内容

v2.5.3 针对导出结果补齐现金流修复：`order_history` 已经能正确解析订单明细，但旧的 `daily_cash_flows` 仍显示 `buy_amount = 0` / `sell_amount = 0`。本版本新增脚本，用已经修好的 `order_history` 按账号和成交日期回填 `daily_cash_flows` 的买入 / 卖出金额。

本版本新增：

1. `scripts/repair_daily_cash_flows_from_orders.py`：读取现有 `daily_cash_flows` 和标准化后的 `order_history`，按 `(account_id, order_date)` 汇总已成交买入 / 卖出订单。
2. 回填 `buy_amount`、`sell_amount`、`net_trading_cash_flow`、`transaction_count`。
3. 在 `daily_cash_flows.raw_payload` 中记录订单来源、订单数量、订单分类，并标记 `cash_flow_repaired_from_order_history = true`。

### 已经跑过 v2.5.2 的用户，只需要新增执行

```bash
python scripts/repair_daily_cash_flows_from_orders.py
python scripts/repair_daily_cash_flows_from_orders.py --apply
python scripts/export_csv.py
```

第一行是 dry run，用来先看会修多少个已有现金流日期；第二行才真正写回 Supabase；第三行重新导出验证文件。

> 如果 `order_history` 还没有先经过 v2.5.2 修复，请先执行旧脚本 `python scripts/repair_order_history_from_raw.py --apply --delete-stale`，再执行本节现金流修复。

## v2.5.2 修复内容

v2.5.2 针对你最新发回的导出结果做了“Raw Data 回填”修复：数据库里已经有 `raw_payload`，但旧的 `order_history` 标准字段仍为空，所以本版本会直接从 raw order payload 重新解析订单标准字段。

本版本新增：

1. `scripts/repair_order_history_from_raw.py`：读取现有 `order_history.raw_payload`，重新解析 `fill.*` / `order.*`，并 upsert 为标准订单行。
2. `scripts/export_csv.py` 导出增强：即使数据库里的旧标准字段为空，导出的 `order_history_*.csv` 也会从 `raw_payload` 自动补齐 `order_time`、`order_type`、`status`、`ticker`、`filled_quantity`、`average_price`、`total_value`、`currency`。
3. 导出的订单增加 `legacy_provider_order_id`，方便对比旧的 `order_*` hash ID 和新的 `fill.id`。

### 已经跑过 v2.5 / v2.5.1 的用户，只需要新增执行

```bash
python scripts/repair_order_history_from_raw.py
python scripts/repair_order_history_from_raw.py --apply --delete-stale
python scripts/export_csv.py
```

第一行是 dry run，用来先看会修多少行；第二行才真正写回 Supabase，并删除旧的空字段 `order_*` 脏行；第三行重新导出验证文件。

## v2.5.1 修复内容

v2.5.1 是基于你发回的本地验证导出做的订单归一化修复包：Trading 212 orders endpoint 实际已经返回订单明细，但 payload 是嵌套结构 `order.*` / `fill.*`。v2.5 已抓到 raw orders，但标准化字段没有正确展开。

本版本修复：

1. `order_history` 正确解析 `order.side`、`order.status`、`order.instrument.ticker`、`fill.quantity`、`fill.price`、`fill.walletImpact.netValue`、`fill.walletImpact.currency`。
2. `provider_order_id` 优先使用 `fill.id`，避免同一个 order 下多次 fill 被覆盖。
3. `daily_cash_flows` 可按 `fill.filledAt` / `order.createdAt` 识别当天 orders，并优先用订单明细计算买入 / 卖出金额。
4. Raw API 归档逻辑保持不变，仍用于后续字段验证。

## v2.5 新增内容

1. **新增订单历史同步**：增加 `/api/v0/equity/history/orders` 拉取逻辑，写入 `order_history`，用于验证 Auto Pie / payout-to-invest 是否能从 Trading 212 API 返回订单级明细。
2. **新增 Raw API 归档**：新增 `raw_api_events`，保存每个 endpoint/page 的原始响应，方便判断问题是“API 没返回”还是“解析逻辑漏字段”。
3. **新增同步告警**：新增 `sync_warnings`，记录订单为空、交易同步关闭、Raw 归档关闭等情况。
4. **增强每日现金流**：`daily_cash_flows` 会优先用当天订单明细计算买入/卖出；如果当天没有订单，则回退使用 transactions。
5. **新增本地验证导出**：`scripts/export_csv.py` 会导出 `order_history_*`、`raw_api_events_*`、`sync_warnings_*` 和 `warehouse_audit_*`，便于你本地跑完后发回检查。
6. **GitHub Actions 支持 v2.5 配置**：新增 orders/raw 相关环境变量。

## 推荐架构

正式主链路：

```text
Trading 212 API
    ↓
GitHub Actions / 本地手动运行
    ↓
python scripts/run_sync.py
    ↓
Supabase PostgreSQL
    ↓
只读 RPC / CSV 导出 / Dashboard / AI 报告
```

CSV 是验证和备份旁路：

```text
Supabase
    ↓
python scripts/export_csv.py
    ↓
exports/*.csv + warehouse_audit_*.json
```

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
    v2_5_warehouse_patch.sql
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

## 如果你已经跑过 v2.4，只需要做这些新增步骤

### 1. 在 Supabase 执行 v2.5 SQL Patch

打开 Supabase SQL Editor，执行：

```text
sql/v2_5_warehouse_patch.sql
```

它会新增：

- `raw_api_events`
- `order_history`
- `sync_warnings`
- `get_latest_order_history(p_limit integer)`
- `get_latest_sync_warnings(p_limit integer)`

不需要重复执行旧的 v2.4 基础 schema，除非你是全新数据库。

### 2. 新增或确认环境变量

本地 `.env` 或 GitHub Variables 中新增/确认：

```text
TRADING212_ORDERS_PATH=/api/v0/equity/history/orders
SYNC_ORDERS=true
ORDER_LOOKBACK_DAYS=7
ORDER_MAX_PAGES=3
ORDER_PAGE_DELAY_SECONDS=15
SYNC_RAW_API=true
```

已有的这些变量继续保留：

```text
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
TRADING212_BASE_URL=https://live.trading212.com
TRADING212_ACCOUNTS=invest:api_key:api_secret:GBP,stock_isa:api_key:api_secret:GBP
TRADING212_ACCOUNT_CASH_PATH=/api/v0/equity/account/summary
TRADING212_PORTFOLIO_PATH=/api/v0/equity/positions
TRADING212_TRANSACTIONS_PATH=/api/v0/equity/history/transactions
SYNC_TRANSACTIONS=true
TRANSACTION_LOOKBACK_DAYS=1
TRANSACTION_MAX_PAGES=1
TRANSACTION_PAGE_DELAY_SECONDS=15
EXPORT_DIR=exports
```

`TRADING212_ACCOUNTS` 格式仍然是四段：

```text
account_key:api_key:api_secret:base_currency
```

多账号用英文逗号分隔。

### 3. 本地跑一次同步和导出

```bash
source .venv/bin/activate
python scripts/run_sync.py
python scripts/export_csv.py
```

导出文件在：

```text
exports/
```

请重点检查并发回这些文件：

- `exports/order_history_*.csv`
- `exports/raw_api_events_*.csv`
- `exports/sync_warnings_*.csv`
- `exports/warehouse_audit_*.json`

这些文件用于判断：

1. Trading 212 是否真的从 orders endpoint 返回了 Auto Pie / payout-to-invest 明细。
2. 如果没返回，是 endpoint 空、分页参数不对，还是字段藏在 raw payload 中。
3. `daily_cash_flows` 的买入/卖出金额来自 orders 还是 transactions。

## 如果你是全新数据库

全新数据库才需要按顺序执行：

1. `sql/schema.sql`
2. `sql/dashboard_rpc.sql`
3. `sql/v2_5_warehouse_patch.sql`

然后再运行：

```bash
python scripts/run_sync.py
python scripts/export_csv.py
```

## v2.5 导出文件说明

`scripts/export_csv.py` 会导出：

1. `daily_metrics_*.csv`：每日资产、现金比例、持仓比例、盈亏等汇总指标。
2. `daily_cash_flows_*.csv`：每日现金流，包含入金、出金、买入、卖出、分红、利息、费用，以及 raw 展开字段。
3. `account_summary_raw_*.csv`：账户概览标准字段 + 原始账户 summary 展开字段。
4. `latest_positions_*.csv`：最新持仓标准字段 + 原始 positions 展开字段。
5. `order_history_*.csv`：v2.5 新增，订单历史明细。
6. `raw_api_events_*.csv`：v2.5 新增，每个 API endpoint/page 的原始响应归档。
7. `sync_warnings_*.csv`：v2.5 新增，同步期间的异常或提示。
8. `warehouse_audit_*.json`：v2.5 新增，本次导出的数量、endpoint 覆盖、warning code 汇总。

## GitHub Actions

`.github/workflows/daily-sync.yml` 已包含 v2.5 变量：

```yaml
TRADING212_ORDERS_PATH: ${{ vars.TRADING212_ORDERS_PATH || '/api/v0/equity/history/orders' }}
SYNC_ORDERS: ${{ vars.SYNC_ORDERS || 'true' }}
ORDER_LOOKBACK_DAYS: ${{ vars.ORDER_LOOKBACK_DAYS || '7' }}
ORDER_MAX_PAGES: ${{ vars.ORDER_MAX_PAGES || '3' }}
ORDER_PAGE_DELAY_SECONDS: ${{ vars.ORDER_PAGE_DELAY_SECONDS || '15' }}
SYNC_RAW_API: ${{ vars.SYNC_RAW_API || 'true' }}
```

如果你已经配置过 GitHub Secrets，只需要确认新增 Variables 即可，不需要重新配置旧的 Secret。

## 安全边界

请继续遵守：

- 不要把 `.env` 提交到 Git。
- 不要上传 `dashboard/config.js`。
- 不要上传 `exports/`、`.venv/`、`__pycache__/`。
- `SUPABASE_SERVICE_ROLE_KEY` 只能放在本地 `.env`、GitHub Secrets、Secret Manager 或后端环境变量。
- `SUPABASE_SERVICE_ROLE_KEY` 绝对不能放在 `dashboard/app.js`、HTML、公开仓库、浏览器环境变量或前端构建产物。
- `TRADING212_API_KEY` 和 `TRADING212_API_SECRET` 不能放进前端。
- 前端 Dashboard 只能使用 Supabase anon key。

## Dashboard

Dashboard 仍然保留，但 v2.5 当前重点是数仓验证。Dashboard 继续读取 Supabase RPC：

- `get_latest_dashboard_summary()`
- `get_position_allocation()`
- `get_portfolio_timeseries(90)`

如果只做 v2.5 数仓验证，可以先不用管 Dashboard。
