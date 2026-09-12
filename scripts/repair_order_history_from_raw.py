from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from src.config import load_settings
from src.supabase_store import SupabaseStore
from src.sync import build_order_history


def is_blank(value: Any) -> bool:
    return value in (None, "")


def is_incomplete_order(row: Dict[str, Any]) -> bool:
    required_fields = [
        "order_time",
        "order_type",
        "status",
        "ticker",
        "filled_quantity",
        "average_price",
        "total_value",
    ]
    return any(is_blank(row.get(field)) for field in required_fields)


def normalize_existing_row(row: Dict[str, Any]) -> Dict[str, Any] | None:
    raw_payload = row.get("raw_payload")
    if not isinstance(raw_payload, dict):
        return None

    account = row.get("accounts") or {}
    fallback_currency = row.get("currency") or account.get("base_currency") or "GBP"
    account_id = str(row.get("account_id") or "")
    if not account_id:
        return None

    normalized = build_order_history(account_id, raw_payload, str(fallback_currency))
    normalized["raw_payload"] = raw_payload
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repair order_history rows by re-parsing their stored raw_payload JSON."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write repaired rows to Supabase. Without this flag the script only prints a dry run.",
    )
    parser.add_argument(
        "--delete-stale",
        action="store_true",
        help="After inserting repaired fill-level rows, delete stale incomplete provider_order_id=order_* rows.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum order_history rows to scan. Default: 5000.",
    )
    args = parser.parse_args()

    settings = load_settings()
    store = SupabaseStore(settings)

    result = (
        store.client.table("order_history")
        .select("*, accounts(account_key, account_name, base_currency)")
        .limit(args.limit)
        .execute()
    )
    rows = result.data or []

    repaired_rows: List[Dict[str, Any]] = []
    stale_row_ids: List[str] = []
    skipped_without_raw = 0
    already_ok = 0

    for row in rows:
        if not is_incomplete_order(row):
            already_ok += 1
            continue

        normalized = normalize_existing_row(row)
        if normalized is None:
            skipped_without_raw += 1
            continue

        repaired_rows.append(normalized)

        legacy_id = str(row.get("provider_order_id") or "")
        normalized_id = str(normalized.get("provider_order_id") or "")
        row_id = row.get("id")
        if legacy_id.startswith("order_") and normalized_id and normalized_id != legacy_id and row_id:
            stale_row_ids.append(str(row_id))

    print(f"Scanned {len(rows)} order_history row(s).")
    print(f"Already complete: {already_ok}")
    print(f"Repairable from raw_payload: {len(repaired_rows)}")
    print(f"Skipped without usable raw_payload: {skipped_without_raw}")
    print(f"Stale incomplete order_* row(s) eligible for deletion: {len(stale_row_ids)}")

    if repaired_rows[:5]:
        print("Sample repaired rows:")
        for row in repaired_rows[:5]:
            print(
                "- "
                f"provider_order_id={row.get('provider_order_id')} "
                f"order_time={row.get('order_time')} "
                f"order_type={row.get('order_type')} "
                f"status={row.get('status')} "
                f"ticker={row.get('ticker')} "
                f"filled_quantity={row.get('filled_quantity')} "
                f"average_price={row.get('average_price')} "
                f"total_value={row.get('total_value')} "
                f"currency={row.get('currency')}"
            )

    if not args.apply:
        print("Dry run only. Re-run with --apply to upsert repaired rows.")
        return

    if repaired_rows:
        store.upsert_order_history(repaired_rows)
        print(f"Upserted {len(repaired_rows)} repaired order_history row(s).")

    if args.delete_stale and stale_row_ids:
        deleted = 0
        for row_id in stale_row_ids:
            response = store.client.table("order_history").delete().eq("id", row_id).execute()
            deleted += len(response.data or [])
        print(f"Deleted {deleted} stale incomplete order_* row(s).")
    elif stale_row_ids:
        print("Stale rows were not deleted. Add --delete-stale after checking the dry-run sample.")


if __name__ == "__main__":
    main()
