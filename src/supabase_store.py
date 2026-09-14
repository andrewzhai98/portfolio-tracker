from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from supabase import Client, create_client

from src.config import AccountConfig, Settings


class SupabaseStore:
    def __init__(self, settings: Settings):
        self.client: Client = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key,
        )

    @staticmethod
    def _data(result: Any, operation: str) -> Any:
        if result is None:
            raise RuntimeError(f"Supabase operation returned no response: {operation}")
        return getattr(result, "data", None)

    @classmethod
    def _rows(cls, result: Any, operation: str) -> List[Dict[str, Any]]:
        data = cls._data(result, operation)
        return data if isinstance(data, list) else []

    def upsert_account(self, account: AccountConfig) -> Dict[str, Any]:
        payload = {
            "provider": "trading212",
            "account_key": account.account_key,
            "account_name": account.account_name,
            "base_currency": account.base_currency,
        }
        result = (
            self.client.table("accounts")
            .upsert(payload, on_conflict="account_key")
            .execute()
        )
        rows = self._rows(result, "read rows")
        if rows:
            return rows[0]

        result = (
            self.client.table("accounts")
            .select("*")
            .eq("account_key", account.account_key)
            .single()
            .execute()
        )
        return self._data(result, "read row")

    def list_accounts(self) -> List[Dict[str, Any]]:
        result = self.client.table("accounts").select("*").execute()
        return self._rows(result, "read rows")

    def count_accounts(self) -> int:
        return len(self.list_accounts())

    def create_sync_run(self, account_id: str) -> str:
        result = (
            self.client.table("sync_runs")
            .insert({"account_id": account_id, "status": "running"})
            .execute()
        )
        rows = self._rows(result, "create sync run")
        if not rows or not rows[0].get("id"):
            raise RuntimeError("Supabase did not return a sync_run id after insert")
        return rows[0]["id"]

    def finish_sync_run(
        self,
        sync_run_id: str,
        status: str,
        error_message: Optional[str] = None,
        records_inserted: int = 0,
        records_updated: int = 0,
    ) -> None:
        self.client.table("sync_runs").update({
            "status": status,
            "error_message": error_message,
            "records_inserted": records_inserted,
            "records_updated": records_updated,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", sync_run_id).execute()

    def upsert_account_snapshot(self, payload: Dict[str, Any]) -> None:
        self.client.table("account_snapshots").upsert(
            payload,
            on_conflict="account_id,snapshot_date",
        ).execute()

    def delete_position_snapshots(self, account_id: str, snapshot_date: date) -> int:
        result = (
            self.client.table("position_snapshots")
            .delete()
            .eq("account_id", account_id)
            .eq("snapshot_date", snapshot_date.isoformat())
            .execute()
        )
        return len(self._rows(result, "delete rows"))

    def get_unknown_position_snapshot_dates(self) -> List[Dict[str, Any]]:
        result = (
            self.client.table("position_snapshots")
            .select("account_id, snapshot_date")
            .like("ticker", "unknown_position_%")
            .execute()
        )
        rows = self._rows(result, "read rows")
        seen = set()
        unique_rows: List[Dict[str, Any]] = []
        for row in rows:
            key = (row.get("account_id"), row.get("snapshot_date"))
            if key in seen:
                continue
            seen.add(key)
            unique_rows.append(row)
        return unique_rows

    def delete_unknown_position_snapshots(self) -> int:
        result = (
            self.client.table("position_snapshots")
            .delete()
            .like("ticker", "unknown_position_%")
            .execute()
        )
        return len(self._rows(result, "delete rows"))

    def upsert_position_snapshots(self, payloads: Iterable[Dict[str, Any]]) -> int:
        rows = list(payloads)
        if not rows:
            return 0
        self.client.table("position_snapshots").upsert(
            rows,
            on_conflict="account_id,snapshot_date,ticker",
        ).execute()
        return len(rows)

    def upsert_transactions(self, payloads: Iterable[Dict[str, Any]]) -> int:
        rows = list(payloads)
        if not rows:
            return 0
        self.client.table("transactions").upsert(
            rows,
            on_conflict="account_id,provider_transaction_id",
        ).execute()
        return len(rows)

    def upsert_order_history(self, payloads: Iterable[Dict[str, Any]]) -> int:
        rows = list(payloads)
        if not rows:
            return 0
        self.client.table("order_history").upsert(
            rows,
            on_conflict="account_id,provider_order_id",
        ).execute()
        return len(rows)

    def upsert_raw_api_events(self, payloads: Iterable[Dict[str, Any]]) -> int:
        rows = list(payloads)
        if not rows:
            return 0
        self.client.table("raw_api_events").upsert(
            rows,
            on_conflict="account_id,sync_run_id,endpoint_name,page_number",
        ).execute()
        return len(rows)

    def insert_sync_warning(
        self,
        account_id: str,
        sync_run_id: str,
        warning_code: str,
        warning_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.client.table("sync_warnings").insert({
            "account_id": account_id,
            "sync_run_id": sync_run_id,
            "warning_code": warning_code,
            "warning_message": warning_message,
            "context": context or {},
        }).execute()

    def upsert_daily_cash_flow(self, payload: Dict[str, Any]) -> None:
        self.client.table("daily_cash_flows").upsert(
            payload,
            on_conflict="account_id,flow_date",
        ).execute()

    def get_daily_cash_flow(self, account_id: str, flow_date: date) -> Optional[Dict[str, Any]]:
        result = (
            self.client.table("daily_cash_flows")
            .select("*")
            .eq("account_id", account_id)
            .eq("flow_date", flow_date.isoformat())
            .maybe_single()
            .execute()
        )
        return self._data(result, "read row")

    def upsert_daily_metric(self, payload: Dict[str, Any]) -> None:
        self.client.table("daily_metrics").upsert(
            payload,
            on_conflict="account_id,metric_date",
        ).execute()

    def delete_daily_metric(self, account_id: str, metric_date: date) -> int:
        result = (
            self.client.table("daily_metrics")
            .delete()
            .eq("account_id", account_id)
            .eq("metric_date", metric_date.isoformat())
            .execute()
        )
        return len(self._rows(result, "delete rows"))

    def get_account_snapshot(self, account_id: str, snapshot_date: date) -> Optional[Dict[str, Any]]:
        result = (
            self.client.table("account_snapshots")
            .select("*")
            .eq("account_id", account_id)
            .eq("snapshot_date", snapshot_date.isoformat())
            .maybe_single()
            .execute()
        )
        return self._data(result, "read row")

    def get_previous_account_snapshot(self, account_id: str, before_date: date) -> Optional[Dict[str, Any]]:
        result = (
            self.client.table("account_snapshots")
            .select("*")
            .eq("account_id", account_id)
            .lt("snapshot_date", before_date.isoformat())
            .order("snapshot_date", desc=True)
            .limit(1)
            .execute()
        )
        rows = self._rows(result, "read rows")
        return rows[0] if rows else None

    def get_position_snapshots(self, account_id: str, snapshot_date: date) -> List[Dict[str, Any]]:
        result = (
            self.client.table("position_snapshots")
            .select("*")
            .eq("account_id", account_id)
            .eq("snapshot_date", snapshot_date.isoformat())
            .execute()
        )
        return self._rows(result, "read rows")

    def get_month_start_snapshot(self, account_id: str, snapshot_date: date) -> Optional[Dict[str, Any]]:
        month_start = snapshot_date.replace(day=1)
        result = (
            self.client.table("account_snapshots")
            .select("*")
            .eq("account_id", account_id)
            .gte("snapshot_date", month_start.isoformat())
            .lte("snapshot_date", snapshot_date.isoformat())
            .order("snapshot_date")
            .limit(1)
            .execute()
        )
        rows = self._rows(result, "read rows")
        return rows[0] if rows else None

    def get_recent_daily_metrics(self, limit: int = 30) -> List[Dict[str, Any]]:
        result = (
            self.client.table("daily_metrics")
            .select("*, accounts(account_key, account_name, base_currency)")
            .order("metric_date", desc=True)
            .limit(limit)
            .execute()
        )
        return self._rows(result, "read rows")

    def get_recent_daily_cash_flows(self, limit: int = 200) -> List[Dict[str, Any]]:
        result = (
            self.client.table("daily_cash_flows")
            .select("*, accounts(account_key, account_name, base_currency)")
            .order("flow_date", desc=True)
            .limit(limit)
            .execute()
        )
        return self._rows(result, "read rows")

    def get_recent_account_snapshots(self, limit: int = 200) -> List[Dict[str, Any]]:
        result = (
            self.client.table("account_snapshots")
            .select("*, accounts(account_key, account_name, base_currency)")
            .order("snapshot_date", desc=True)
            .limit(limit)
            .execute()
        )
        return self._rows(result, "read rows")

    def get_recent_order_history(self, limit: int = 500) -> List[Dict[str, Any]]:
        result = (
            self.client.table("order_history")
            .select("*, accounts(account_key, account_name, base_currency)")
            .order("order_time", desc=True)
            .limit(limit)
            .execute()
        )
        return self._rows(result, "read rows")

    def get_recent_raw_api_events(self, limit: int = 200) -> List[Dict[str, Any]]:
        result = (
            self.client.table("raw_api_events")
            .select("*, accounts(account_key, account_name, base_currency)")
            .order("captured_at", desc=True)
            .limit(limit)
            .execute()
        )
        return self._rows(result, "read rows")

    def get_recent_sync_warnings(self, limit: int = 200) -> List[Dict[str, Any]]:
        result = (
            self.client.table("sync_warnings")
            .select("*, accounts(account_key, account_name, base_currency)")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return self._rows(result, "read rows")

    def get_latest_positions(self) -> List[Dict[str, Any]]:
        result = self.client.rpc("get_latest_positions").execute()
        return self._rows(result, "read rows")

    def get_latest_position_snapshots(self) -> List[Dict[str, Any]]:
        accounts_result = self.client.table("accounts").select("id, account_key, account_name, base_currency").execute()
        accounts = self._rows(accounts_result, "list accounts for latest positions")
        all_rows: List[Dict[str, Any]] = []

        for account in accounts:
            latest_result = (
                self.client.table("position_snapshots")
                .select("snapshot_date")
                .eq("account_id", account["id"])
                .order("snapshot_date", desc=True)
                .limit(1)
                .execute()
            )
            latest_rows = self._rows(latest_result, "get latest position date")
            if not latest_rows:
                continue

            latest_date = latest_rows[0]["snapshot_date"]
            positions_result = (
                self.client.table("position_snapshots")
                .select("*")
                .eq("account_id", account["id"])
                .eq("snapshot_date", latest_date)
                .execute()
            )
            for row in self._rows(positions_result, "get latest position snapshots"):
                row["accounts"] = account
                all_rows.append(row)

        return all_rows
