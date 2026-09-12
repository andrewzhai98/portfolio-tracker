from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from src.config import AccountConfig, Settings
from src.metrics import calculate_and_store_daily_metrics
from src.supabase_store import SupabaseStore
from src.trading212_client import Trading212Client


POSITION_TICKER_PATHS = (
    "instrument.ticker",
    "ticker",
    "raw_instrument_ticker",
    "instrumentCode",
    "shortName",
    "isin",
    "instrument.isin",
    "raw_instrument_isin",
    "instrumentId",
    "id",
    "instrument.name",
    "name",
)

TRANSACTION_TIME_PATHS = (
    "dateTime",
    "date_time",
    "date",
    "time",
    "created",
    "timestamp",
    "transactionTime",
    "eventTime",
)

ORDER_ID_PATHS = (
    "fill.id",
    "order.id",
    "id",
    "orderId",
    "reference",
    "executionId",
    "fillId",
    "historyId",
)

ORDER_TIME_PATHS = (
    "fill.filledAt",
    "order.filledAt",
    "order.createdAt",
    "dateTime",
    "created",
    "createdAt",
    "filledAt",
    "executedAt",
    "lastModified",
    "timestamp",
    "time",
)

ORDER_SIDE_PATHS = ("order.side", "side", "action", "kind")
ORDER_TYPE_PATHS = ("order.type", "type", "orderType")
ORDER_STATUS_PATHS = ("order.status", "status", "state")
ORDER_TICKER_PATHS = (
    "order.instrument.ticker",
    "order.ticker",
    "instrument.ticker",
    "raw_instrument_ticker",
    "ticker",
    "instrumentCode",
    "shortName",
    "order.instrument.isin",
    "isin",
    "instrument.isin",
)
ORDER_QUANTITY_PATHS = ("order.quantity", "fill.quantity", "filledQuantity", "quantity", "qty", "shares")
ORDER_FILLED_QUANTITY_PATHS = ("fill.quantity", "order.filledQuantity", "filledQuantity", "filled_qty", "executedQuantity", "quantity")
ORDER_PRICE_PATHS = ("fill.price", "order.averagePrice", "averagePrice", "fillPrice", "price")
ORDER_CURRENCY_PATHS = (
    "fill.walletImpact.currency",
    "order.currency",
    "order.instrument.currency",
    "currency",
    "currencyCode",
    "money.currency",
    "walletImpact.currency",
)


