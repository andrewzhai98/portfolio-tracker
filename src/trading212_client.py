from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests


class Trading212Client:
    def __init__(self, base_url: str, api_key: str, api_secret: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = (api_key, api_secret)
        self.session.headers.update({"Accept": "application/json"})

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(f"{self.base_url}/", path.lstrip("/"))

    def _get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        max_retries: int = 5,
    ) -> Any:
        url = self._url(path)

        for attempt in range(max_retries):
            response = self.session.get(url, params=params, timeout=self.timeout)

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    wait_seconds = max(10, int(retry_after))
                else:
                    wait_seconds = min(60, 10 * (attempt + 1))

                print(f"Trading 212 rate limited. Waiting {wait_seconds}s before retry...")
                time.sleep(wait_seconds)
                continue

            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                detail = response.text[:500]
                raise requests.HTTPError(
                    f"{exc}. Response body: {detail}",
                    response=response,
                ) from exc

            if not response.text:
                return None
            return response.json()

        raise requests.HTTPError(
            f"Trading 212 API rate limit persisted after {max_retries} retries: {url}"
        )

    def get_account_summary(self, path: str) -> Dict[str, Any]:
        data = self._get(path)
        if isinstance(data, dict):
            return data
        return {"raw": data}

    def get_account_cash(self, path: str) -> Dict[str, Any]:
        return self.get_account_summary(path)

    def get_portfolio(self, path: str) -> List[Dict[str, Any]]:
        data = self._get(path)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "positions", "data", "results"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []

    def get_transactions(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        max_pages: int = 1,
        page_delay_seconds: int = 15,
    ) -> List[Dict[str, Any]]:
        transactions: List[Dict[str, Any]] = []
        next_path: Optional[str] = path
        next_params: Optional[Dict[str, Any]] = params
        seen_paths = set()
        page_count = 0

        while next_path:
            seen_key = f"{next_path}|{next_params}"
            if seen_key in seen_paths:
                break
            seen_paths.add(seen_key)

            page_count += 1
            if page_count > max_pages:
                print(
                    f"Stopped transaction pagination after {max_pages} pages "
                    "to avoid Trading 212 rate limits."
                )
                break

            if page_count > 1 and page_delay_seconds > 0:
                print(f"Waiting {page_delay_seconds}s before fetching next transaction page...")
                time.sleep(page_delay_seconds)

            data = self._get(next_path, params=next_params)
            next_params = None

            if isinstance(data, list):
                transactions.extend(item for item in data if isinstance(item, dict))
                break

            if not isinstance(data, dict):
                break

            page_items: Any = None
            for key in ("items", "transactions", "data", "results"):
                value = data.get(key)
                if isinstance(value, list):
                    page_items = value
                    break

            if page_items is None:
                break

            transactions.extend(item for item in page_items if isinstance(item, dict))
            next_page_path = data.get("nextPagePath") or data.get("next_page_path") or data.get("next")
            next_path = next_page_path if isinstance(next_page_path, str) and next_page_path else None

        return transactions
