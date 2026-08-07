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
