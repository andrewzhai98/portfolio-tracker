from __future__ import annotations

import time
from json import JSONDecodeError
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests


class Trading212APIError(RuntimeError):
    """Raised when Trading 212 returns an unusable API response."""


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
        last_error: Optional[BaseException] = None

        for attempt in range(max_retries):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                wait_seconds = min(60, 10 * (attempt + 1))
                if attempt < max_retries - 1:
                    print(f"Trading 212 request failed for {url}. Waiting {wait_seconds}s before retry: {exc}")
                    time.sleep(wait_seconds)
                    continue
                raise Trading212APIError(f"Trading 212 request failed for {url}: {exc}") from exc

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

            if not response.text or not response.text.strip():
                raise Trading212APIError(f"Trading 212 returned an empty response for {url}")

            try:
                return response.json()
            except JSONDecodeError as exc:
                detail = response.text[:500]
                raise Trading212APIError(
                    f"Trading 212 returned invalid JSON for {url}. Response body: {detail}"
                ) from exc

        if last_error is not None:
            raise Trading212APIError(f"Trading 212 request failed for {url}: {last_error}") from last_error
        raise requests.HTTPError(
            f"Trading 212 API rate limit persisted after {max_retries} retries: {url}"
        )

    @staticmethod
    def _extract_items(data: Any, collection_keys: Tuple[str, ...]) -> List[Dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in collection_keys:
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _next_path(data: Any) -> Optional[str]:
        if not isinstance(data, dict):
            return None
        next_page_path = data.get("nextPagePath") or data.get("next_page_path") or data.get("next")
        return next_page_path if isinstance(next_page_path, str) and next_page_path else None

    def get_account_summary(self, path: str) -> Dict[str, Any]:
        data = self._get(path)
        if isinstance(data, dict):
            return data
        return {"raw": data}

    def get_account_cash(self, path: str) -> Dict[str, Any]:
        return self.get_account_summary(path)

    def get_portfolio(self, path: str) -> List[Dict[str, Any]]:
        data = self._get(path)
        return self._extract_items(data, ("items", "positions", "data", "results"))

    def get_paginated_items_with_pages(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        max_pages: int = 1,
        page_delay_seconds: int = 15,
        collection_keys: Tuple[str, ...] = ("items", "data", "results"),
        label: str = "items",
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        items: List[Dict[str, Any]] = []
        raw_pages: List[Dict[str, Any]] = []
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
                print(f"Stopped {label} pagination after {max_pages} pages to avoid Trading 212 rate limits.")
                break

            if page_count > 1 and page_delay_seconds > 0:
                print(f"Waiting {page_delay_seconds}s before fetching next {label} page...")
                time.sleep(page_delay_seconds)

            data = self._get(next_path, params=next_params)
            page_items = self._extract_items(data, collection_keys)
            items.extend(page_items)
            raw_pages.append({
                "page_number": page_count,
                "path": next_path,
                "params": next_params or {},
                "item_count": len(page_items),
                "payload": data,
            })

            next_params = None
            next_path = self._next_path(data)

        return items, raw_pages

    def get_transactions_with_pages(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        max_pages: int = 1,
        page_delay_seconds: int = 15,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        return self.get_paginated_items_with_pages(
            path,
            params=params,
            max_pages=max_pages,
            page_delay_seconds=page_delay_seconds,
            collection_keys=("items", "transactions", "data", "results"),
            label="transaction",
        )

    def get_transactions(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        max_pages: int = 1,
        page_delay_seconds: int = 15,
    ) -> List[Dict[str, Any]]:
        items, _pages = self.get_transactions_with_pages(path, params, max_pages, page_delay_seconds)
        return items

    def get_orders_with_pages(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        max_pages: int = 1,
        page_delay_seconds: int = 15,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        return self.get_paginated_items_with_pages(
            path,
            params=params,
            max_pages=max_pages,
            page_delay_seconds=page_delay_seconds,
            collection_keys=("items", "orders", "data", "results"),
            label="order",
        )

    def get_orders(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        max_pages: int = 1,
        page_delay_seconds: int = 15,
    ) -> List[Dict[str, Any]]:
        items, _pages = self.get_orders_with_pages(path, params, max_pages, page_delay_seconds)
        return items
