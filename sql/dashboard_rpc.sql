-- Read-only Dashboard and AI report RPCs.
-- Run this in Supabase SQL Editor after sql/schema.sql.

create or replace function get_portfolio_timeseries(p_days integer default 90)
returns table (
  metric_date date,
  total_value numeric,
  cash_value numeric,
  invested_value numeric,
  cash_ratio numeric,
  invested_ratio numeric,
  unrealized_pnl numeric,
  dividend_income numeric,
  base_currency text
)
language sql
stable
as $$
  with daily as (
    select
      s.snapshot_date as metric_date,
      sum(coalesce(s.total_value, 0)) as total_value,
      sum(coalesce(s.cash_value, 0)) as cash_value,
      sum(coalesce(s.invested_value, 0)) as invested_value,
      sum(coalesce(m.unrealized_pnl, 0)) as unrealized_pnl,
      sum(coalesce(m.dividend_income, 0)) as dividend_income,
      min(a.base_currency) as base_currency
    from account_snapshots s
    join accounts a on a.id = s.account_id
    left join daily_metrics m on m.account_id = s.account_id and m.metric_date = s.snapshot_date
    where s.snapshot_date >= current_date - greatest(coalesce(p_days, 90), 1)
    group by s.snapshot_date
  )
  select
    metric_date,
    total_value,
    cash_value,
    invested_value,
    case when total_value <> 0 then cash_value / total_value else null end as cash_ratio,
    case when total_value <> 0 then invested_value / total_value else null end as invested_ratio,
    unrealized_pnl,
    dividend_income,
    base_currency
  from daily
  order by metric_date;
$$;

create or replace function get_cash_flow_timeseries(p_days integer default 90)
returns table (
  flow_date date,
  deposit_amount numeric,
  withdrawal_amount numeric,
  buy_amount numeric,
  sell_amount numeric,
  dividend_amount numeric,
  interest_amount numeric,
  fee_amount numeric,
  fx_fee_amount numeric,
  net_contribution numeric,
  net_trading_cash_flow numeric,
  transaction_count integer,
  base_currency text
)
language sql
stable
as $$
  select
    f.flow_date,
    sum(coalesce(f.deposit_amount, 0)) as deposit_amount,
    sum(coalesce(f.withdrawal_amount, 0)) as withdrawal_amount,
    sum(coalesce(f.buy_amount, 0)) as buy_amount,
    sum(coalesce(f.sell_amount, 0)) as sell_amount,
    sum(coalesce(f.dividend_amount, 0)) as dividend_amount,
    sum(coalesce(f.interest_amount, 0)) as interest_amount,
    sum(coalesce(f.fee_amount, 0)) as fee_amount,
    sum(coalesce(f.fx_fee_amount, 0)) as fx_fee_amount,
    sum(coalesce(f.net_contribution, 0)) as net_contribution,
    sum(coalesce(f.net_trading_cash_flow, 0)) as net_trading_cash_flow,
    sum(coalesce(f.transaction_count, 0))::integer as transaction_count,
    min(a.base_currency) as base_currency
  from daily_cash_flows f
  join accounts a on a.id = f.account_id
  where f.flow_date >= current_date - greatest(coalesce(p_days, 90), 1)
  group by f.flow_date
  order by f.flow_date;
$$;

create or replace function get_position_allocation()
returns table (
  account_id uuid,
  account_key text,
  account_name text,
  snapshot_date date,
  ticker text,
  instrument_name text,
  quantity numeric,
  market_value numeric,
  cost_basis numeric,
  unrealized_pnl numeric,
  unrealized_pnl_pct numeric,
  currency text,
  portfolio_weight numeric
)
language sql
stable
as $$
  with latest_positions as (
    select
      p.account_id,
      a.account_key,
      a.account_name,
      p.snapshot_date,
      p.ticker,
      p.instrument_name,
      p.quantity,
      p.market_value,
      p.cost_basis,
      p.unrealized_pnl,
      p.unrealized_pnl_pct,
      p.currency
    from position_snapshots p
    join accounts a on a.id = p.account_id
    where p.snapshot_date = (
      select max(p2.snapshot_date)
      from position_snapshots p2
      where p2.account_id = p.account_id
    )
    and p.ticker not like 'unknown_position_%'
  ), total as (
    select sum(coalesce(market_value, 0)) as total_market_value
    from latest_positions
  )
  select
    lp.account_id,
    lp.account_key,
    lp.account_name,
    lp.snapshot_date,
    lp.ticker,
    lp.instrument_name,
    lp.quantity,
    lp.market_value,
    lp.cost_basis,
    lp.unrealized_pnl,
    lp.unrealized_pnl_pct,
    lp.currency,
    case when total.total_market_value <> 0 then lp.market_value / total.total_market_value else null end as portfolio_weight
  from latest_positions lp
  cross join total
  order by lp.market_value desc nulls last;
$$;

create or replace function get_ai_report_context(p_days integer default 90)
returns jsonb
language sql
stable
as $$
  with latest_summary as (
    select * from get_latest_dashboard_summary()
  ), portfolio as (
    select jsonb_build_object(
      'as_of_date', max(snapshot_date),
      'total_value', sum(coalesce(total_value, 0)),
      'cash_value', sum(coalesce(cash_value, 0)),
      'invested_value', sum(coalesce(invested_value, 0)),
      'cash_ratio', case when sum(coalesce(total_value, 0)) <> 0 then sum(coalesce(cash_value, 0)) / sum(coalesce(total_value, 0)) else null end,
      'invested_ratio', case when sum(coalesce(total_value, 0)) <> 0 then sum(coalesce(invested_value, 0)) / sum(coalesce(total_value, 0)) else null end,
      'unrealized_pnl', sum(coalesce(unrealized_pnl, 0)),
      'base_currency', min(base_currency)
    ) as data
    from latest_summary
  ), accounts_json as (
    select coalesce(jsonb_agg(to_jsonb(latest_summary) order by total_value desc nulls last), '[]'::jsonb) as data
    from latest_summary
  ), positions_json as (
    select coalesce(jsonb_agg(to_jsonb(p) order by market_value desc nulls last), '[]'::jsonb) as data
    from (
      select * from get_position_allocation() limit 25
    ) p
  ), trend_json as (
    select coalesce(jsonb_agg(to_jsonb(t) order by metric_date), '[]'::jsonb) as data
    from get_portfolio_timeseries(p_days) t
  ), cash_flow_json as (
    select coalesce(jsonb_agg(to_jsonb(f) order by flow_date), '[]'::jsonb) as data
    from get_cash_flow_timeseries(p_days) f
  ), quality as (
    select jsonb_build_object(
      'unknown_position_count', (
        select count(*) from position_snapshots where ticker like 'unknown_position_%'
      ),
      'latest_sync', (
        select jsonb_build_object(
          'status', status,
          'started_at', started_at,
          'finished_at', finished_at,
          'error_message', error_message
        )
        from sync_runs
        order by started_at desc
        limit 1
      ),
      'report_window_days', greatest(coalesce(p_days, 90), 1)
    ) as data
  )
  select jsonb_build_object(
    'portfolio', portfolio.data,
    'accounts', accounts_json.data,
    'top_positions', positions_json.data,
    'portfolio_timeseries', trend_json.data,
    'cash_flow_timeseries', cash_flow_json.data,
    'data_quality', quality.data
  )
  from portfolio, accounts_json, positions_json, trend_json, cash_flow_json, quality;
$$;
