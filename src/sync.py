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


def transaction_datetime(transaction: Dict[str, Any]) -> Optional[datetime]:
    return parse_datetime(pick_path(transaction, *TRANSACTION_TIME_PATHS))


def transaction_date(transaction: Dict[str, Any]) -> Optional[date]:
    parsed = transaction_datetime(transaction)
    return parsed.astimezone(timezone.utc).date() if parsed else None


def filter_transactions_for_date(transactions: Iterable[Dict[str, Any]], flow_date: date) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for transaction in transactions:
        tx_date = transaction_date(transaction)
        if tx_date == flow_date:
            filtered.append(transaction)
    return filtered


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


def classify_transaction(transaction: Dict[str, Any]) -> str:
    tx_type = normalize_text(pick_path(transaction, "type", "eventType", "action", "kind"))
    if any(token in tx_type for token in ("deposit", "fund", "cash_in", "pay_in")):
        return "deposit"
    if any(token in tx_type for token in ("withdraw", "cash_out", "pay_out")):
        return "withdrawal"
    if any(token in tx_type for token in ("buy", "market_buy", "limit_buy")):
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

    # Trading 212 may quote UK instruments in GBX while walletImpact is in GBP.
    # Always prefer walletImpact values for portfolio-level accounting.
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


def build_daily_cash_flow(
    account_id: str,
    flow_date: date,
    account_snapshot: Dict[str, Any],
    transactions: List[Dict[str, Any]],
    previous_snapshot: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    daily_transactions = filter_transactions_for_date(transactions, flow_date)

    deposit_amount = 0.0
    withdrawal_amount = 0.0
    buy_amount = 0.0
    sell_amount = 0.0
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
            buy_amount += abs_float(amount)
        elif tx_class == "sell":
            sell_amount += abs_float(amount)
        elif tx_class == "dividend":
            dividend_amount += abs_float(amount)
        elif tx_class == "interest":
            interest_amount += abs_float(amount)
        elif tx_class == "fee":
            fee_amount += abs_float(amount)

        fee_amount += abs_float(fee)
        fx_fee_amount += abs_float(fx_fee)

    closing_cash = to_float_or_none(account_snapshot.get("cash_value"))
    opening_cash = to_float_or_none(previous_snapshot.get("cash_value")) if previous_snapshot else None
    cash_change = closing_cash - opening_cash if closing_cash is not None and opening_cash is not None else None

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
        "transaction_count": len(daily_transactions),
        "raw_payload": {
            "transactions": daily_transactions,
            "raw_transaction_count": len(transactions),
            "excluded_transaction_count": len(transactions) - len(daily_transactions),
            "transaction_classes": [classify_transaction(transaction) for transaction in daily_transactions],
        },
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

        if settings.sync_transactions:
            params = transaction_window(snapshot_date, settings.transaction_lookback_days)
            transactions = client.get_transactions(
                settings.transactions_path,
                params=params,
                max_pages=settings.transaction_max_pages,
                page_delay_seconds=settings.transaction_page_delay_seconds,
            )
            print(f"Fetched {len(transactions)} transactions for {account.account_key}")
        else:
            print("Skipping Trading 212 transactions sync because SYNC_TRANSACTIONS=false")
            transactions = []

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

        store.upsert_daily_cash_flow(build_daily_cash_flow(
            account_id,
            snapshot_date,
            account_snapshot,
            transactions,
            previous_snapshot,
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
