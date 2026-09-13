from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx

from civic_metrics.domain import DatasetPayload


@dataclass(frozen=True)
class CachedResponse:
    body: bytes
    source_url: str
    content_type: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class HttpRequestTiming:
    method: str
    url: str
    elapsed_seconds: float
    status_code: int | None
    error: str | None = None


class HttpClient:
    """Synchronous HTTP client with retries and a per-run request cache.

    Identical GET requests are performed only once during a pipeline run. This is
    what allows one downloaded dataset to feed several indicators without repeating
    calls to the official source.
    """

    RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
    MAX_RETRY_AFTER_SECONDS = 30

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
        self._active_dataset: ContextVar[str | None] = ContextVar(
            f"http_dataset_{id(self)}", default=None
        )
        self._dataset_timings: dict[str, list[HttpRequestTiming]] = defaultdict(list)

    def close(self) -> None:
        self._client.close()

    @contextmanager
    def measure_dataset(self, dataset_code: str) -> Iterator[None]:
        token: Token[str | None] = self._active_dataset.set(dataset_code)
        try:
            yield
        finally:
            self._active_dataset.reset(token)

    def dataset_request_timings(self, dataset_code: str) -> list[HttpRequestTiming]:
        return list(self._dataset_timings.get(dataset_code, []))

    def _record_request_timing(
        self,
        method: str,
        url: str,
        elapsed_seconds: float,
        status_code: int | None,
        error: Exception | None = None,
    ) -> None:
        dataset_code = self._active_dataset.get()
        if dataset_code is None:
            return
        self._dataset_timings[dataset_code].append(
            HttpRequestTiming(
                method=method,
                url=self._public_url(url),
                elapsed_seconds=elapsed_seconds,
                status_code=status_code,
                error=f"{type(error).__name__}: {error}" if error is not None else None,
            )
        )

    @staticmethod
    def _public_url(url: str) -> str:
        parts = urlsplit(url)
        query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in {"access_token", "token"}
        ]
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

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
            request_started = time.perf_counter()
            try:
                response = self._client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                )
                self._record_request_timing(
                    method,
                    str(response.request.url),
                    time.perf_counter() - request_started,
                    response.status_code,
                )
                response.raise_for_status()
                return CachedResponse(
                    body=response.content,
                    source_url=self._public_url(str(response.url)),
                    content_type=response.headers.get(
                        "content-type", "application/octet-stream"
                    ).split(";")[0],
                    metadata={
                        "etag": response.headers.get("etag"),
                        "last_modified": response.headers.get("last-modified"),
                        "status_code": response.status_code,
                    },
                )
            except httpx.HTTPStatusError as exc:
                if (
                    exc.response.status_code not in self.RETRYABLE_STATUS_CODES
                    or attempt == 3
                ):
                    raise
                last_error = exc
                time.sleep(self._retry_delay(attempt, exc.response.headers.get("Retry-After")))
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                self._record_request_timing(
                    method,
                    str(httpx.URL(url, params=params)),
                    time.perf_counter() - request_started,
                    None,
                    exc,
                )
                last_error = exc
                if attempt == 3:
                    raise
                time.sleep(min(2**attempt, 8))
        assert last_error is not None
        raise last_error

    @classmethod
    def _retry_delay(cls, attempt: int, retry_after: str | None) -> float:
        """Use the server's Retry-After hint when valid; otherwise back off exponentially."""
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=UTC)
                    delay = (retry_at - datetime.now(UTC)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    delay = min(2**attempt, 8)
            return min(max(delay, 0), cls.MAX_RETRY_AFTER_SECONDS)
        return min(2**attempt, 8)

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
