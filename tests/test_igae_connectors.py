from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from civic_metrics.catalog import load_catalog
from civic_metrics.connectors.igae import (
    IgaeQuarterlyAccountsConnector,
    IgaeStateBudgetExecutionConnector,
)
from civic_metrics.domain import DatasetPayload


FIXTURES = Path(__file__).parent / "fixtures"


def _payload(dataset_code: str, source_code: str, filename: str) -> DatasetPayload:
    body = (FIXTURES / filename).read_bytes()
    return DatasetPayload(
        dataset_code=dataset_code,
        source_code=source_code,
        fetched_at=datetime.now(timezone.utc),
        source_url=f"https://example.test/{filename}",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        body=body,
        sha256=hashlib.sha256(body).hexdigest(),
        metadata={"selected_link_text": "Cuadros junio 2026"},
    )


def test_igae_quarterly_accounts_uses_latest_populated_quarter() -> None:
    catalog = load_catalog(Path("config"))
    dataset = catalog.dataset_by_code["igae_quarterly_non_financial_accounts"]
    indicators = [item for item in catalog.indicators if item.dataset == dataset.code]
    results = IgaeQuarterlyAccountsConnector().extract(
        dataset,
        _payload(dataset.code, dataset.source, "T_AAPP.xlsx"),
        indicators,
    )
    values = {item.indicator_code: item for item in results}
    assert values["general_government_revenue"].value == 172627
    assert values["general_government_expenditure"].value == 179057
    assert values["general_government_balance"].value == -6430
    assert {item.period.label for item in results} == {"2026-Q1"}


def test_igae_budget_execution_converts_thousands_to_millions() -> None:
    catalog = load_catalog(Path("config"))
    dataset = catalog.dataset_by_code["igae_state_budget_execution"]
    indicators = [item for item in catalog.indicators if item.dataset == dataset.code]
    results = IgaeStateBudgetExecutionConnector().extract(
        dataset,
        _payload(dataset.code, dataset.source, "EXTRACTO_JUNIO_2026.xlsx"),
        indicators,
    )
    values = {item.indicator_code: item.value for item in results}
    assert values == {
        "state_budget_revenue_recognised": Decimal("224731.010"),
        "state_budget_revenue_forecast": Decimal("192544.168"),
        "state_budget_expenditure_recognised": Decimal("187829.911"),
        "state_budget_final_appropriation": Decimal("458104.069"),
        "state_interest_expenditure": Decimal("13204.985"),
    }
    assert {item.period.label for item in results} == {"2026-06"}
