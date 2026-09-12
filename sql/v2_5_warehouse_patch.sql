create extension if not exists pgcrypto;

create table if not exists raw_api_events (
  id uuid primary key default gen_random_uuid(),
  account_id uuid references accounts(id) on delete cascade,
  sync_run_id uuid references sync_runs(id) on delete cascade,
  endpoint_name text not null,
  endpoint_path text not null,
  page_number integer not null default 1,
  request_params jsonb default '{}'::jsonb,
  item_count integer default 0,
  raw_payload jsonb,
  captured_at timestamptz default now(),
  unique(account_id, sync_run_id, endpoint_name, page_number)
);

create table if not exists order_history (
  id uuid primary key default gen_random_uuid(),
  account_id uuid references accounts(id) on delete cascade,
  provider_order_id text not null,
  order_time timestamptz,
  order_type text,
  status text,
  ticker text,
  quantity numeric,
  filled_quantity numeric,
  limit_price numeric,
  stop_price numeric,
  average_price numeric,
  total_value numeric,
  currency text,
  raw_payload jsonb,
  created_at timestamptz default now(),
  unique(account_id, provider_order_id)
);

create table if not exists sync_warnings (
  id uuid primary key default gen_random_uuid(),
  account_id uuid references accounts(id) on delete cascade,
  sync_run_id uuid references sync_runs(id) on delete cascade,
  warning_code text not null,
  warning_message text not null,
  context jsonb default '{}'::jsonb,
  created_at timestamptz default now()
);

create index if not exists idx_raw_api_events_account_endpoint on raw_api_events(account_id, endpoint_name, captured_at desc);
create index if not exists idx_raw_api_events_sync_run on raw_api_events(sync_run_id);
create index if not exists idx_order_history_account_time on order_history(account_id, order_time desc);
create index if not exists idx_order_history_ticker on order_history(ticker);
create index if not exists idx_sync_warnings_account_created on sync_warnings(account_id, created_at desc);

create or replace function get_latest_order_history(p_limit integer default 100)
returns table (
  account_key text,
  account_name text,
  order_time timestamptz,
  order_type text,
  status text,
  ticker text,
  quantity numeric,
  filled_quantity numeric,
  average_price numeric,
  total_value numeric,
  currency text
)
language sql
security definer
set search_path = public
as $$
  select
    a.account_key,
    a.account_name,
    o.order_time,
    o.order_type,
    o.status,
    o.ticker,
    o.quantity,
    o.filled_quantity,
    o.average_price,
    o.total_value,
    o.currency
  from order_history o
  join accounts a on a.id = o.account_id
  order by o.order_time desc nulls last, o.created_at desc
  limit greatest(p_limit, 1);
$$;

create or replace function get_latest_sync_warnings(p_limit integer default 100)
returns table (
  account_key text,
  account_name text,
  warning_code text,
  warning_message text,
  context jsonb,
  created_at timestamptz
)
language sql
security definer
set search_path = public
as $$
  select
    a.account_key,
    a.account_name,
    w.warning_code,
    w.warning_message,
    w.context,
    w.created_at
  from sync_warnings w
  join accounts a on a.id = w.account_id
  order by w.created_at desc
  limit greatest(p_limit, 1);
$$;

grant execute on function get_latest_order_history(integer) to anon;
grant execute on function get_latest_sync_warnings(integer) to anon;
