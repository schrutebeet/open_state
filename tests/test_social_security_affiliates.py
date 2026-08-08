from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import openpyxl

from civic_metrics.catalog import load_catalog
from civic_metrics.connectors.social_security_affiliates import SocialSecurityAffiliatesConnector
from civic_metrics.domain import DatasetPayload


def test_extracts_totals_for_the_latest_configured_months() -> None:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Tabla_1_5"
    sheet.append(["PERIODO", "Descripcion_Sexo", "SALDOS"])
    sheet.append(["202607", "Mujer", 10.123])
    sheet.append(["202607", "Varón", 12.345])
    sheet.append(["202606", "Mujer", 8])
    sheet.append(["202606", "Varón", 9])
    buffer = BytesIO()
    book.save(buffer)

    catalog = load_catalog(Path("config"))
    dataset = catalog.dataset_by_code["social_security_affiliation_article"]
    indicator = catalog.indicator_by_code["social_security_affiliates_avg"]
    payload = DatasetPayload(
        dataset_code=dataset.code,
        source_code=dataset.source,
        fetched_at=datetime.now(UTC),
        source_url="https://example.test/affiliates.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        body=buffer.getvalue(),
        sha256="0" * 64,
        metadata={"history_periods": 2},
    )

    observations = SocialSecurityAffiliatesConnector().extract(dataset, payload, [indicator])

    assert [(item.period.label, item.value) for item in observations] == [
        ("2026-07", Decimal("22.47")),
        ("2026-06", Decimal("17.00")),
    ]
    assert observations[0].source_series == "1. EDAD!TOTAL SISTEMA"
