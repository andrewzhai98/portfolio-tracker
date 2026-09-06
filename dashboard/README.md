# Supabase Dashboard

这个 Dashboard 直接读取 Supabase 只读 RPC，不再依赖本地 CSV。

## 1. 先执行 SQL

在 Supabase SQL Editor 中按顺序执行：

1. `sql/schema.sql`
2. `sql/dashboard_rpc.sql`

`dashboard_rpc.sql` 会新增：

- `get_portfolio_timeseries(p_days integer)`
- `get_cash_flow_timeseries(p_days integer)`
- `get_position_allocation()`
- `get_ai_report_context(p_days integer)`

## 2. 配置前端只读 key

复制配置文件：

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

注意：这里只能填 Supabase anon key，不能填 service role key。

## 3. 打开 Dashboard

可以直接双击打开：

```text
dashboard/index.html
```

如果浏览器或 CORS 策略导致本地文件模式不可用，可以用任意静态文件预览工具打开，但不要把 service role key 放进前端。

## 4. 日常自动同步

正式数据流：

```text
Trading 212 API → GitHub Actions → scripts/run_sync.py → Supabase → Dashboard
```

每天自动同步只需要运行：

```bash
python scripts/run_sync.py
```

CSV 导出是可选项：

```bash
python scripts/export_csv.py
```

## 5. AI 报告读取方式

后续 AI 或自动报告应优先读取：

```text
get_ai_report_context(p_days)
```

它会返回整理后的 JSON，包含组合摘要、账户拆分、Top holdings、资产趋势、现金流趋势和数据质量提示。这样不需要让 AI 直接理解所有原始表。
