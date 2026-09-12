from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List

from dotenv import load_dotenv


@dataclass(frozen=True)
class AccountConfig:
    account_key: str
    api_key: str
    api_secret: str
    base_currency: str

    @property
    def account_name(self) -> str:
        return self.account_key.replace("_", " ").title()


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_service_role_key: str
    trading212_base_url: str
    account_cash_path: str
    portfolio_path: str
    transactions_path: str
    orders_path: str
    accounts: List[AccountConfig]
    export_dir: str
    sync_transactions: bool
    transaction_lookback_days: int
    transaction_max_pages: int
    transaction_page_delay_seconds: int
    sync_orders: bool
    order_lookback_days: int
    order_max_pages: int
    order_page_delay_seconds: int
    sync_raw_api: bool


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise RuntimeError(f"Invalid integer environment variable: {name}") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}")
    return value


def _parse_accounts(raw: str) -> List[AccountConfig]:
    accounts: List[AccountConfig] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [part.strip() for part in chunk.split(":")]
        if len(parts) != 4:
            raise RuntimeError(
                "Invalid TRADING212_ACCOUNTS format. Expected: "
                "account_key:api_key:api_secret:base_currency"
            )
        account_key, api_key, api_secret, base_currency = parts
        if not account_key or not api_key or not api_secret or not base_currency:
            raise RuntimeError(
                "Invalid TRADING212_ACCOUNTS format. None of account_key, "
                "api_key, api_secret, base_currency can be empty."
            )
        accounts.append(AccountConfig(
            account_key=account_key,
            api_key=api_key,
            api_secret=api_secret,
            base_currency=base_currency.upper(),
        ))
    if not accounts:
        raise RuntimeError("TRADING212_ACCOUNTS must contain at least one account")
    return accounts


def load_settings() -> Settings:
    load_dotenv()

    return Settings(
        supabase_url=_required_env("SUPABASE_URL"),
        supabase_service_role_key=_required_env("SUPABASE_SERVICE_ROLE_KEY"),
        trading212_base_url=os.getenv("TRADING212_BASE_URL", "https://live.trading212.com").rstrip("/"),
        account_cash_path=os.getenv(
            "TRADING212_ACCOUNT_CASH_PATH",
            "/api/v0/equity/account/summary",
        ),
        portfolio_path=os.getenv(
            "TRADING212_PORTFOLIO_PATH",
            "/api/v0/equity/positions",
        ),
        transactions_path=os.getenv(
            "TRADING212_TRANSACTIONS_PATH",
            "/api/v0/equity/history/transactions",
        ),
        orders_path=os.getenv(
            "TRADING212_ORDERS_PATH",
            "/api/v0/equity/history/orders",
        ),
        accounts=_parse_accounts(_required_env("TRADING212_ACCOUNTS")),
        export_dir=os.getenv("EXPORT_DIR", "exports"),
        sync_transactions=_env_bool("SYNC_TRANSACTIONS", default=True),
        transaction_lookback_days=_env_int("TRANSACTION_LOOKBACK_DAYS", default=1, minimum=1),
        transaction_max_pages=_env_int("TRANSACTION_MAX_PAGES", default=1, minimum=1),
        transaction_page_delay_seconds=_env_int("TRANSACTION_PAGE_DELAY_SECONDS", default=15, minimum=0),
        sync_orders=_env_bool("SYNC_ORDERS", default=True),
        order_lookback_days=_env_int("ORDER_LOOKBACK_DAYS", default=7, minimum=1),
        order_max_pages=_env_int("ORDER_MAX_PAGES", default=3, minimum=1),
        order_page_delay_seconds=_env_int("ORDER_PAGE_DELAY_SECONDS", default=15, minimum=0),
        sync_raw_api=_env_bool("SYNC_RAW_API", default=True),
    )
