from bs4 import BeautifulSoup

from civic_metrics.connectors.html_excel import HtmlExcelConnector


def test_download_link_uses_table_row_context() -> None:
    soup = BeautifulSoup(
        """
        <table><tr><td>Series de pensiones en vigor. Julio 2026</td>
        <td><a href="/opaque/S202607.xlsx">(XLS, 103 KB)</a></td></tr></table>
        """,
        "html.parser",
    )
    anchor = soup.find("a")
    assert anchor is not None
    context = HtmlExcelConnector._link_context(anchor)
    assert "Series de pensiones en vigor" in context
    assert "Julio 2026" in context
