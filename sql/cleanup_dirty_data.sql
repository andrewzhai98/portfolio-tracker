-- Optional one-off cleanup for older sync versions.
-- This removes fallback rows created before Trading 212 nested position fields were parsed correctly.
-- Prefer running scripts/cleanup_dirty_data.py because it also refreshes affected daily_metrics.

delete from position_snapshots
where ticker like 'unknown_position_%';

delete from daily_metrics dm
where exists (
  select 1
  from account_snapshots s
  where s.account_id = dm.account_id
    and s.snapshot_date = dm.metric_date
);

-- After running this SQL-only cleanup, run:
--   python scripts/run_sync.py
--   python scripts/export_csv.py
-- so the latest day is rebuilt with clean positions and metrics.
