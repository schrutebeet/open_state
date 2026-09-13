from civic_metrics.catalog import DatasetDefinition, ExtractionDefinition, IndicatorDefinition
from civic_metrics.connectors.base import Connector
from civic_metrics.http import HttpClient
from civic_metrics.orchestrator import PipelineOrchestrator
from civic_metrics.settings import Settings


def test_transient_configured_source_is_reported_as_stale(monkeypatch) -> None:
    dataset = DatasetDefinition(
        code="temporarily_unavailable",
        source="official_source",
        connector="temporary_failure",
        endpoint="https://example.test/data",
        config={"allow_stale_on_transient_error": True},
    )
    indicator = IndicatorDefinition(
        code="demo_indicator",
        name="Demo",
        description="Demo",
        category="demo",
        dataset=dataset.code,
        unit="people",
        frequency="monthly",
        extraction=ExtractionDefinition(kind="excel_label"),
    )

    class TemporaryFailureConnector(Connector):
        def fetch(self, dataset, context):
            context.http.get(dataset.endpoint)
            raise AssertionError("HTTP client should have raised first")

        def extract(self, dataset, payload, indicators):
            return []

    from civic_metrics import orchestrator

    monkeypatch.setitem(orchestrator.CONNECTORS, "temporary_failure", TemporaryFailureConnector)

    import httpx

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    http = HttpClient(1)
    http._client.close()
    http._client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr("civic_metrics.http.time.sleep", lambda _: None)
    instance = object.__new__(PipelineOrchestrator)
    instance.settings = Settings(lookback_period=12)
    try:
        result = instance._run_dataset(1, dataset, [indicator], http)
    finally:
        http.close()

    assert result.status == "stale"
    assert result.fetched_observations == 0
    assert "temporary HTTP 503" in result.warnings[0]
