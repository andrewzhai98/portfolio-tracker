from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from src.config import load_settings
from src.supabase_store import SupabaseStore
from src.sync import classify_order, normalize_text, parse_datetime, to_float_or_none


NUMERIC_FIELDS = (
    "opening_cash",
    "closing_cash",
    "cash_change",
    "deposit_amount",
    "withdrawal_amount",
    "buy_amount",
    "sell_amount",
    "dividend_amount",
    "interest_amount",
    "fee_amount",
    "fx_fee_amount",
    "net_contribution",
    "net_trading_cash_flow",
)


def parse_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    parsed = parse_datetime(value)
    if parsed:
        return parsed.astimezone(timezone.utc).date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def money(value: Any) -> float:
    parsed = to_float_or_none(value)
    return float(parsed) if parsed is not None else 0.0


def is_filled_order(row: Dict[str, Any]) -> bool:
    status = normalize_text(row.get("status"))
    total_value = money(row.get("total_value"))
    if status in {"cancelled", "canceled", "rejected", "failed"}:
        return False
    return status in {"filled", "executed", "completed"} or total_value > 0


def row_order_class(row: Dict[str, Any]) -> str:
    raw_payload = as_dict(row.get("raw_payload"))
    if raw_payload:
        order_class = classify_order(raw_payload)
        if order_class != "unknown":
            return order_class

    order_type = normalize_text(row.get("order_type"))
    if order_type == "buy" or "buy" in order_type:
        return "buy"
    if order_type == "sell" or "sell" in order_type:
        return "sell"
    return order_type or "unknown"


def compact_order(row: Dict[str, Any], order_class: str) -> Dict[str, Any]:
    return {
        "provider_order_id": row.get("provider_order_id"),
        "order_time": row.get("order_time"),
        "order_type": row.get("order_type"),
        "status": row.get("status"),
        "ticker": row.get("ticker"),
        "filled_quantity": row.get("filled_quantity"),
        "average_price": row.get("average_price"),
        "total_value": row.get("total_value"),
        "currency": row.get("currency"),
        "order_class": order_class,
    }


