from urllib.parse import urlparse

from bs4 import BeautifulSoup

from civic_metrics.connectors.html_excel import HtmlExcelConnector


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
