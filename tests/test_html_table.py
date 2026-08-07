from datetime import datetime, timezone
from pathlib import Path

from civic_metrics.catalog import load_catalog
from civic_metrics.connectors.html_table import HtmlTableConnector
from civic_metrics.domain import DatasetPayload


def test_html_table_skips_title_and_header_rows() -> None:
    catalog = load_catalog(Path("config"))
    dataset = catalog.dataset_by_code["ine_epa_summary"]
    indicators = [
        catalog.indicator_by_code["labour_force"],
        catalog.indicator_by_code["unemployment_rate"],
    ]
    body = b"""
    <html><body><table>
      <tr><th colspan="6">EPA historical series</th></tr>
      <tr><th>Trimestre</th><th>Activos</th><th>Ocupados</th><th>Parados</th>
          <th>Tasa de actividad</th><th>Tasa de paro</th></tr>
      <tr><td>2T 2026</td><td>25.274,3</td><td>22.779,0</td><td>2.495,3</td>
          <td>59,29</td><td>9,87</td></tr>
      <tr><td>1T 2026</td><td>25.001,6</td><td>22.293,0</td><td>2.708,6</td>
          <td>58,86</td><td>10,83</td></tr>
    </table></body></html>
    """
    payload = DatasetPayload(
        dataset_code=dataset.code,
        source_code=dataset.source,
        fetched_at=datetime.now(timezone.utc),
        source_url="https://example.test/epa",
        content_type="text/html",
        body=body,
        sha256="test",
    )
    observations = HtmlTableConnector().extract(dataset, payload, indicators)
    assert {item.indicator_code for item in observations} == {"labour_force", "unemployment_rate"}
    assert {item.period.label for item in observations} == {"2026-Q2"}
    assert str(next(item.value for item in observations if item.indicator_code == "labour_force")) == "25274300.0"
