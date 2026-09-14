create extension if not exists pgcrypto;

create table if not exists accounts (
  id uuid primary key default gen_random_uuid(),
  provider text not null default 'trading212',
  account_key text not null unique,
  account_name text not null,
  base_currency text not null,
  created_at timestamptz default now()
);

create table if not exists sync_runs (
  id uuid primary key default gen_random_uuid(),
  account_id uuid references accounts(id) on delete cascade,
  status text not null default 'running',
  records_inserted integer default 0,
  records_updated integer default 0,
  error_message text,
  started_at timestamptz default now(),
  finished_at timestamptz
);

create table if not exists account_snapshots (
  id uuid primary key default gen_random_uuid(),
  account_id uuid references accounts(id) on delete cascade,
  snapshot_date date not null,
  total_value numeric,
  cash_value numeric,
  invested_value numeric,
  pnl_total numeric,
  pnl_daily numeric,
  currency text,
  raw_payload jsonb,
  created_at timestamptz default now(),
  unique(account_id, snapshot_date)
);

create table if not exists position_snapshots (
  id uuid primary key default gen_random_uuid(),
  account_id uuid references accounts(id) on delete cascade,
  snapshot_date date not null,
  ticker text not null,
  instrument_name text,
  quantity numeric,
  average_price numeric,
  current_price numeric,
  market_value numeric,
  cost_basis numeric,
  unrealized_pnl numeric,
  unrealized_pnl_pct numeric,
  currency text,
  raw_payload jsonb,
  created_at timestamptz default now(),
  unique(account_id, snapshot_date, ticker)
);

create table if not exists transactions (
  id uuid primary key default gen_random_uuid(),
  account_id uuid references accounts(id) on delete cascade,
  provider_transaction_id text not null,
  transaction_time timestamptz not null,
  transaction_type text,
  ticker text,
  quantity numeric,
  price numeric,
  amount numeric,
  fee numeric,
  currency text,
  fx_rate numeric,
  raw_payload jsonb,
  created_at timestamptz default now(),
  unique(account_id, provider_transaction_id)
);

create table if not exists daily_cash_flows (
  id uuid primary key default gen_random_uuid(),
  flow_date date not null,
  account_id uuid references accounts(id) on delete cascade,
  opening_cash numeric,
  closing_cash numeric,
  cash_change numeric,
  deposit_amount numeric default 0,
  withdrawal_amount numeric default 0,
  buy_amount numeric default 0,
  sell_amount numeric default 0,
  dividend_amount numeric default 0,
  interest_amount numeric default 0,
  fee_amount numeric default 0,
  fx_fee_amount numeric default 0,
  net_contribution numeric default 0,
  net_trading_cash_flow numeric default 0,
  transaction_count integer default 0,
  raw_payload jsonb,
  created_at timestamptz default now(),
  unique(account_id, flow_date)
);

create table if not exists daily_metrics (
  id uuid primary key default gen_random_uuid(),
  metric_date date not null,
  account_id uuid references accounts(id) on delete cascade,
  total_value numeric,
  cash_ratio numeric,
  invested_ratio numeric,
  daily_change numeric,
  daily_change_pct numeric,
  monthly_change numeric,
  monthly_change_pct numeric,
  top_1_position_ratio numeric,
  top_5_position_ratio numeric,
  unrealized_pnl numeric,
  realized_pnl numeric,
  dividend_income numeric,
  created_at timestamptz default now(),
  unique(account_id, metric_date)
);

create index if not exists idx_sync_runs_account_started on sync_runs(account_id, started_at desc);
create index if not exists idx_account_snapshots_date on account_snapshots(snapshot_date);
create index if not exists idx_position_snapshots_date on position_snapshots(snapshot_date);
create index if not exists idx_position_snapshots_ticker on position_snapshots(ticker);
create index if not exists idx_transactions_time on transactions(transaction_time);
create index if not exists idx_transactions_ticker on transactions(ticker);
create index if not exists idx_daily_cash_flows_date on daily_cash_flows(flow_date);
create index if not exists idx_daily_metrics_date on daily_metrics(metric_date);

create or replace function get_latest_positions()
returns table (
  account_id uuid,
  account_key text,
  account_name text,
  snapshot_date date,
  ticker text,
  instrument_name text,
  quantity numeric,
  average_price numeric,
  current_price numeric,
  market_value numeric,
  cost_basis numeric,
  unrealized_pnl numeric,
  unrealized_pnl_pct numeric,
  currency text
)
language sql
as $$
  select
    p.account_id,
    a.account_key,
    a.account_name,
    p.snapshot_date,
    p.ticker,
    p.instrument_name,
    p.quantity,
    p.average_price,
    p.current_price,
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
  order by p.market_value desc nulls last;
$$;

create or replace function get_latest_dashboard_summary()
returns table (
  account_id uuid,
  account_key text,
  account_name text,
  snapshot_date date,
  total_value numeric,
  cash_value numeric,
  invested_value numeric,
  cash_ratio numeric,
  invested_ratio numeric,
  unrealized_pnl numeric,
  dividend_income numeric,
  deposit_amount numeric,
  withdrawal_amount numeric,
  buy_amount numeric,
  sell_amount numeric,
  transaction_count integer,
  base_currency text
)
language sql
as $$
  select
    a.id as account_id,
    a.account_key,
    a.account_name,
    s.snapshot_date,
    s.total_value,
    s.cash_value,
    s.invested_value,
    case when s.total_value is not null and s.total_value <> 0 then s.cash_value / s.total_value else null end as cash_ratio,
    case when s.total_value is not null and s.total_value <> 0 then s.invested_value / s.total_value else null end as invested_ratio,
    coalesce(m.unrealized_pnl, 0) as unrealized_pnl,
    coalesce(m.dividend_income, f.dividend_amount, 0) as dividend_income,
    coalesce(f.deposit_amount, 0) as deposit_amount,
    coalesce(f.withdrawal_amount, 0) as withdrawal_amount,
    coalesce(f.buy_amount, 0) as buy_amount,
    coalesce(f.sell_amount, 0) as sell_amount,
    coalesce(f.transaction_count, 0) as transaction_count,
    a.base_currency
  from accounts a
  join account_snapshots s on s.account_id = a.id
  left join daily_metrics m on m.account_id = a.id and m.metric_date = s.snapshot_date
  left join daily_cash_flows f on f.account_id = a.id and f.flow_date = s.snapshot_date
  where s.snapshot_date = (
    select max(s2.snapshot_date)
    from account_snapshots s2
    where s2.account_id = a.id
  )
  order by s.total_value desc nulls last;
$$;
