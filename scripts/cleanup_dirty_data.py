from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from src.config import load_settings
from src.metrics import calculate_and_store_daily_metrics
from src.supabase_store import SupabaseStore


def parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def unique_dates(rows: List[Dict[str, Any]]) -> List[Tuple[str, date]]:
    seen = set()
    result: List[Tuple[str, date]] = []
    for row in rows:
        account_id = str(row.get("account_id"))
        snapshot_date = parse_date(row.get("snapshot_date"))
        key = (account_id, snapshot_date.isoformat())
        if key in seen:
            continue
        seen.add(key)
        result.append((account_id, snapshot_date))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean fallback unknown_position_* rows and refresh affected daily metrics."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete dirty rows. Without this flag the script only prints a dry run.",
    )
    parser.add_argument(
        "--no-recalculate",
        action="store_true",
        help="Delete affected daily_metrics rows without recalculating them.",
    )
    args = parser.parse_args()

    settings = load_settings()
    store = SupabaseStore(settings)
    dirty_dates = store.get_unknown_position_snapshot_dates()
    affected_dates = unique_dates(dirty_dates)

    if not affected_dates:
        print("No unknown_position_* rows found. Nothing to clean.")
        return

    print(f"Found dirty position rows on {len(affected_dates)} account/date slice(s):")
    for account_id, snapshot_date in affected_dates:
        print(f"- account_id={account_id} snapshot_date={snapshot_date.isoformat()}")

    if not args.yes:
        print("Dry run only. Re-run with --yes to delete dirty rows and refresh metrics.")
        return

    deleted_positions = store.delete_unknown_position_snapshots()
    print(f"Deleted {deleted_positions} unknown_position_* row(s).")

    deleted_metrics = 0
    recalculated_metrics = 0
    for account_id, snapshot_date in affected_dates:
        deleted_metrics += store.delete_daily_metric(account_id, snapshot_date)
        if not args.no_recalculate:
            calculate_and_store_daily_metrics(store, account_id, snapshot_date)
            recalculated_metrics += 1

    print(f"Deleted {deleted_metrics} affected daily_metrics row(s).")
    if args.no_recalculate:
        print("Skipped metrics recalculation because --no-recalculate was provided.")
    else:
        print(f"Recalculated {recalculated_metrics} daily_metrics row(s).")


if __name__ == "__main__":
    main()
