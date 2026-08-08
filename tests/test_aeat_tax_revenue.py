from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import openpyxl

from civic_metrics.catalog import load_catalog
from civic_metrics.connectors.aeat_tax_revenue import AeatTaxRevenueConnector
from civic_metrics.domain import DatasetPayload


def test_extracts_latest_populated_monthly_net_tax_revenue() -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Ingresos tributarios"
    sheet.append(["year", "month"])
    previous = [None] * 108
    previous[0], previous[1], previous[4], previous[6] = 2026, 5, -7_000_000, 10_000_000
    previous[29], previous[65], previous[107] = 4_000_000, 300_000, 3_000_000
    latest = [None] * 108
    latest[0], latest[1], latest[4], latest[6] = 2026, 6, -8_544_316, 13_934_443
    latest[29], latest[65], latest[107] = 6_022_926, 444_050, 4_381_496
    future_blank = [None] * 108
    future_blank[0], future_blank[1] = 2026, 7
    for row in [previous, latest, future_blank]:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)

    catalog = load_catalog(Path("config"))
    dataset = catalog.dataset_by_code["aeat_monthly_tax_revenue"]
    codes = [
        "tax_revenue_total",
        "tax_revenue_irpf",
        "tax_revenue_vat",
        "tax_revenue_corporate",
        "tax_refunds",
    ]
    indicators = [catalog.indicator_by_code[code] for code in codes]
    payload = DatasetPayload(
        dataset_code=dataset.code,
        source_code=dataset.source,
        fetched_at=datetime.now(UTC),
        source_url="https://example.test/series.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        body=buffer.getvalue(),
        sha256="0" * 64,
    )

    observations = AeatTaxRevenueConnector().extract(dataset, payload, indicators)

    assert [(item.indicator_code, item.value) for item in observations] == [
        ("tax_revenue_total", Decimal("13.934443")),
        ("tax_revenue_irpf", Decimal("6.022926")),
        ("tax_revenue_vat", Decimal("4.381496")),
        ("tax_revenue_corporate", Decimal("0.444050")),
        ("tax_refunds", Decimal("8.544316")),
    ]
    assert {item.period.label for item in observations} == {"2026-06"}
    assert {item.source_series for item in observations} == {
        "Ingresos tributarios!R3C7",
        "Ingresos tributarios!R3C30",
        "Ingresos tributarios!R3C66",
        "Ingresos tributarios!R3C108",
        "Ingresos tributarios!R3C5",
    }