def fetch_cash_flows(store: SupabaseStore, limit: int) -> List[Dict[str, Any]]:
    result = (
        store.client.table("daily_cash_flows")
        .select("*, accounts(account_key, account_name, base_currency)")
        .order("flow_date", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


def fetch_orders(store: SupabaseStore, limit: int) -> List[Dict[str, Any]]:
    result = (
        store.client.table("order_history")
        .select("*, accounts(account_key, account_name, base_currency)")
        .order("order_time", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


def build_order_groups(orders: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, date], Dict[str, Any]]:
    groups: Dict[Tuple[str, date], Dict[str, Any]] = defaultdict(lambda: {
        "buy_amount": 0.0,
        "sell_amount": 0.0,
        "order_count": 0,
        "orders": [],
        "order_classes": [],
    })

    for row in orders:
        account_id = str(row.get("account_id") or "")
        order_date = parse_date(row.get("order_time"))
        if not account_id or order_date is None:
            continue
        if not is_filled_order(row):
            continue

        order_class = row_order_class(row)
        amount = abs(money(row.get("total_value")))
        if amount == 0:
            continue

        group = groups[(account_id, order_date)]
        if order_class == "buy":
            group["buy_amount"] += amount
        elif order_class == "sell":
            group["sell_amount"] += amount
        else:
            continue

        group["order_count"] += 1
        group["orders"].append(compact_order(row, order_class))
        group["order_classes"].append(order_class)

    return groups


def build_repaired_cash_flow(existing: Optional[Dict[str, Any]], account_id: str, flow_date: date, group: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(existing or {})
    raw_payload = as_dict(row.get("raw_payload"))

    payload: Dict[str, Any] = {
        "account_id": account_id,
        "flow_date": flow_date.isoformat(),
    }

    for field in NUMERIC_FIELDS:
        payload[field] = money(row.get(field))

    payload["buy_amount"] = round(float(group["buy_amount"]), 8)
    payload["sell_amount"] = round(float(group["sell_amount"]), 8)
    payload["net_contribution"] = payload["deposit_amount"] - payload["withdrawal_amount"]
    payload["net_trading_cash_flow"] = (
        payload["sell_amount"]
        + payload["dividend_amount"]
        + payload["interest_amount"]
        - payload["buy_amount"]
        - payload["fee_amount"]
        - payload["fx_fee_amount"]
    )

    original_transaction_count = int(row.get("transaction_count") or 0)
    previous_order_count = int(raw_payload.get("order_history_order_count") or raw_payload.get("raw_order_count") or 0)
    transaction_count_without_old_orders = max(original_transaction_count - previous_order_count, 0)
    payload["transaction_count"] = transaction_count_without_old_orders + int(group["order_count"])

    raw_payload.update({
        "orders": group["orders"],
        "order_classes": group["order_classes"],
        "order_history_order_count": int(group["order_count"]),
        "raw_order_count": int(group["order_count"]),
        "excluded_order_count": 0,
        "buy_amount_source": "order_history",
        "cash_flow_repaired_from_order_history": True,
    })
    payload["raw_payload"] = raw_payload
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repair daily_cash_flows buy/sell amounts from normalized order_history rows."
    )
    parser.add_argument("--apply", action="store_true", help="Write repaired daily_cash_flows rows to Supabase.")
    parser.add_argument("--cash-flow-limit", type=int, default=5000, help="Maximum daily_cash_flows rows to scan.")
    parser.add_argument("--order-limit", type=int, default=10000, help="Maximum order_history rows to scan.")
    parser.add_argument("--from-date", help="Only repair flow dates on or after this YYYY-MM-DD date.")
    parser.add_argument("--to-date", help="Only repair flow dates on or before this YYYY-MM-DD date.")
    args = parser.parse_args()

    from_date = parse_date(args.from_date) if args.from_date else None
    to_date = parse_date(args.to_date) if args.to_date else None

    settings = load_settings()
    store = SupabaseStore(settings)

    cash_flows = fetch_cash_flows(store, args.cash_flow_limit)
    orders = fetch_orders(store, args.order_limit)
    order_groups = build_order_groups(orders)

    existing_by_key: Dict[Tuple[str, date], Dict[str, Any]] = {}
    for row in cash_flows:
        account_id = str(row.get("account_id") or "")
        flow_date = parse_date(row.get("flow_date"))
        if account_id and flow_date:
            existing_by_key[(account_id, flow_date)] = row

    repaired_rows: List[Dict[str, Any]] = []
    skipped_without_existing_cash_flow = 0

    for (account_id, flow_date), group in sorted(order_groups.items(), key=lambda item: (item[0][1], item[0][0])):
        if from_date and flow_date < from_date:
            continue
        if to_date and flow_date > to_date:
            continue

        existing = existing_by_key.get((account_id, flow_date))
        if existing is None:
            skipped_without_existing_cash_flow += 1
            continue

        repaired_rows.append(build_repaired_cash_flow(existing, account_id, flow_date, group))

    print(f"Scanned {len(cash_flows)} daily_cash_flows row(s).")
    print(f"Scanned {len(orders)} order_history row(s).")
    print(f"Order date/account groups with filled buy/sell activity: {len(order_groups)}")
    print(f"Repairable daily_cash_flows row(s): {len(repaired_rows)}")
    print(f"Skipped group(s) without existing daily_cash_flows row: {skipped_without_existing_cash_flow}")

    if repaired_rows:
        print("Sample repaired rows:")
        for row in repaired_rows[:10]:
            print(
                "- "
                f"flow_date={row.get('flow_date')} "
                f"account_id={row.get('account_id')} "
                f"buy_amount={row.get('buy_amount')} "
                f"sell_amount={row.get('sell_amount')} "
                f"transaction_count={row.get('transaction_count')} "
                f"source={(row.get('raw_payload') or {}).get('buy_amount_source')}"
            )

    if not args.apply:
        print("Dry run only. Re-run with --apply to upsert repaired daily_cash_flows rows.")
        return

    for row in repaired_rows:
        store.upsert_daily_cash_flow(row)
    print(f"Upserted {len(repaired_rows)} repaired daily_cash_flows row(s).")


if __name__ == "__main__":
    main()
