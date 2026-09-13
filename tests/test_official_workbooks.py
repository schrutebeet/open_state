from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import httpx

from civic_metrics.catalog import load_catalog
from civic_metrics.connectors.base import ConnectorContext
from civic_metrics.connectors.sepe import SepeRegisteredUnemploymentConnector
from civic_metrics.connectors.social_security_pensions import SocialSecurityPensionsConnector
from civic_metrics.domain import DatasetPayload
from civic_metrics.http import HttpClient
from civic_metrics.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parents[1]


def payload(dataset: str, source: str, filename: str) -> DatasetPayload:
    body = (FIXTURES / filename).read_bytes()
    return DatasetPayload(
        dataset_code=dataset,
        source_code=source,
        fetched_at=datetime.now(timezone.utc),
        source_url=f"https://example.test/{filename}",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        body=body,
        sha256=sha256(body).hexdigest(),
        metadata={"selected_link_text": filename},
    )


def definitions(dataset_code: str):
    catalog = load_catalog(ROOT / "config")
    dataset = catalog.dataset_by_code[dataset_code]
    indicators = [item for item in catalog.indicators if item.dataset == dataset_code]
    return dataset, indicators


def test_social_security_series_workbook() -> None:
    dataset, indicators = definitions("social_security_pension_series")
    results = SocialSecurityPensionsConnector().extract(
        dataset, payload(dataset.code, dataset.source, "S202607.xlsx"), indicators
    )
    values = {item.indicator_code: item.value for item in results}
    assert {item.period.label for item in results} == {
        "2026-01",
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
        "2026-06",
        "2026-07",
        "2026-08",
    }
    assert values["pension_count"] == 10517634
    assert values["average_pension"].quantize(__import__('decimal').Decimal('0.01')) == __import__('decimal').Decimal('1372.16')
    assert values["average_retirement_pension"].quantize(__import__('decimal').Decimal('0.01')) == __import__('decimal').Decimal('1573.65')


def test_social_security_payroll_workbook() -> None:
    dataset, indicators = definitions("social_security_pension_payroll")
    results = SocialSecurityPensionsConnector().extract(
        dataset, payload(dataset.code, dataset.source, "ICONCEPTOS202607.xlsx"), indicators
    )
    assert len(results) == 1
    assert results[0].value.quantize(__import__('decimal').Decimal('0.001')) == __import__('decimal').Decimal('14431.901')


def test_social_security_pensioners_workbook() -> None:
    dataset, indicators = definitions("social_security_pensioners")
    results = SocialSecurityPensionsConnector().extract(
        dataset, payload(dataset.code, dataset.source, "PTAS202607.xlsx"), indicators
    )
    assert len(results) == 1
    assert results[0].value == 9511126


def test_sepe_legacy_workbook() -> None:
    dataset, indicators = definitions("sepe_registered_unemployment")
    source_payload = payload(dataset.code, dataset.source, "evolparo.xls")
    source_payload = DatasetPayload(
        **{**source_payload.__dict__, "content_type": "application/vnd.ms-excel"}
    )
    results = SepeRegisteredUnemploymentConnector().extract(dataset, source_payload, indicators)
    assert len(results) == 12
    latest = max(results, key=lambda item: item.period.end)
    assert latest.value == 2311499
    assert latest.period.label == "2026-07"


def test_sepe_fetch_falls_back_to_official_workbook_when_listing_is_unavailable(
    monkeypatch,
) -> None:
    dataset, indicators = definitions("sepe_registered_unemployment")
    fallback_url = dataset.config["fallback_url"]
    workbook = (FIXTURES / "evolparo.xls").read_bytes()
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == dataset.endpoint:
            return httpx.Response(503, request=request)
        if str(request.url) == fallback_url:
            return httpx.Response(
                200,
                headers={"Content-Type": "application/vnd.ms-excel"},
                content=workbook,
                request=request,
            )
        return httpx.Response(404, request=request)

    http = HttpClient(1)
    http._client.close()
    http._client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr("civic_metrics.http.time.sleep", lambda _: None)
    context = ConnectorContext(Settings(lookback_period=12), http)
    try:
        connector = SepeRegisteredUnemploymentConnector()
        document = connector.fetch(dataset, context)
        observations = connector.extract(dataset, document, indicators)
    finally:
        http.close()

    assert calls.count(dataset.endpoint) == 4
    assert calls[-1] == fallback_url
    assert document.source_url == fallback_url
    assert len(observations) == 12
    assert max(observations, key=lambda item: item.period.end).period.label == "2026-07"
