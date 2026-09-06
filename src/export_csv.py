from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.config import Settings
from src.supabase_store import SupabaseStore


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
            account = row.get("accounts") or {}
            writer.writerow({
                "metric_date": row.get("metric_date"),
                "account_key": account.get("account_key"),
                "account_name": account.get("account_name"),
                "base_currency": account.get("base_currency"),
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
        account = row.get("accounts") or {}
        raw_payload = row.get("raw_payload") or {}
        output_row = {
            "flow_date": row.get("flow_date"),
            "account_key": account.get("account_key"),
            "account_name": account.get("account_name"),
            "base_currency": account.get("base_currency"),
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
    write_dynamic_csv(
        cash_flows_path,
        cash_flow_rows,
        [
            "flow_date",
            "account_key",
            "account_name",
            "base_currency",
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
            "transaction_count",
        ],
    )
    paths.append(str(cash_flows_path))

    account_snapshots = store.get_recent_account_snapshots(limit=200)
    account_summary_rows: List[Dict[str, Any]] = []
    for row in account_snapshots:
        account = row.get("accounts") or {}
        raw_payload = row.get("raw_payload") or {}
        output_row = {
            "account_key": account.get("account_key"),
            "account_name": account.get("account_name"),
            "base_currency": account.get("base_currency"),
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
    write_dynamic_csv(
        account_summary_path,
        account_summary_rows,
        [
            "account_key",
            "account_name",
            "base_currency",
            "snapshot_date",
            "total_value",
            "cash_value",
            "invested_value",
            "pnl_total",
            "pnl_daily",
            "currency",
        ],
    )
    paths.append(str(account_summary_path))

    positions = store.get_latest_position_snapshots()
    position_rows: List[Dict[str, Any]] = []
    for row in positions:
        account = row.get("accounts") or {}
        raw_payload = row.get("raw_payload") or {}
        output_row = {
            "account_key": account.get("account_key"),
            "account_name": account.get("account_name"),
            "base_currency": account.get("base_currency"),
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
    write_dynamic_csv(
        positions_path,
        position_rows,
        [
            "account_key",
            "account_name",
            "base_currency",
            "snapshot_date",
            "ticker",
            "instrument_name",
            "quantity",
            "average_price",
            "current_price",
            "market_value",
            "cost_basis",
            "unrealized_pnl",
            "unrealized_pnl_pct",
            "currency",
        ],
    )
    paths.append(str(positions_path))

    return paths
