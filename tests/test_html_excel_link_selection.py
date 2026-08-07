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
