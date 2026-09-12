from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.config import Settings
from src.supabase_store import SupabaseStore
from src.sync import build_order_history


def flatten_json(value: Any, prefix: str = "raw") -> Dict[str, Any]:
    flattened: Dict[str, Any] = {}

    def walk(current: Any, current_key: str) -> None:
        if isinstance(current, dict):
            if not current:
                flattened[current_key] = "{}"
            for key, nested_value in current.items():
                safe_key = str(key).replace(".", "_").replace(" ", "_")
                walk(nested_value, f"{current_key}_{safe_key}")
        elif isinstance(current, list):
            flattened[current_key] = json.dumps(current, ensure_ascii=False)
        else:
            flattened[current_key] = current

    walk(value, prefix)
    return flattened


def collect_fieldnames(rows: Iterable[Dict[str, Any]], preferred: List[str]) -> List[str]:
    seen = set(preferred)
    fieldnames = list(preferred)
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    return fieldnames


def write_dynamic_csv(path: Path, rows: List[Dict[str, Any]], preferred: List[str]) -> None:
    fieldnames = collect_fieldnames(rows, preferred)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def first_non_blank(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def normalized_order_from_raw(row: Dict[str, Any]) -> Dict[str, Any]:
    raw_payload = row.get("raw_payload")
    if not isinstance(raw_payload, dict):
        return {}

    account_id = str(row.get("account_id") or "")
    fallback_currency = (
        row.get("currency")
        or (row.get("accounts") or {}).get("base_currency")
        or "GBP"
    )
    return build_order_history(account_id, raw_payload, str(fallback_currency))


def account_columns(row: Dict[str, Any]) -> Dict[str, Any]:
    account = row.get("accounts") or {}
    return {
        "account_key": account.get("account_key"),
        "account_name": account.get("account_name"),
        "base_currency": account.get("base_currency"),
    }


def export_current_data(settings: Settings) -> List[str]:
    export_dir = Path(settings.export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)

    store = SupabaseStore(settings)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    paths: List[str] = []

    metrics = store.get_recent_daily_metrics(limit=200)
    metrics_path = export_dir / f"daily_metrics_{timestamp}.csv"
    with metrics_path.open("w", newline="", encoding="utf-8") as file:
        fieldnames = [
            "metric_date",
            "account_key",
            "account_name",
            "base_currency",
            "total_value",
            "cash_ratio",
            "invested_ratio",
            "daily_change",
            "daily_change_pct",
            "monthly_change",
            "monthly_change_pct",
            "top_1_position_ratio",
            "top_5_position_ratio",
            "unrealized_pnl",
            "realized_pnl",
            "dividend_income",
        ]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in metrics:
            writer.writerow({
                "metric_date": row.get("metric_date"),
                **account_columns(row),
                "total_value": row.get("total_value"),
                "cash_ratio": row.get("cash_ratio"),
                "invested_ratio": row.get("invested_ratio"),
                "daily_change": row.get("daily_change"),
                "daily_change_pct": row.get("daily_change_pct"),
                "monthly_change": row.get("monthly_change"),
                "monthly_change_pct": row.get("monthly_change_pct"),
                "top_1_position_ratio": row.get("top_1_position_ratio"),
                "top_5_position_ratio": row.get("top_5_position_ratio"),
                "unrealized_pnl": row.get("unrealized_pnl"),
                "realized_pnl": row.get("realized_pnl"),
                "dividend_income": row.get("dividend_income"),
            })
    paths.append(str(metrics_path))

    cash_flows = store.get_recent_daily_cash_flows(limit=200)
    cash_flow_rows: List[Dict[str, Any]] = []
    for row in cash_flows:
        raw_payload = row.get("raw_payload") or {}
        output_row = {
            "flow_date": row.get("flow_date"),
            **account_columns(row),
            "opening_cash": row.get("opening_cash"),
            "closing_cash": row.get("closing_cash"),
            "cash_change": row.get("cash_change"),
            "deposit_amount": row.get("deposit_amount"),
            "withdrawal_amount": row.get("withdrawal_amount"),
            "buy_amount": row.get("buy_amount"),
            "sell_amount": row.get("sell_amount"),
            "dividend_amount": row.get("dividend_amount"),
            "interest_amount": row.get("interest_amount"),
            "fee_amount": row.get("fee_amount"),
            "fx_fee_amount": row.get("fx_fee_amount"),
            "net_contribution": row.get("net_contribution"),
            "net_trading_cash_flow": row.get("net_trading_cash_flow"),
            "transaction_count": row.get("transaction_count"),
        }
        if isinstance(raw_payload, dict):
            output_row.update(flatten_json(raw_payload, "raw"))
        else:
            output_row["raw_payload"] = json.dumps(raw_payload, ensure_ascii=False)
        cash_flow_rows.append(output_row)

    cash_flows_path = export_dir / f"daily_cash_flows_{timestamp}.csv"
    write_dynamic_csv(cash_flows_path, cash_flow_rows, [
        "flow_date", "account_key", "account_name", "base_currency", "opening_cash", "closing_cash",
        "cash_change", "deposit_amount", "withdrawal_amount", "buy_amount", "sell_amount",
        "dividend_amount", "interest_amount", "fee_amount", "fx_fee_amount", "net_contribution",
        "net_trading_cash_flow", "transaction_count", "raw_raw_order_count", "raw_buy_amount_source",
    ])
    paths.append(str(cash_flows_path))

    account_snapshots = store.get_recent_account_snapshots(limit=200)
    account_summary_rows: List[Dict[str, Any]] = []
    for row in account_snapshots:
        raw_payload = row.get("raw_payload") or {}
        output_row = {
            **account_columns(row),
            "snapshot_date": row.get("snapshot_date"),
            "total_value": row.get("total_value"),
            "cash_value": row.get("cash_value"),
            "invested_value": row.get("invested_value"),
            "pnl_total": row.get("pnl_total"),
            "pnl_daily": row.get("pnl_daily"),
            "currency": row.get("currency"),
        }
        if isinstance(raw_payload, dict):
            output_row.update(flatten_json(raw_payload, "raw"))
        else:
            output_row["raw_payload"] = json.dumps(raw_payload, ensure_ascii=False)
        account_summary_rows.append(output_row)

    account_summary_path = export_dir / f"account_summary_raw_{timestamp}.csv"
    write_dynamic_csv(account_summary_path, account_summary_rows, [
        "account_key", "account_name", "base_currency", "snapshot_date", "total_value", "cash_value",
        "invested_value", "pnl_total", "pnl_daily", "currency",
    ])
    paths.append(str(account_summary_path))

    positions = store.get_latest_position_snapshots()
    position_rows: List[Dict[str, Any]] = []
    for row in positions:
        raw_payload = row.get("raw_payload") or {}
        output_row = {
            **account_columns(row),
            "snapshot_date": row.get("snapshot_date"),
            "ticker": row.get("ticker"),
            "instrument_name": row.get("instrument_name"),
            "quantity": row.get("quantity"),
            "average_price": row.get("average_price"),
            "current_price": row.get("current_price"),
            "market_value": row.get("market_value"),
            "cost_basis": row.get("cost_basis"),
            "unrealized_pnl": row.get("unrealized_pnl"),
            "unrealized_pnl_pct": row.get("unrealized_pnl_pct"),
            "currency": row.get("currency"),
        }
        if isinstance(raw_payload, dict):
            output_row.update(flatten_json(raw_payload, "raw"))
        else:
            output_row["raw_payload"] = json.dumps(raw_payload, ensure_ascii=False)
        position_rows.append(output_row)

    positions_path = export_dir / f"latest_positions_{timestamp}.csv"
    write_dynamic_csv(positions_path, position_rows, [
        "account_key", "account_name", "base_currency", "snapshot_date", "ticker", "instrument_name",
        "quantity", "average_price", "current_price", "market_value", "cost_basis", "unrealized_pnl",
        "unrealized_pnl_pct", "currency",
    ])
    paths.append(str(positions_path))

    orders = store.get_recent_order_history(limit=1000)
    order_rows: List[Dict[str, Any]] = []
    for row in orders:
        raw_payload = row.get("raw_payload") or {}
        normalized = normalized_order_from_raw(row)
        output_row = {
            **account_columns(row),
            "provider_order_id": first_non_blank(normalized.get("provider_order_id"), row.get("provider_order_id")),
            "legacy_provider_order_id": row.get("provider_order_id"),
            "order_time": first_non_blank(row.get("order_time"), normalized.get("order_time")),
            "order_type": first_non_blank(row.get("order_type"), normalized.get("order_type")),
            "status": first_non_blank(row.get("status"), normalized.get("status")),
            "ticker": first_non_blank(row.get("ticker"), normalized.get("ticker")),
            "quantity": first_non_blank(row.get("quantity"), normalized.get("quantity")),
            "filled_quantity": first_non_blank(row.get("filled_quantity"), normalized.get("filled_quantity")),
            "limit_price": first_non_blank(row.get("limit_price"), normalized.get("limit_price")),
            "stop_price": first_non_blank(row.get("stop_price"), normalized.get("stop_price")),
            "average_price": first_non_blank(row.get("average_price"), normalized.get("average_price")),
            "total_value": first_non_blank(row.get("total_value"), normalized.get("total_value")),
            "currency": first_non_blank(row.get("currency"), normalized.get("currency")),
        }
        if isinstance(raw_payload, dict):
            output_row.update(flatten_json(raw_payload, "raw"))
        else:
            output_row["raw_payload"] = json.dumps(raw_payload, ensure_ascii=False)
        order_rows.append(output_row)

    orders_path = export_dir / f"order_history_{timestamp}.csv"
    write_dynamic_csv(orders_path, order_rows, [
        "account_key", "account_name", "base_currency", "provider_order_id", "legacy_provider_order_id", "order_time", "order_type",
        "status", "ticker", "quantity", "filled_quantity", "average_price", "total_value", "currency",
    ])
    paths.append(str(orders_path))

    raw_api_events = store.get_recent_raw_api_events(limit=500)
    raw_event_rows: List[Dict[str, Any]] = []
    for row in raw_api_events:
        raw_event_rows.append({
            **account_columns(row),
            "endpoint_name": row.get("endpoint_name"),
            "endpoint_path": row.get("endpoint_path"),
            "page_number": row.get("page_number"),
            "request_params": json.dumps(row.get("request_params") or {}, ensure_ascii=False),
            "item_count": row.get("item_count"),
            "captured_at": row.get("captured_at"),
            "raw_payload_json": json.dumps(row.get("raw_payload"), ensure_ascii=False),
        })
    raw_events_path = export_dir / f"raw_api_events_{timestamp}.csv"
    write_dynamic_csv(raw_events_path, raw_event_rows, [
        "account_key", "account_name", "base_currency", "endpoint_name", "endpoint_path", "page_number",
        "request_params", "item_count", "captured_at", "raw_payload_json",
    ])
    paths.append(str(raw_events_path))

    warnings = store.get_recent_sync_warnings(limit=500)
    warning_rows = [
        {
            **account_columns(row),
            "warning_code": row.get("warning_code"),
            "warning_message": row.get("warning_message"),
            "context": json.dumps(row.get("context") or {}, ensure_ascii=False),
            "created_at": row.get("created_at"),
        }
        for row in warnings
    ]
    warnings_path = export_dir / f"sync_warnings_{timestamp}.csv"
    write_dynamic_csv(warnings_path, warning_rows, [
        "account_key", "account_name", "base_currency", "warning_code", "warning_message", "context", "created_at",
    ])
    paths.append(str(warnings_path))

    audit = {
        "generated_at": timestamp,
        "file_purpose": "Send these export files back for v2.5 warehouse validation.",
        "counts": {
            "daily_metrics": len(metrics),
            "daily_cash_flows": len(cash_flows),
            "account_snapshots": len(account_snapshots),
            "latest_positions": len(positions),
            "order_history": len(orders),
            "raw_api_events": len(raw_api_events),
            "sync_warnings": len(warnings),
        },
        "raw_api_endpoint_counts": {},
        "warning_codes": {},
        "notes": [
            "raw_api_events tells us whether Trading 212 returned an endpoint/page at all.",
            "order_history is the first place to inspect Auto Pie / payout-to-invest execution detail.",
            "daily_cash_flows raw_* columns show whether buy/sell came from orders or transactions.",
        ],
    }
    for row in raw_api_events:
        endpoint = str(row.get("endpoint_name") or "unknown")
        audit["raw_api_endpoint_counts"][endpoint] = audit["raw_api_endpoint_counts"].get(endpoint, 0) + 1
    for row in warnings:
        code = str(row.get("warning_code") or "unknown")
        audit["warning_codes"][code] = audit["warning_codes"].get(code, 0) + 1

    audit_path = export_dir / f"warehouse_audit_{timestamp}.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.append(str(audit_path))

    return paths
