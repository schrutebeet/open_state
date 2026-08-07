from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo
from typing import Any

import httpx

from civic_metrics.domain import DatasetPayload


@dataclass(frozen=True)
class CachedResponse:
    body: bytes
    source_url: str
    content_type: str
    metadata: dict[str, Any]


class HttpClient:
    """Synchronous HTTP client with retries and a per-run request cache.

    Identical GET requests are performed only once during a pipeline run. This is
    what allows one downloaded dataset to feed several indicators without repeating
    calls to the official source.
    """

    def __init__(self, timeout_seconds: float) -> None:
        self._client = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": "civic-metrics/0.1 (+public-data research)",
                "Accept-Language": "es,en;q=0.7",
            },
        )
        self._cache: dict[str, CachedResponse] = {}

    def close(self) -> None:
        self._client.close()

    def _key(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        headers: dict[str, str] | None,
    ) -> str:
        serialised = json.dumps(
            {
                "method": method,
                "url": url,
                "params": params or {},
                "json": json_body or {},
                # Authentication headers affect the response and must be in the key,
                # but the key itself is a one-way hash and is never logged.
                "headers": headers or {},
            },
            sort_keys=True,
            ensure_ascii=True,
            default=str,
        )
        return hashlib.sha256(serialised.encode()).hexdigest()

    def _request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> CachedResponse:
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                response = self._client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                )
                response.raise_for_status()
                return CachedResponse(
                    body=response.content,
                    source_url=str(response.url),
                    content_type=response.headers.get(
                        "content-type", "application/octet-stream"
                    ).split(";")[0],
                    metadata={
                        "etag": response.headers.get("etag"),
                        "last_modified": response.headers.get("last-modified"),
                        "status_code": response.status_code,
                    },
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt == 3:
                    raise
                time.sleep(min(2**attempt, 8))
        assert last_error is not None
        raise last_error

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
    ) -> CachedResponse:
        cache_key = self._key(method, url, params, json_body, headers)
        if use_cache and cache_key in self._cache:
            return self._cache[cache_key]
        response = self._request(method, url, params, json_body, headers)
        if use_cache:
            self._cache[cache_key] = response
        return response

    def get(self, url: str, **kwargs: Any) -> CachedResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> CachedResponse:
        return self.request("POST", url, **kwargs)

    def payload(
        self,
        dataset_code: str,
        source_code: str,
        response: CachedResponse,
        metadata: dict[str, Any] | None = None,
    ) -> DatasetPayload:
        return DatasetPayload(
            dataset_code=dataset_code,
            source_code=source_code,
            fetched_at=datetime.now(UTC).astimezone(ZoneInfo("Europe/Madrid")),
            source_url=response.source_url,
            content_type=response.content_type,
            body=response.body,
            sha256=hashlib.sha256(response.body).hexdigest(),
            metadata={**response.metadata, **(metadata or {})},
        )
