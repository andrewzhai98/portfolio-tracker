# SQL Files

建议按以下顺序在 Supabase SQL Editor 执行：

1. `schema.sql`：创建基础表和基础 Dashboard RPC。
2. `dashboard_rpc.sql`：创建趋势、持仓分布和 AI 报告上下文 RPC。
3. `cleanup_dirty_data.sql`：仅用于旧版本脏数据清理；优先使用 `scripts/cleanup_dirty_data.py`。

## 前端只读访问

Dashboard 只应该调用只读 RPC：

- `get_latest_dashboard_summary()`
- `get_latest_positions()`
- `get_portfolio_timeseries(p_days)`
- `get_cash_flow_timeseries(p_days)`
- `get_position_allocation()`
- `get_ai_report_context(p_days)`

不要把 `SUPABASE_SERVICE_ROLE_KEY` 放进浏览器代码。
