from datetime import date
from decimal import Decimal
from urllib.parse import urlparse

import httpx
import pytest
from bs4 import BeautifulSoup

from civic_metrics.catalog import DatasetDefinition, ExtractionDefinition, IndicatorDefinition
from civic_metrics.connectors.base import ConnectorContext
from civic_metrics.connectors.html_excel import HtmlExcelConnector
from civic_metrics.domain import ObservationCandidate, Period
from civic_metrics.http import HttpClient
from civic_metrics.settings import Settings


def test_extension_filter_rejects_pdf_when_xls_is_required() -> None:
    extensions = (".xls", ".xlsx")

    assert not HtmlExcelConnector._href_has_allowed_extension(
        "/datos/pdf/empleo/evolparo.pdf", extensions
    )
    assert HtmlExcelConnector._href_has_allowed_extension(
        "/datos/xls/empleo/evolparo.xls", extensions
    )


def test_extension_filter_handles_query_strings_and_case() -> None:
    extensions = (".xls", ".xlsx")

    assert HtmlExcelConnector._href_has_allowed_extension(
        "https://example.test/DATA.XLSX?download=1", extensions
    )
    assert not HtmlExcelConnector._href_has_allowed_extension(
        "https://example.test/data.pdf?format=xls", extensions
    )


def test_named_navigation_ignores_global_navigation_links() -> None:
    soup = BeautifulSoup(
        """
        <a href="/report?changeLanguage=es#top">Pensiones contributivas en vigor</a>
        <a href="/report/valid">Pensiones contributivas en vigor</a>
        """,
        "html.parser",
    )

    class Page:
        source_url = "https://example.test/year"
        body = str(soup)

    class Context:
        class Http:
            def get(self, url):
                return url

        http = Http()

    assert HtmlExcelConnector._get_named_page(
        Page(), "Pensiones contributivas en vigor", Context()
    ) == "https://example.test/report/valid"


def test_named_navigation_accepts_an_alternative_label() -> None:
    soup = BeautifulSoup(
        '<a href="/total-system">Pensiones por CCAA y provincias. Total Sistema</a>',
        "html.parser",
    )

    class Page:
        source_url = "https://example.test/year"
        body = str(soup)

    class Context:
        class Http:
            def get(self, url):
                return url

        http = Http()

    assert HtmlExcelConnector._get_named_page(
        Page(),
        [
            "Pensiones por CCAA y provincias",
            "Pensiones por CCAA y provincias. Total Sistema",
        ],
        Context(),
    ) == "https://example.test/total-system"


def test_auxiliary_navigation_url_is_rejected() -> None:
    assert HtmlExcelConnector._is_auxiliary_navigation_url(
        urlparse("https://example.test/page?changeLanguage=ca#search")
    )
    assert not HtmlExcelConnector._is_auxiliary_navigation_url(
        urlparse("https://example.test/report/valid")
    )


def test_generated_historical_links_remove_current_file_cache() -> None:
    links = [
        (
            "https://example.test/ICONCEPTOS202608.xlsx?CACHEID=current&MOD=AJPERES",
            "Importe nómina por conceptos. Agosto 2026",
            (2026, 8, 1),
        )
    ]

    generated = HtmlExcelConnector._generated_historical_links(links, 3)

    assert [item[0] for item in generated] == [
        links[0][0],
        "https://example.test/ICONCEPTOS202607.xlsx?MOD=AJPERES",
        "https://example.test/ICONCEPTOS202606.xlsx?MOD=AJPERES",
    ]


def test_incomplete_history_is_a_warning_not_a_lookup_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    HtmlExcelConnector._warn_incomplete_history(
        "social_security_affiliation_article",
        50,
        {"social_security_affiliates_avg": {(object(), object())}},
    )

    assert "requested=50" in caplog.text
    assert "Continuing with the available official data" in caplog.text


def test_history_shortfall_returns_available_documents_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class PartialHistoryConnector(HtmlExcelConnector):
        def extract(self, dataset, payload, indicators):
            return [
                ObservationCandidate(
                    indicator_code="demo_indicator",
                    source_code=dataset.source,
                    dataset_code=dataset.code,
                    period=Period(date(2026, 1, 1), date(2026, 1, 31), "2026-01", "monthly"),
                    value=Decimal("1"),
                    unit="people",
                )
            ]

    dataset = DatasetDefinition(
        code="demo_history",
        source="demo_source",
        connector="html_excel",
        endpoint="https://example.test/listing",
        config={"historical_files": True, "extensions": [".xlsx"]},
    )
    indicator = IndicatorDefinition(
        code="demo_indicator",
        name="Demo",
        description="Demo indicator",
        category="demo",
        dataset=dataset.code,
        unit="people",
        frequency="monthly",
        extraction=ExtractionDefinition(kind="excel_label"),
    )

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/listing":
            return httpx.Response(
                200,
                content=b'<a href="/demo.xlsx">Demo workbook</a>',
                request=request,
            )
        return httpx.Response(200, content=b"not used by this test", request=request)

    http = HttpClient(1)
    http._client.close()
    http._client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        documents = PartialHistoryConnector().collect(
            dataset,
            ConnectorContext(Settings(lookback_period=2), http, "monthly"),
            [indicator],
        )
    finally:
        http.close()

    assert len(documents) == 1
    assert len(documents[0][1]) == 1
    assert "requested=2" in caplog.text
