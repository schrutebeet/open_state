from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import openpyxl

from civic_metrics.catalog import load_catalog
from civic_metrics.connectors.social_security_minimum_supplements import (
    SocialSecurityMinimumSupplementsConnector,
)
from civic_metrics.domain import DatasetPayload


def test_extracts_national_total_number_instead_of_a_percentage() -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Min_número_%"
    sheet.append(["PENSIONES CON COMPLEMENTO A MÍNIMOS"])
    sheet.append(["AMBOS SEXOS"])
    sheet.append(["RÉGIMEN", "TOTAL PENSIONES", None])
    sheet.append([None, "Número", "Porcentaje"])
    sheet.append(["Total", 2_116_033, 0.20118906970902392])
    buffer = BytesIO()
    workbook.save(buffer)

    catalog = load_catalog(Path("config"))
    dataset = catalog.dataset_by_code["social_security_minimum_supplements"]
    indicator = catalog.indicator_by_code["pensions_with_minimum_supplement"]
    payload = DatasetPayload(
        dataset_code=dataset.code,
        source_code=dataset.source,
        fetched_at=datetime.now(UTC),
        source_url="https://example.test/MIN202607.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        body=buffer.getvalue(),
        sha256="0" * 64,
    )

    observations = SocialSecurityMinimumSupplementsConnector().extract(
        dataset,
        payload,
        [indicator],
    )

    assert len(observations) == 1
    assert observations[0].value == 2_116_033
    assert observations[0].unit == "people"
    assert observations[0].period.label == "2026-07"
