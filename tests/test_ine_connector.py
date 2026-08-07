from datetime import datetime, timezone
from pathlib import Path

from civic_metrics.catalog import load_catalog
from civic_metrics.connectors.ine import IneTableConnector
from civic_metrics.domain import DatasetPayload


def test_one_ine_payload_feeds_multiple_indicators() -> None:
    catalog = load_catalog(Path("config"))
    dataset = catalog.dataset_by_code["ine_gdp_current_prices"]
    indicators = [
        catalog.indicator_by_code["gdp_nominal"],
        catalog.indicator_by_code["household_consumption_nominal"],
    ]
    body = Path("tests/fixtures/ine_sample.json").read_bytes()
    payload = DatasetPayload(
        dataset_code=dataset.code,
        source_code=dataset.source,
        fetched_at=datetime.now(timezone.utc),
        source_url="https://servicios.ine.es/test",
        content_type="application/json",
        body=body,
        sha256="test",
    )
    observations = IneTableConnector().extract(dataset, payload, indicators)
    assert {item.indicator_code for item in observations} == {
        "gdp_nominal",
        "household_consumption_nominal",
    }
    assert len([item for item in observations if item.indicator_code == "gdp_nominal"]) == 2
