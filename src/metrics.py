from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

from src.supabase_store import SupabaseStore


def to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def safe_float(value: Optional[Decimal]) -> Optional[float]:
    return float(value) if value is not None else None


def pct_change(current: Optional[Decimal], previous: Optional[Decimal]) -> Optional[Decimal]:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous


def is_metric_position(position: Dict[str, Any]) -> bool:
    """Exclude known fallback rows from dashboard metrics.

    Older sync versions could store duplicate rows with ticker values like
    unknown_position_* before Trading 212 nested fields were parsed correctly.
    Keeping those rows in concentration metrics can make top position ratios
    meaningless, especially for UK GBX-quoted instruments.
    """
    ticker = str(position.get("ticker") or "")
    if ticker.startswith("unknown_position_"):
        return False
    market_value = to_decimal(position.get("market_value"))
    return market_value is not None and market_value >= 0


def calculate_and_store_daily_metrics(store: SupabaseStore, account_id: str, metric_date: date) -> Dict[str, Any]:
    snapshot = store.get_account_snapshot(account_id, metric_date)
    if not snapshot:
        raise RuntimeError(f"No account snapshot found for account={account_id}, date={metric_date}")

    positions = [
        position
        for position in store.get_position_snapshots(account_id, metric_date)
        if is_metric_position(position)
    ]
    previous = store.get_previous_account_snapshot(account_id, metric_date)
    month_start = store.get_month_start_snapshot(account_id, metric_date)

    total_value = to_decimal(snapshot.get("total_value"))
    cash_value = to_decimal(snapshot.get("cash_value"))
    invested_value = to_decimal(snapshot.get("invested_value"))
    previous_total = to_decimal(previous.get("total_value")) if previous else None
    month_start_total = to_decimal(month_start.get("total_value")) if month_start else None

    market_values = sorted(
        [to_decimal(position.get("market_value")) or Decimal("0") for position in positions],
        reverse=True,
    )
    top_1 = market_values[0] if market_values else Decimal("0")
    top_5 = sum(market_values[:5], Decimal("0"))

    unrealized_pnl = sum(
        (to_decimal(position.get("unrealized_pnl")) or Decimal("0"))
        for position in positions
    )

    cash_flow = store.get_daily_cash_flow(account_id, metric_date)
    dividend_income = (
        to_decimal(cash_flow.get("dividend_amount"))
        if cash_flow
        else Decimal("0")
    ) or Decimal("0")

    # Realized PnL requires a cost-basis method such as FIFO or average cost.
    # Until that is implemented, store a conservative zero instead of null so
    # null continues to mean a genuine data-quality gap rather than "no events".
    realized_pnl = Decimal("0")

    daily_change = (total_value - previous_total) if total_value is not None and previous_total is not None else None
    monthly_change = (total_value - month_start_total) if total_value is not None and month_start_total is not None else None

    metric = {
        "account_id": account_id,
        "metric_date": metric_date.isoformat(),
        "total_value": safe_float(total_value),
        "cash_ratio": safe_float(cash_value / total_value) if cash_value is not None and total_value not in (None, Decimal("0")) else None,
        "invested_ratio": safe_float(invested_value / total_value) if invested_value is not None and total_value not in (None, Decimal("0")) else None,
        "daily_change": safe_float(daily_change),
        "daily_change_pct": safe_float(pct_change(total_value, previous_total)),
        "monthly_change": safe_float(monthly_change),
        "monthly_change_pct": safe_float(pct_change(total_value, month_start_total)),
        "top_1_position_ratio": safe_float(top_1 / total_value) if total_value not in (None, Decimal("0")) else None,
        "top_5_position_ratio": safe_float(top_5 / total_value) if total_value not in (None, Decimal("0")) else None,
        "unrealized_pnl": safe_float(unrealized_pnl),
        "realized_pnl": safe_float(realized_pnl),
        "dividend_income": safe_float(dividend_income),
    }
    store.upsert_daily_metric(metric)
    return metric
