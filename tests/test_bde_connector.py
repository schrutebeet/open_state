from datetime import UTC, datetime
from decimal import Decimal

from civic_metrics.catalog import DatasetDefinition, ExtractionDefinition, IndicatorDefinition
from civic_metrics.connectors.bde import BdeSeriesConnector
from civic_metrics.domain import DatasetPayload


def test_bde_series_keeps_full_history_in_chronological_order() -> None:
    dataset = DatasetDefinition(
        code="bde_mortgage_rates",
        source="bde",
        connector="bde_series",
        config={"series": ["DN_1TI2T0002"]},
    )
    indicator = IndicatorDefinition(
        code="mortgage_interest_rate",
        name="Mortgage rate",
        description="Monthly mortgage rate",
        category="economy",
        dataset=dataset.code,
        unit="percent",
        frequency="monthly",
        extraction=ExtractionDefinition(kind="bde_series", series_code="DN_1TI2T0002"),
    )
    payload = DatasetPayload(
        dataset_code=dataset.code,
        source_code="bde",
        fetched_at=datetime.now(UTC),
        source_url="https://app.bde.es/bierest/resources/srdatosapp/listaSeries",
        content_type="application/json",
        body=(
            b'{"serie":"DN_1TI2T0002","fechas":["2024-02-29",'
            b'"2024-04-30","2024-03-31"],"valores":["3.1","3.3","3.2"]}'
        ),
        sha256="test",
        metadata={},
    )

    observations = BdeSeriesConnector().extract(dataset, payload, [indicator])

    assert [(row.period.label, row.value) for row in observations] == [
        ("2024-02", Decimal("3.1")),
        ("2024-03", Decimal("3.2")),
        ("2024-04", Decimal("3.3")),
    ]
