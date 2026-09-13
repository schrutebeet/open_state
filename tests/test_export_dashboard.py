import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from civic_metrics.models import (
    Base,
    Category,
    Indicator,
    Observation,
    Source,
    SourceDataset,
)
from scripts.export_dashboard import REQUIRED_CODES, export_dashboard


def test_dashboard_json_includes_spanish_indicator_text(tmp_path: Path) -> None:
    database = tmp_path / "snapshot.db"
    output = tmp_path / "paisometro-dashboard.json"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        category = Category(code="economy", name="Economy", description="Economic indicators")
        source = Source(
            code="test_source",
            name="Test source",
            base_url="https://example.test",
            institution_type="test",
        )
        dataset = SourceDataset(
            code="test_dataset",
            source=source,
            connector="test",
            endpoint="https://example.test/data",
        )
        session.add_all([category, source, dataset])
        session.flush()

        for code in REQUIRED_CODES | {"gdp_nominal"}:
            indicator = Indicator(
                code=code,
                name=f"English name: {code}",
                description=f"English description: {code}",
                subcategory="test",
                category=category,
                dataset=dataset,
                unit="count",
                frequency="monthly",
            )
            session.add(indicator)
            session.flush()
            session.add(
                Observation(
                    indicator=indicator,
                    source_code=source.code,
                    dataset_code=dataset.code,
                    period_start=date(2026, 8, 1),
                    period_end=date(2026, 8, 31),
                    period_label="2026-08",
                    frequency="monthly",
                    geography="ES",
                    value=Decimal("1"),
                    unit="count",
                    status="published",
                    is_provisional=False,
                    source_url="https://example.test/data.xlsx",
                    retrieved_at=datetime(2026, 9, 1),
                )
            )
        session.commit()

    export_dashboard(database, output, history_limit=1)
    dashboard = json.loads(output.read_text(encoding="utf-8"))
    gdp = dashboard["indicators"]["gdp_nominal"]

    assert gdp["name"] == "English name: gdp_nominal"
    assert gdp["description"] == "English description: gdp_nominal"
    assert gdp["nameEs"] == "PIB nominal"
    assert gdp["descriptionEs"]
