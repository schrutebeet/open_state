from datetime import UTC, datetime
from decimal import Decimal

from civic_metrics.catalog import DatasetDefinition, ExtractionDefinition, IndicatorDefinition
from civic_metrics.connectors.world_bank import WorldBankIndicatorConnector
from civic_metrics.domain import DatasetPayload


def test_world_bank_extraction_sorts_non_null_values_and_applies_lookback() -> None:
    dataset = DatasetDefinition(
        code="world_bank_pm25_exposure",
        source="world_bank",
        connector="world_bank_indicator",
        config={"indicator_id": "EN.ATM.PM25.MC.M3"},
    )
    indicator = IndicatorDefinition(
        code="pm25_exposure",
        name="PM2.5 exposure",
        description="Annual exposure",
        category="environment",
        dataset=dataset.code,
        unit="micrograms_per_cubic_metre",
        frequency="annual",
        extraction=ExtractionDefinition(kind="world_bank_indicator"),
    )
    payload = DatasetPayload(
        dataset_code=dataset.code,
        source_code="world_bank",
        fetched_at=datetime.now(UTC),
        source_url="https://api.worldbank.org/v2/country/ESP/indicator/EN.ATM.PM25.MC.M3",
        content_type="application/json",
        body=(
            b'[{"sourceid":"2","lastupdated":"2026-07-13"},['
            b'{"date":"2022","value":null,"indicator":{"id":"EN.ATM.PM25.MC.M3"}},'
            b'{"date":"2021","value":12.5,"indicator":{"id":"EN.ATM.PM25.MC.M3"}},'
            b'{"date":"2023","value":10.2,"indicator":{"id":"EN.ATM.PM25.MC.M3"}},'
            b'{"date":"2024","value":9.8,"indicator":{"id":"EN.ATM.PM25.MC.M3"}}]]'
        ),
        sha256="test",
        metadata={"lookback_periods": 2},
    )

    observations = WorldBankIndicatorConnector().extract(dataset, payload, [indicator])

    assert [(row.period.label, row.value) for row in observations] == [
        ("2023", Decimal("10.2")),
        ("2024", Decimal("9.8")),
    ]
