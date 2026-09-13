import httpx
import pytest

from civic_metrics.http import CachedResponse, HttpClient


def test_identical_requests_are_cached_per_run() -> None:
    client = HttpClient(1)
    calls = 0

    def fake_request(method, url, params=None, json_body=None, headers=None):
        nonlocal calls
        calls += 1
        return CachedResponse(b"{}", url, "application/json", {})

    client._request = fake_request  # type: ignore[method-assign]
    try:
        client.get("https://example.test/data", params={"x": 1})
        client.get("https://example.test/data", params={"x": 1})
    finally:
        client.close()
    assert calls == 1


def test_transient_server_error_is_retried_and_respects_retry_after(monkeypatch) -> None:
    calls = 0
    delays: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(200, content=b"ok")

    client = HttpClient(1)
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr("civic_metrics.http.time.sleep", delays.append)
    try:
        response = client.get("https://example.test/data")
    finally:
        client.close()

    assert response.body == b"ok"
    assert calls == 2
    assert delays == [0]


def test_non_transient_http_error_is_not_retried(monkeypatch) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    client = HttpClient(1)
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr("civic_metrics.http.time.sleep", lambda _: None)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            client.get("https://example.test/missing")
    finally:
        client.close()

    assert calls == 1


def test_request_timings_are_scoped_to_dataset_and_include_retries(monkeypatch) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(200, content=b"ok")

    client = HttpClient(1)
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr("civic_metrics.http.time.sleep", lambda _: None)
    try:
        with client.measure_dataset("demo_dataset"):
            client.get("https://example.test/data")
        client.get("https://example.test/uncounted")
    finally:
        client.close()

    timings = client.dataset_request_timings("demo_dataset")
    assert len(timings) == 2
    assert [timing.status_code for timing in timings] == [503, 200]
    assert all(timing.method == "GET" for timing in timings)
    assert all(timing.url == "https://example.test/data" for timing in timings)
    assert all(timing.elapsed_seconds >= 0 for timing in timings)
    assert client.dataset_request_timings("other_dataset") == []