def pick(data: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def pick_path(data: Dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = data
        found = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and current is not None:
            return current
    return None


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def iso_datetime_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def transaction_window(target_date: date, lookback_days: int) -> Dict[str, str]:
    start_date = target_date - timedelta(days=max(lookback_days - 1, 0))
    start = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return {
        "from": iso_datetime_utc(start),
        "to": iso_datetime_utc(end),
    }


def parse_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)

    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def event_datetime(payload: Dict[str, Any], paths: Iterable[str]) -> Optional[datetime]:
    return parse_datetime(pick_path(payload, *paths))


def event_date(payload: Dict[str, Any], paths: Iterable[str]) -> Optional[date]:
    parsed = event_datetime(payload, paths)
    return parsed.astimezone(timezone.utc).date() if parsed else None


def filter_events_for_date(events: Iterable[Dict[str, Any]], flow_date: date, paths: Iterable[str]) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for event in events:
        if event_date(event, paths) == flow_date:
            filtered.append(event)
    return filtered


def filter_transactions_for_date(transactions: Iterable[Dict[str, Any]], flow_date: date) -> List[Dict[str, Any]]:
    return filter_events_for_date(transactions, flow_date, TRANSACTION_TIME_PATHS)


def filter_orders_for_date(orders: Iterable[Dict[str, Any]], flow_date: date) -> List[Dict[str, Any]]:
    return filter_events_for_date(orders, flow_date, ORDER_TIME_PATHS)


def to_float_or_none(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def stable_hash(prefix: str, payload: Dict[str, Any], keys: List[str]) -> str:
    basis = "|".join(str(pick_path(payload, key) or "") for key in keys)
    if not basis.replace("|", ""):
        basis = repr(sorted(payload.items()))
    return f"{prefix}_{hashlib.sha256(basis.encode('utf-8')).hexdigest()[:16]}"


def stable_position_ticker(position: Dict[str, Any]) -> str:
    explicit = pick_path(position, *POSITION_TICKER_PATHS)
    if explicit:
        return str(explicit)
    return stable_hash(
        "unknown_position",
        position,
        ["instrument.name", "name", "instrumentName", "fullName", "quantity", "ownedQuantity"],
    )


def stable_transaction_id(transaction: Dict[str, Any]) -> str:
    explicit_id = pick_path(
        transaction,
        "id",
        "transactionId",
        "reference",
        "orderId",
        "eventId",
        "historyId",
    )
    if explicit_id:
        return str(explicit_id)
    return stable_hash(
        "transaction",
        transaction,
        [
            "dateTime",
            "date",
            "time",
            "created",
            "timestamp",
            "transactionTime",
            "eventTime",
            "type",
            "eventType",
            "ticker",
            "instrument.ticker",
            "quantity",
            "amount",
            "price",
        ],
    )


def stable_order_id(order: Dict[str, Any]) -> str:
    explicit_id = pick_path(order, *ORDER_ID_PATHS)
    if explicit_id:
        return str(explicit_id)
    return stable_hash(
        "order",
        order,
        [
            "order.id",
            "fill.id",
            "fill.filledAt",
            "order.createdAt",
            "order.side",
            "order.type",
            "order.status",
            "order.ticker",
            "order.instrument.ticker",
            "fill.quantity",
            "fill.price",
            "order.value",
            "order.filledValue",
            "fill.walletImpact.netValue",
            "dateTime",
            "createdAt",
            "filledAt",
            "type",
            "side",
            "ticker",
            "quantity",
            "filledQuantity",
            "averagePrice",
            "total",
            "value",
        ],
    )


def normalize_text(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def abs_float(value: Optional[float]) -> float:
    return abs(value) if value is not None else 0.0


def signed_amount(transaction: Dict[str, Any]) -> Optional[float]:
    amount = to_float_or_none(pick_path(
        transaction,
        "amount",
        "total",
        "value",
        "result",
        "cashAmount",
        "money.amount",
        "walletImpact.cash",
        "walletImpact.amount",
    ))
    if amount is not None:
        return amount

    quantity = to_float_or_none(pick_path(transaction, "quantity", "qty", "shares", "filledQuantity"))
    price = to_float_or_none(pick_path(transaction, "price", "fillPrice", "averagePrice"))
    if quantity is not None and price is not None:
        return quantity * price
    return None


def order_amount(order: Dict[str, Any]) -> Optional[float]:
    amount = to_float_or_none(pick_path(
        order,
        "order.filledValue",
        "order.value",
        "fill.walletImpact.netValue",
        "fill.walletImpact.amount",
        "total",
        "value",
        "amount",
        "cashAmount",
        "filledValue",
        "money.amount",
        "walletImpact.amount",
        "walletImpact.cash",
    ))
    if amount is not None:
        return amount

    quantity = to_float_or_none(pick_path(order, *ORDER_FILLED_QUANTITY_PATHS))
    price = to_float_or_none(pick_path(order, *ORDER_PRICE_PATHS, "limitPrice"))
    if quantity is not None and price is not None:
        return quantity * price
    return None


def classify_transaction(transaction: Dict[str, Any]) -> str:
    tx_type = normalize_text(pick_path(transaction, "type", "eventType", "action", "kind"))
    if any(token in tx_type for token in ("deposit", "fund", "cash_in", "pay_in")):
        return "deposit"
    if any(token in tx_type for token in ("withdraw", "cash_out", "pay_out")):
        return "withdrawal"
    if any(token in tx_type for token in ("buy", "market_buy", "limit_buy", "autoinvest", "auto_invest", "pie_invest")):
        return "buy"
    if any(token in tx_type for token in ("sell", "market_sell", "limit_sell")):
        return "sell"
    if "dividend" in tx_type:
        return "dividend"
    if "interest" in tx_type:
        return "interest"
    if "fee" in tx_type or "commission" in tx_type:
        return "fee"
    return tx_type or "unknown"


def classify_order(order: Dict[str, Any]) -> str:
    side = normalize_text(pick_path(order, *ORDER_SIDE_PATHS))
    order_type = normalize_text(pick_path(order, *ORDER_TYPE_PATHS))
    text = " ".join(part for part in (side, order_type) if part)
    if side == "buy" or "buy" in text:
        return "buy"
    if side == "sell" or "sell" in text:
        return "sell"
    if order_type in {"market_buy", "limit_buy", "autoinvest", "auto_invest", "pie_invest"}:
        return "buy"
    if order_type in {"market_sell", "limit_sell"}:
        return "sell"
    return side or order_type or "unknown"


def is_filled_order(order: Dict[str, Any]) -> bool:
    status = normalize_text(pick_path(order, *ORDER_STATUS_PATHS))
    amount = order_amount(order)
    if status in {"cancelled", "canceled", "rejected", "failed"}:
        return False
    return status in {"filled", "executed", "completed"} or abs_float(amount) > 0


def cash_flow_dates_from_events(
    snapshot_date: date,
    transactions: Iterable[Dict[str, Any]],
    orders: Iterable[Dict[str, Any]],
) -> List[date]:
    flow_dates = {snapshot_date}

    for transaction in transactions:
        tx_date = event_date(transaction, TRANSACTION_TIME_PATHS)
        if tx_date and tx_date <= snapshot_date:
            flow_dates.add(tx_date)

    for order in orders:
        order_date = event_date(order, ORDER_TIME_PATHS)
        order_class = classify_order(order)
        if order_date and order_date <= snapshot_date and is_filled_order(order) and order_class in {"buy", "sell"}:
            flow_dates.add(order_date)

    return sorted(flow_dates)


def build_account_snapshot(
    account_id: str,
    snapshot_date: date,
    summary: Dict[str, Any],
    positions: List[Dict[str, Any]],
    fallback_currency: str,
) -> Dict[str, Any]:
    cash_value = to_float_or_none(pick_path(
        summary,
        "cash.availableToTrade",
        "cash.free",
        "cash.total",
        "cash",
        "free",
        "freeCash",
        "cashValue",
        "availableCash",
        "availableToWithdraw",
    ))

    invested_value = to_float_or_none(pick_path(
        summary,
        "investments.currentValue",
        "portfolio.currentValue",
        "invested",
        "investedValue",
        "blocked",
        "portfolioValue",
    ))

    if invested_value is None:
        invested_value = sum(
            to_float_or_none(pick_path(
                position,
                "walletImpact.currentValue",
                "raw_walletImpact_currentValue",
                "currentValue",
                "marketValue",
                "value",
                "amount",
            )) or 0.0
            for position in positions
        )

    total_value = to_float_or_none(pick_path(
        summary,
        "totalValue",
        "accountValue",
        "portfolioValue",
        "value",
        "equity",
        "total",
    ))

    if total_value is None:
        if cash_value is not None:
            total_value = cash_value + (invested_value or 0.0)
        elif invested_value is not None:
            total_value = invested_value

    pnl_total = to_float_or_none(pick_path(
        summary,
        "investments.unrealizedProfitLoss",
        "ppl",
        "pnl",
        "pnlTotal",
        "totalPnl",
        "result",
    ))

    return {
        "account_id": account_id,
        "snapshot_date": snapshot_date.isoformat(),
        "total_value": total_value,
        "cash_value": cash_value,
        "invested_value": invested_value,
        "pnl_total": pnl_total,
        "pnl_daily": to_float_or_none(pick_path(summary, "dailyPpl", "dailyPnl", "pnlDaily")),
        "currency": pick_path(summary, "currency", "currencyCode", "cash.currency") or fallback_currency,
        "raw_payload": summary,
    }


def build_position_snapshot(
    account_id: str,
    snapshot_date: date,
    position: Dict[str, Any],
    fallback_currency: str,
) -> Optional[Dict[str, Any]]:
    if not isinstance(position, dict):
        return None

    ticker = stable_position_ticker(position)
    quantity = to_float_or_none(pick_path(position, "quantity", "qty", "ownedQuantity", "pieQuantity"))
    average_price = to_float_or_none(pick_path(position, "averagePricePaid", "averagePrice", "avgPrice", "average_price"))
    current_price = to_float_or_none(pick_path(position, "currentPrice", "price", "current_price"))

    market_value = to_float_or_none(pick_path(
        position,
        "walletImpact.currentValue",
        "raw_walletImpact_currentValue",
        "currentValue",
        "marketValue",
        "value",
    ))

    cost_basis = to_float_or_none(pick_path(
        position,
        "walletImpact.totalCost",
        "raw_walletImpact_totalCost",
        "investedValue",
        "costBasis",
        "cost_basis",
    ))

    if market_value is None and quantity is not None and current_price is not None:
        market_value = quantity * current_price

    if cost_basis is None and quantity is not None and average_price is not None:
        cost_basis = quantity * average_price

    unrealized_pnl = to_float_or_none(pick_path(
        position,
        "walletImpact.unrealizedProfitLoss",
        "raw_walletImpact_unrealizedProfitLoss",
        "ppl",
        "pnl",
        "unrealizedPnl",
        "unrealized_pnl",
        "result",
    ))
    if unrealized_pnl is None and market_value is not None and cost_basis is not None:
        unrealized_pnl = market_value - cost_basis

    unrealized_pnl_pct = to_float_or_none(pick_path(
        position,
        "pplPerc",
        "pplPercentage",
        "pnlPct",
        "unrealizedPnlPct",
    ))
    if unrealized_pnl_pct is None and unrealized_pnl is not None and cost_basis not in (None, 0):
        unrealized_pnl_pct = unrealized_pnl / cost_basis

    return {
        "account_id": account_id,
        "snapshot_date": snapshot_date.isoformat(),
        "ticker": ticker,
        "instrument_name": pick_path(position, "instrument.name", "raw_instrument_name", "name", "instrumentName", "fullName", "issueName", "shortName"),
        "quantity": quantity,
        "average_price": average_price,
        "current_price": current_price,
        "market_value": market_value,
        "cost_basis": cost_basis,
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "currency": pick_path(position, "walletImpact.currency", "raw_walletImpact_currency", "instrument.currency", "raw_instrument_currency", "currency", "currencyCode") or fallback_currency,
        "raw_payload": position,
    }


def build_transaction(
    account_id: str,
    transaction: Dict[str, Any],
    fallback_currency: str,
) -> Optional[Dict[str, Any]]:
    raw_time = pick_path(transaction, *TRANSACTION_TIME_PATHS)
    parsed_time = parse_datetime(raw_time)
    if not parsed_time:
        return None

    return {
        "account_id": account_id,
        "provider_transaction_id": stable_transaction_id(transaction),
        "transaction_time": iso_datetime_utc(parsed_time),
        "transaction_type": pick_path(transaction, "type", "eventType", "action", "kind"),
        "ticker": pick_path(transaction, "instrument.ticker", "raw_instrument_ticker", "ticker", "instrumentCode", "shortName", "isin", "instrument.isin"),
        "quantity": to_float_or_none(pick_path(transaction, "quantity", "qty", "shares", "filledQuantity")),
        "price": to_float_or_none(pick_path(transaction, "price", "fillPrice", "averagePrice")),
        "amount": signed_amount(transaction),
        "fee": to_float_or_none(pick_path(transaction, "fee", "fees", "commission", "fxFee", "taxes")),
        "currency": pick_path(transaction, "currency", "currencyCode", "money.currency", "walletImpact.currency") or fallback_currency,
        "fx_rate": to_float_or_none(pick_path(transaction, "fxRate", "exchangeRate")),
        "raw_payload": transaction,
    }


def build_order_history(
    account_id: str,
    order: Dict[str, Any],
    fallback_currency: str,
) -> Dict[str, Any]:
    parsed_time = event_datetime(order, ORDER_TIME_PATHS)
    return {
        "account_id": account_id,
        "provider_order_id": stable_order_id(order),
        "order_time": iso_datetime_utc(parsed_time) if parsed_time else None,
        "order_type": pick_path(order, *ORDER_SIDE_PATHS, *ORDER_TYPE_PATHS),
        "status": pick_path(order, *ORDER_STATUS_PATHS),
        "ticker": pick_path(order, *ORDER_TICKER_PATHS),
        "quantity": to_float_or_none(pick_path(order, *ORDER_QUANTITY_PATHS)),
        "filled_quantity": to_float_or_none(pick_path(order, *ORDER_FILLED_QUANTITY_PATHS)),
        "limit_price": to_float_or_none(pick_path(order, "order.limitPrice", "limitPrice", "limit_price")),
        "stop_price": to_float_or_none(pick_path(order, "order.stopPrice", "stopPrice", "stop_price")),
        "average_price": to_float_or_none(pick_path(order, *ORDER_PRICE_PATHS)),
        "total_value": order_amount(order),
        "currency": pick_path(order, *ORDER_CURRENCY_PATHS) or fallback_currency,
        "raw_payload": order,
    }


def build_raw_api_event(
    account_id: str,
    sync_run_id: str,
    endpoint_name: str,
    endpoint_path: str,
    page_number: int,
    params: Dict[str, Any],
    item_count: int,
    payload: Any,
) -> Dict[str, Any]:
    return {
        "account_id": account_id,
        "sync_run_id": sync_run_id,
        "endpoint_name": endpoint_name,
        "endpoint_path": endpoint_path,
        "page_number": page_number,
        "request_params": params,
        "item_count": item_count,
        "raw_payload": payload,
    }


def build_page_raw_events(
    account_id: str,
    sync_run_id: str,
    endpoint_name: str,
    pages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return [
        build_raw_api_event(
            account_id,
            sync_run_id,
            endpoint_name,
            str(page.get("path") or ""),
            int(page.get("page_number") or index),
            dict(page.get("params") or {}),
            int(page.get("item_count") or 0),
            page.get("payload"),
        )
        for index, page in enumerate(pages, start=1)
    ]


def build_daily_cash_flow(
    account_id: str,
    flow_date: date,
    account_snapshot: Optional[Dict[str, Any]],
    transactions: List[Dict[str, Any]],
    orders: List[Dict[str, Any]],
    previous_snapshot: Optional[Dict[str, Any]],
    existing_cash_flow: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    daily_transactions = filter_transactions_for_date(transactions, flow_date)
    daily_orders = filter_orders_for_date(orders, flow_date)
    daily_filled_trade_orders = [
        order
        for order in daily_orders
        if is_filled_order(order) and classify_order(order) in {"buy", "sell"}
    ]

    deposit_amount = 0.0
    withdrawal_amount = 0.0
    buy_amount_from_transactions = 0.0
    sell_amount_from_transactions = 0.0
    buy_amount_from_orders = 0.0
    sell_amount_from_orders = 0.0
    dividend_amount = 0.0
    interest_amount = 0.0
    fee_amount = 0.0
    fx_fee_amount = 0.0

    for transaction in daily_transactions:
        tx_class = classify_transaction(transaction)
        amount = signed_amount(transaction)
        fee = to_float_or_none(pick_path(transaction, "fee", "fees", "commission", "taxes"))
        fx_fee = to_float_or_none(pick_path(transaction, "fxFee", "fx_fee", "currencyConversionFee"))

        if tx_class == "deposit":
            deposit_amount += abs_float(amount)
        elif tx_class == "withdrawal":
            withdrawal_amount += abs_float(amount)
        elif tx_class == "buy":
            buy_amount_from_transactions += abs_float(amount)
        elif tx_class == "sell":
            sell_amount_from_transactions += abs_float(amount)
        elif tx_class == "dividend":
            dividend_amount += abs_float(amount)
        elif tx_class == "interest":
            interest_amount += abs_float(amount)
        elif tx_class == "fee":
            fee_amount += abs_float(amount)

        fee_amount += abs_float(fee)
        fx_fee_amount += abs_float(fx_fee)

    for order in daily_filled_trade_orders:
        order_class = classify_order(order)
        amount = order_amount(order)
        if order_class == "buy":
            buy_amount_from_orders += abs_float(amount)
        elif order_class == "sell":
            sell_amount_from_orders += abs_float(amount)

    has_daily_order_cash_flow = bool(daily_filled_trade_orders)
    buy_amount = buy_amount_from_orders if has_daily_order_cash_flow else buy_amount_from_transactions
    sell_amount = sell_amount_from_orders if has_daily_order_cash_flow else sell_amount_from_transactions

    closing_cash = to_float_or_none(account_snapshot.get("cash_value")) if account_snapshot else None
    opening_cash = to_float_or_none(previous_snapshot.get("cash_value")) if previous_snapshot else None
    cash_change = closing_cash - opening_cash if closing_cash is not None and opening_cash is not None else None

    if existing_cash_flow:
        if closing_cash is None:
            closing_cash = to_float_or_none(existing_cash_flow.get("closing_cash"))
        if opening_cash is None:
            opening_cash = to_float_or_none(existing_cash_flow.get("opening_cash"))
        if cash_change is None:
            cash_change = to_float_or_none(existing_cash_flow.get("cash_change"))

    raw_payload = {
        "transactions": daily_transactions,
        "orders": daily_filled_trade_orders if has_daily_order_cash_flow else daily_orders,
        "raw_transaction_count": len(transactions),
        "raw_order_count": len(daily_filled_trade_orders) if has_daily_order_cash_flow else len(daily_orders),
        "fetched_order_count": len(orders),
        "excluded_transaction_count": len(transactions) - len(daily_transactions),
        "excluded_order_count": len(orders) - len(daily_orders),
        "excluded_non_filled_or_non_trade_order_count": len(daily_orders) - len(daily_filled_trade_orders),
        "transaction_classes": [classify_transaction(transaction) for transaction in daily_transactions],
        "order_classes": [classify_order(order) for order in (daily_filled_trade_orders if has_daily_order_cash_flow else daily_orders)],
        "buy_amount_source": "order_history" if has_daily_order_cash_flow else "transactions",
        "computed_from_order_history": has_daily_order_cash_flow,
    }

    return {
        "account_id": account_id,
        "flow_date": flow_date.isoformat(),
        "opening_cash": opening_cash,
        "closing_cash": closing_cash,
        "cash_change": cash_change,
        "deposit_amount": deposit_amount,
        "withdrawal_amount": withdrawal_amount,
        "buy_amount": buy_amount,
        "sell_amount": sell_amount,
        "dividend_amount": dividend_amount,
        "interest_amount": interest_amount,
        "fee_amount": fee_amount,
        "fx_fee_amount": fx_fee_amount,
        "net_contribution": deposit_amount - withdrawal_amount,
        "net_trading_cash_flow": sell_amount + dividend_amount + interest_amount - buy_amount - fee_amount - fx_fee_amount,
        "transaction_count": len(daily_transactions) + len(daily_filled_trade_orders),
        "raw_payload": raw_payload,
    }


def sync_one_account(settings: Settings, store: SupabaseStore, account: AccountConfig) -> None:
    account_row = store.upsert_account(account)
    account_id = account_row["id"]
    sync_run_id = store.create_sync_run(account_id)
    inserted = 0
    updated = 0

    try:
        client = Trading212Client(
            settings.trading212_base_url,
            account.api_key,
            account.api_secret,
        )
        snapshot_date = today_utc()

        summary = client.get_account_summary(settings.account_cash_path)
        positions = client.get_portfolio(settings.portfolio_path)
        print(f"Fetched {len(positions)} positions for {account.account_key}")

        raw_events: List[Dict[str, Any]] = [
            build_raw_api_event(account_id, sync_run_id, "account_summary", settings.account_cash_path, 1, {}, 1, summary),
            build_raw_api_event(account_id, sync_run_id, "positions", settings.portfolio_path, 1, {}, len(positions), positions),
        ]

        if settings.sync_transactions:
            params = transaction_window(snapshot_date, settings.transaction_lookback_days)
            transactions, transaction_pages = client.get_transactions_with_pages(
                settings.transactions_path,
                params=params,
                max_pages=settings.transaction_max_pages,
                page_delay_seconds=settings.transaction_page_delay_seconds,
            )
            raw_events.extend(build_page_raw_events(account_id, sync_run_id, "transactions", transaction_pages))
            print(f"Fetched {len(transactions)} transactions for {account.account_key}")
        else:
            store.insert_sync_warning(
                account_id,
                sync_run_id,
                "transactions_disabled",
                "SYNC_TRANSACTIONS=false; cash-flow detail may be incomplete.",
            )
            print("Skipping Trading 212 transactions sync because SYNC_TRANSACTIONS=false")
            transactions = []

        if settings.sync_orders:
            order_params = transaction_window(snapshot_date, settings.order_lookback_days)
            orders, order_pages = client.get_orders_with_pages(
                settings.orders_path,
                params=order_params,
                max_pages=settings.order_max_pages,
                page_delay_seconds=settings.order_page_delay_seconds,
            )
            raw_events.extend(build_page_raw_events(account_id, sync_run_id, "orders", order_pages))
            print(f"Fetched {len(orders)} orders for {account.account_key}")
        else:
            store.insert_sync_warning(
                account_id,
                sync_run_id,
                "orders_disabled",
                "SYNC_ORDERS=false; order-level buy/sell detail will be missing.",
            )
            print("Skipping Trading 212 orders sync because SYNC_ORDERS=false")
            orders = []

        if settings.sync_raw_api:
            inserted += store.upsert_raw_api_events(raw_events)
        else:
            store.insert_sync_warning(
                account_id,
                sync_run_id,
                "raw_api_archive_disabled",
                "SYNC_RAW_API=false; raw endpoint payloads were not archived.",
            )

        if settings.sync_orders and not orders:
            store.insert_sync_warning(
                account_id,
                sync_run_id,
                "orders_empty",
                "Trading 212 orders endpoint returned zero rows for the configured lookback window.",
                {"order_lookback_days": settings.order_lookback_days},
            )

        account_snapshot = build_account_snapshot(
            account_id,
            snapshot_date,
            summary,
            positions,
            account.base_currency,
        )
        previous_snapshot = store.get_previous_account_snapshot(account_id, snapshot_date)
        store.upsert_account_snapshot(account_snapshot)
        updated += 1

        position_payloads = [
            payload for payload in (
                build_position_snapshot(account_id, snapshot_date, position, account.base_currency)
                for position in positions
            )
            if payload is not None
        ]
        deleted_positions = store.delete_position_snapshots(account_id, snapshot_date)
        if deleted_positions:
            print(f"Deleted {deleted_positions} existing positions for {account.account_key} on {snapshot_date}")
        updated += store.upsert_position_snapshots(position_payloads)

        transaction_payloads = [
            payload for payload in (
                build_transaction(account_id, transaction, account.base_currency)
                for transaction in transactions
            )
            if payload is not None
        ]
        inserted += store.upsert_transactions(transaction_payloads)

        order_payloads = [build_order_history(account_id, order, account.base_currency) for order in orders]
        inserted += store.upsert_order_history(order_payloads)

        cash_flow_dates = cash_flow_dates_from_events(snapshot_date, transactions, orders)
        for flow_date in cash_flow_dates:
            flow_snapshot = account_snapshot if flow_date == snapshot_date else store.get_account_snapshot(account_id, flow_date)
            flow_previous_snapshot = store.get_previous_account_snapshot(account_id, flow_date)
            existing_cash_flow = store.get_daily_cash_flow(account_id, flow_date)
            store.upsert_daily_cash_flow(build_daily_cash_flow(
                account_id,
                flow_date,
                flow_snapshot,
                transactions,
                orders,
                flow_previous_snapshot,
                existing_cash_flow,
            ))
            updated += 1

        calculate_and_store_daily_metrics(store, account_id, snapshot_date)
        updated += 1

        store.finish_sync_run(
            sync_run_id,
            status="success",
            records_inserted=inserted,
            records_updated=updated,
        )
    except Exception as exc:
        store.finish_sync_run(sync_run_id, status="failed", error_message=str(exc))
        raise


def sync_all_accounts(settings: Settings) -> None:
    store = SupabaseStore(settings)
    for account in settings.accounts:
        sync_one_account(settings, store, account)
