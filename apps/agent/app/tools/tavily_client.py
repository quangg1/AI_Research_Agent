from __future__ import annotations

import threading
from typing import Any

import httpx

from app.config import settings
from app.domain.retrieval_limits import API_RESULTS_PER_QUERY
from app.llm.providers import is_retryable_slot_error, is_unusable_key, split_api_keys
from app.observability.logging import logger

TAVILY_URL = "https://api.tavily.com/search"


class TavilyClient:
    """Round-robin Tavily keys with failover on rate limits and dead keys."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._index = 0
        self._dead: set[int] = set()
        self._keys: list[str] = []

    def reload(self) -> None:
        keys = split_api_keys(settings.tavily_api_key)
        with self._lock:
            self._keys = keys
            if self._index >= len(self._keys):
                self._index = 0
            self._dead = {i for i in self._dead if i < len(self._keys)}

    @property
    def available(self) -> bool:
        self.reload()
        with self._lock:
            return bool(self._keys) and any(i not in self._dead for i in range(len(self._keys)))

    def search(self, query: str) -> list[dict[str, Any]]:
        self.reload()
        with self._lock:
            keys = list(self._keys)
            dead = set(self._dead)
            start = self._index
            self._index = (self._index + 1) % max(1, len(keys))
        if not keys:
            return []

        tried: set[int] = set()
        for step in range(len(keys)):
            idx = (start + step) % len(keys)
            if idx in tried or idx in dead:
                continue
            tried.add(idx)
            rows, status, body = self._call(query, keys[idx])
            if rows is not None:
                return rows
            permanent = is_unusable_key(status, body)
            if permanent:
                with self._lock:
                    self._dead.add(idx)
                logger.warning("tavily_key_dead slot=%s/%s status=%s", idx + 1, len(keys), status)
            elif is_retryable_slot_error(status, body):
                logger.warning("tavily_rate_limited slot=%s/%s status=%s", idx + 1, len(keys), status)
                continue
            logger.warning("tavily_failed slot=%s/%s status=%s", idx + 1, len(keys), status)
        return []

    def _call(
        self, query: str, api_key: str
    ) -> tuple[list[dict[str, Any]] | None, int | None, str]:
        try:
            with httpx.Client(timeout=20) as client:
                response = client.post(
                    TAVILY_URL,
                    json={
                        "api_key": api_key,
                        "query": query,
                        "search_depth": "advanced",
                        "max_results": API_RESULTS_PER_QUERY,
                    },
                )
                body = response.text
                if response.is_success:
                    data = response.json()
                    return list(data.get("results") or []), response.status_code, body
                return None, response.status_code, body
        except Exception as exc:
            logger.warning("tavily_request_failed %s", exc)
            return None, None, str(exc)


tavily = TavilyClient()
